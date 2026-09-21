"""Bounded source onboarding trials and human-reviewable previews."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import re
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit
from uuid import uuid4

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.import_json import SOURCE_DEFAULTS
from backend.app.db.models import AgentRun, ExecutionEvent, JobSource, SourcePreview
from backend.app.db.repository import ensure_source
from backend.app.sources.discovery import (
    RateLimitedError,
    SafeSourceClient,
    ServerError,
    SourceBlockedError,
    SourceContract,
)
from backend.app.sources.extractors import extract_job_page_record
from backend.app.sources.lifecycle import InvalidSourceTransition, transition_source
from backend.app.trace_safety import sanitize_error_text, sanitize_job_url
from backend.app.workflow.sources import (
    APPROVED_HANDLERS,
    SourceExtractionHandler,
    resolve_source_handler,
)
from backend.app.sources.solita_discovery import SOLITA_CONTRACT
from backend.app.sources.vestas_contract import VESTAS_DRAFT_CONTRACT
from backend.app.sources.trifork_discovery import TRIFORK_CONTRACT
from backend.app.sources.energinet_contract import ENERGINET_DRAFT_CONTRACT


PREVIEW_TTL = timedelta(hours=24)
PREVIEW_SAMPLE_SIZE = 5
GENERIC_PREVIEW_MAX_PAGES = 3
GENERIC_PREVIEW_MAX_JOBS = 20
GENERIC_PREVIEW_CRAWL_DELAY = 5
_JOB_PATH_PARTS = frozenset({"job", "jobs", "career", "careers", "position", "positions", "vacancy", "vacancies", "opening", "openings", "role", "roles"})
_ATS_HOST_MARKERS = ("homerun", "recruitee", "greenhouse", "lever.co", "workday", "smartrecruiters", "teamtailor", "bamboohr", "personio", "jobylon")


class _PreviewLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(" ".join(self._text).split())))
            self._href = None
            self._text = []


class _GenericPreviewHandler:
    """Preview-only same-domain link discovery; never registered for execution."""

    execution_ready = False

    def __init__(self, url: str, source_id: str | None = None) -> None:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path or "/"
        self.source_id = source_id or f"preview_{hashlib.sha256(f'{host}{path}'.encode()).hexdigest()[:12]}"
        self.display_name = host
        self.host = host
        self.careers_url = url
        self.contract: SourceContract | None = None
        self.warnings: list[str] = []
        self.external_candidates: list[dict[str, str]] = []

    def can_handle(self, url: str) -> bool:
        return (urlsplit(url).hostname or "").lower() == self.host

    def extract_record(self, url: str, *, html_content: str | None = None, transport: Any | None = None) -> dict[str, Any]:
        if html_content is None:
            raise AssertionError("generic preview handler only uses bounded discovery")
        return extract_job_page_record(html_content, source=self.source_id, url=url)

    def discover_jobs(self, careers_url: str, *, transport: Any | None = None) -> list[dict[str, Any]]:
        if transport is None:
            from backend.app.sources.phase1_trial import _RequestsTransport
            transport = _RequestsTransport()
        landing_path = urlsplit(careers_url).path or "/"
        # SafeSourceClient fetches and enforces the live robots.txt of this host
        # before the landing page is requested; there is no embedded policy.
        sleep = getattr(transport, "sleep", None)
        client = SafeSourceClient(
            SourceContract(
                source_id=self.source_id,
                careers_url=careers_url,
                allowed_domains=frozenset({self.host}),
                allowed_paths=(landing_path,),
                crawl_delay_seconds=GENERIC_PREVIEW_CRAWL_DELAY,
                max_pages=GENERIC_PREVIEW_MAX_PAGES,
                max_jobs=GENERIC_PREVIEW_MAX_JOBS,
            ),
            transport,
            **({"sleep": sleep} if callable(sleep) else {}),
        )
        response = client.get(careers_url)
        html = str(getattr(response, "text", ""))
        parser = _PreviewLinkParser()
        parser.feed(html)
        parser.close()
        self.warnings = []
        self.external_candidates = []
        candidates: list[tuple[str, str]] = []
        external_ats_hosts: set[str] = set()
        seen: set[str] = set()
        for href, title in parser.links:
            candidate = urljoin(careers_url, href)
            parsed = urlsplit(candidate)
            hostname = (parsed.hostname or "").lower()
            if parsed.scheme != "https" or not title:
                continue
            if hostname != self.host:
                if any(marker in hostname for marker in _ATS_HOST_MARKERS):
                    external_ats_hosts.add(hostname)
                    if len(self.external_candidates) < GENERIC_PREVIEW_MAX_JOBS:
                        job_title = re.split(r"\s+Type\s+", title, maxsplit=1)[0].strip()
                        if job_title and job_title.casefold() not in {"view jobs", "open positions", "apply", "jobs"}:
                            self.external_candidates.append({
                                "title": job_title,
                                "host": hostname,
                                "url": sanitize_job_url(candidate) or "",
                            })
                continue
            canonical = urlunsplit(("https", hostname, parsed.path or "/", parsed.query, ""))
            if canonical in seen or canonical == careers_url or not _job_detail_path(parsed.path):
                continue
            seen.add(canonical)
            candidates.append((canonical, title))
            if len(candidates) >= GENERIC_PREVIEW_MAX_JOBS:
                break
        if external_ats_hosts:
            self.warnings.append("EXTERNAL_ATS_NOT_APPROVED: " + ", ".join(sorted(external_ats_hosts)))

        paths = {landing_path, *(urlsplit(url).path.rsplit("/", 1)[0] + "/" for url, _ in candidates)}
        client.contract = replace(client.contract, allowed_paths=tuple(sorted(paths)))
        records: list[dict[str, Any]] = []
        for url, link_title in candidates:
            if client.pages_fetched >= GENERIC_PREVIEW_MAX_PAGES:
                self.warnings.append("PREVIEW_PAGE_LIMIT: remaining candidate links were not verified")
                break
            try:
                detail = client.get(url)
            except (SourceBlockedError, RateLimitedError, ServerError, requests.RequestException) as error:
                self.warnings.append(f"DETAIL_NOT_VERIFIED: {sanitize_job_url(url)} ({type(error).__name__})")
                continue
            if getattr(detail, "status_code", 200) != 200:
                self.warnings.append(f"DETAIL_NOT_VERIFIED: {sanitize_job_url(url)} (HTTP {detail.status_code})")
                continue
            record = self.extract_record(url, html_content=str(getattr(detail, "text", "")))
            posting = _job_posting(record.get("json_ld", []))
            if posting is not None:
                record["title"] = str(posting.get("title") or record.get("title") or "")
                record["description"] = str(posting.get("description") or record.get("description") or "")
            if posting is None or not str(record.get("title") or "").strip() or len(str(record.get("description") or "").strip()) < 80:
                self.warnings.append(f"JOB_POSTING_NOT_VERIFIED: {sanitize_job_url(url)}")
                continue
            valid_through = _iso_date(posting.get("validThrough"))
            date_posted = _iso_date(posting.get("datePosted"))
            today = date.today()
            if valid_through is not None and valid_through < today:
                self.warnings.append(f"JOB_POSTING_EXPIRED: {sanitize_job_url(url)}")
                continue
            if date_posted is not None and date_posted > today:
                self.warnings.append(f"JOB_POSTING_FUTURE_DATED: {sanitize_job_url(url)}")
                continue
            record["status"] = "unknown"  # Structured data alone does not prove applications are open.
            record["date_posted"] = date_posted.isoformat() if date_posted else None
            record["valid_through"] = valid_through.isoformat() if valid_through else None
            record["country"] = _posting_countries(posting)
            record["title"] = str(record["title"]).strip() or link_title
            records.append(record)
        self.contract = SourceContract(
            source_id=self.source_id,
            careers_url=careers_url,
            allowed_domains=frozenset({self.host}),
            allowed_paths=tuple(paths),
            crawl_delay_seconds=GENERIC_PREVIEW_CRAWL_DELAY,
            max_pages=GENERIC_PREVIEW_MAX_PAGES,
            max_jobs=GENERIC_PREVIEW_MAX_JOBS,
        )
        return records


def _job_detail_path(path: str) -> bool:
    parts = [part.lower() for part in path.strip("/").split("/") if part]
    if len(parts) < 2 or any(part in {"apply", "application", "preapply"} for part in parts):
        return False
    return any(part in _JOB_PATH_PARTS for part in parts[:-1]) and parts[-1] not in _JOB_PATH_PARTS


def _job_posting(items: Any) -> dict[str, Any] | None:
    if isinstance(items, list):
        for item in items:
            found = _job_posting(item)
            if found is not None:
                return found
    elif isinstance(items, dict):
        types = items.get("@type")
        if types == "JobPosting" or isinstance(types, list) and "JobPosting" in types:
            return items
        return _job_posting(items.get("@graph"))
    return None


def _iso_date(value: Any) -> date | None:
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _posting_countries(posting: dict[str, Any]) -> list[str]:
    locations = posting.get("jobLocation")
    if not isinstance(locations, list):
        locations = [locations]
    countries: set[str] = set()
    for location in locations:
        address = location.get("address") if isinstance(location, dict) else None
        country = address.get("addressCountry") if isinstance(address, dict) else None
        if isinstance(country, dict):
            country = country.get("name")
        if isinstance(country, str) and country.strip():
            countries.add(country.strip())
    return sorted(countries)


@dataclass(frozen=True)
class OnboardingFailure:
    code: str
    message: str
    retryable: bool = False
    user_fixable: bool = True
    safety_blocked: bool = False
    source_health_related: bool = False


class OnboardingError(RuntimeError):
    def __init__(self, failure: OnboardingFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure
        self.code = failure.code


class OnboardingTrialService:
    """Run one bounded trial without activating or persisting discovered jobs."""

    def __init__(
        self,
        session: Session,
        *,
        transport: Any | None = None,
        handlers: list[SourceExtractionHandler] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.transport = transport
        self.handlers = handlers or APPROVED_HANDLERS
        self.now = now or (lambda: datetime.now(timezone.utc))

    def run(
        self,
        submitted_url: str,
        *,
        company_name: str | None = None,
        company_domain: str | None = None,
        country: str = "Denmark",
        source_id: str | None = None,
    ) -> SourcePreview:
        canonical_url = self._validate_url(submitted_url)
        handler = self._resolve_handler(canonical_url, source_id=source_id)
        source = self._prepare_source(
            handler,
            canonical_url,
            company_name=company_name,
            company_domain=company_domain,
            country=country,
            source_id=source_id,
        )
        run = self._start_trace(source.source_id)
        try:
            self._event(run, "onboarding_validation", source.source_id)
            self._event(run, "onboarding_robots_check", source.source_id)
            records = self._discover(handler, canonical_url)
            self._event(run, "onboarding_discovery", source.source_id, metadata={"jobs_discovered": len(records)})
            validated, rejected, warnings = self._validate_records(records, handler, source)
            warnings.extend(getattr(handler, "warnings", ()))
            if not records:
                warnings.append("NO_JOBS_DISCOVERED: trial discovery found 0 jobs on the landing page")
            elif not validated:
                warnings.append("JOB_VALIDATION_FAILED: none of the discovered jobs passed validation")
            self._event(run, "onboarding_extraction", source.source_id, metadata={"jobs_verified": len(validated)})
            preview = self._persist_preview(source, canonical_url, handler, validated, rejected, warnings)
            self._event(run, "onboarding_preview_created", source.source_id, metadata={"preview_version": preview.version})
            self._complete_trace(run, "SUCCESS")
            self.session.commit()
            return preview
        except OnboardingError as error:
            source.readiness_status = "BLOCKED"
            self._event(
                run,
                "onboarding_failed",
                source.source_id,
                status="FAILURE",
                error_code=error.code,
                error_message=error.failure.message,
            )
            self._complete_trace(run, "FAILED", error.failure.message)
            self.session.commit()
            raise
        except Exception as error:
            failure = self._classify_exception(error)
            source.readiness_status = "BLOCKED"
            self._event(
                run,
                "onboarding_failed",
                source.source_id,
                status="FAILURE",
                error_code=failure.code,
                error_message=failure.message,
            )
            self._complete_trace(run, "FAILED", failure.message)
            self.session.commit()
            raise OnboardingError(failure) from error

    @staticmethod
    def _validate_url(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise OnboardingError(OnboardingFailure("INVALID_URL", "A source URL is required."))
        raw = value.strip()
        parsed = urlsplit(raw)
        if parsed.scheme.lower() != "https":
            raise OnboardingError(OnboardingFailure("UNSUPPORTED_SCHEME", "Only HTTPS source URLs are supported."))
        if parsed.username or parsed.password:
            raise OnboardingError(OnboardingFailure("INVALID_URL", "Source URLs cannot contain credentials."))
        try:
            port = parsed.port
        except ValueError as error:
            raise OnboardingError(OnboardingFailure("INVALID_URL", "Source URL has an invalid port.")) from error
        if port not in (None, 443) or not parsed.hostname:
            raise OnboardingError(OnboardingFailure("INVALID_URL", "Source URL must contain a public HTTPS host."))
        hostname = parsed.hostname.rstrip(".").lower()
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if hostname in {"localhost", "localhost.localdomain"} or (address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved)):
            raise OnboardingError(OnboardingFailure("INVALID_URL", "Private, loopback, and internal hosts are not allowed."))
        return urlunsplit(("https", hostname, parsed.path or "/", parsed.query, ""))

    def _resolve_handler(self, url: str, *, source_id: str | None = None) -> SourceExtractionHandler:
        handler = resolve_source_handler(url)
        if handler is not None:
            return handler
        # `energinet_dk` is deliberately NOT resolved to a fetch-capable handler:
        # its only listing path is disallowed by the live candidate.hr-manager.net
        # robots.txt (see backend/app/sources/energinet_contract.py). Such URLs
        # fall through to the preview-only generic handler, which enforces the
        # live robots.txt of whatever host it is given and can never execute.
        return _GenericPreviewHandler(url, source_id=source_id)

    @staticmethod
    def _handler_contract(handler: SourceExtractionHandler, url: str) -> SourceContract:
        from dataclasses import replace
        contract = getattr(handler, "contract", None) or _contracts().get(handler.source_id)
        if contract is not None:
            if contract.approval_status == "BLOCKED":
                # Fail closed: a BLOCKED source must not be previewed or fetched.
                # The preview path must never self-authorize a blocked contract.
                raise OnboardingError(OnboardingFailure(
                    "POLICY_UNRESOLVED",
                    f"source {contract.source_id} is BLOCKED and must not be fetched",
                    safety_blocked=True,
                ))
            if not contract.enabled or contract.approval_status != "APPROVED":
                return replace(contract, enabled=True, approval_status="APPROVED")
            return contract
        parsed = urlsplit(url)
        return SourceContract(
            source_id=handler.source_id,
            careers_url=url,
            allowed_domains=frozenset({(parsed.hostname or "").lower()}),
            allowed_paths=(parsed.path or "/",),
            crawl_delay_seconds=GENERIC_PREVIEW_CRAWL_DELAY,
            max_pages=GENERIC_PREVIEW_MAX_PAGES,
            max_jobs=GENERIC_PREVIEW_MAX_JOBS,
            enabled=True,
            approval_status="APPROVED",
        )

    def _prepare_source(
        self,
        handler: SourceExtractionHandler,
        canonical_url: str,
        *,
        company_name: str | None,
        company_domain: str | None,
        country: str,
        source_id: str | None = None,
    ) -> JobSource:
        source_key = source_id or handler.source_id
        defaults = dict(SOURCE_DEFAULTS.get(source_key, {}))
        contract = self._handler_contract(handler, canonical_url)
        defaults.setdefault("display_name", handler.display_name)
        defaults.setdefault("official_jobs_url", contract.careers_url)
        defaults.setdefault("allowed_domains", list(contract.allowed_domains))
        defaults.setdefault("crawl_delay_seconds", int(contract.crawl_delay_seconds))
        defaults.setdefault("enabled", False)
        defaults.setdefault("company_name", handler.display_name)
        defaults.setdefault("company_domain", next(iter(contract.allowed_domains)))
        if company_name is not None:
            defaults["company_name"] = company_name
        if company_domain is not None:
            defaults["company_domain"] = company_domain
        defaults.setdefault("country", country)
        source = self.session.scalar(
            select(JobSource).where(
                (JobSource.source_id == source_key) | (JobSource.canonical_url == canonical_url)
            )
        )
        if source is None:
            source = ensure_source(self.session, source_id=source_key, **defaults)
            self.session.flush()
        if source.lifecycle_status == "ACTIVE":
            raise OnboardingError(OnboardingFailure("REVALIDATION_REQUIRED", "Active sources require an explicit revalidation transition before onboarding.", user_fixable=True))
        if source.lifecycle_status not in {"DRAFT", "ONBOARDING", "PREVIEW_READY", "REVALIDATION_REQUIRED", "DISABLED"}:
            raise OnboardingError(OnboardingFailure("INVALID_STATE_TRANSITION", "Source is not eligible for onboarding."))
        if source.lifecycle_status != "ONBOARDING":
            try:
                transition_source(source, "ONBOARDING")
            except InvalidSourceTransition as error:
                raise OnboardingError(OnboardingFailure("INVALID_STATE_TRANSITION", str(error))) from error
        source.canonical_url = canonical_url
        source.enabled = False
        source.boundary_fingerprint = _boundary_fingerprint(contract)
        source.readiness_status = "UNKNOWN"
        self.session.flush()
        return source

    def _discover(self, handler: SourceExtractionHandler, url: str) -> list[dict[str, Any]]:
        try:
            records = handler.discover_jobs(url, transport=self.transport)
        except Exception as error:
            failure = self._classify_exception(error)
            raise OnboardingError(failure) from error
        if not isinstance(records, list):
            raise self._failure("INVALID_EXTRACTION", "Source handler returned an invalid discovery result.")
        return records

    def _validate_records(
        self,
        records: list[dict[str, Any]],
        handler: SourceExtractionHandler,
        source: JobSource,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
        valid: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        warnings: list[str] = []
        seen_urls: set[str] = set()
        seen_ids: set[str] = set()
        contract = self._handler_contract(handler, source.canonical_url or source.official_jobs_url)
        for record in records[: contract.max_jobs]:
            if not isinstance(record, dict):
                rejected.append({"code": "INVALID_EXTRACTION", "detail": "record is not an object"})
                continue
            url = record.get("url")
            title = str(record.get("title") or "").strip()
            if not isinstance(url, str) or not url.startswith("https://"):
                rejected.append({"code": "JOB_VALIDATION_FAILED", "detail": "job URL is not HTTPS"})
                continue
            if not contract.allows(url) or not handler.can_handle(url):
                rejected.append({"code": "PATH_NOT_ALLOWED", "detail": sanitize_job_url(url) or "unknown"})
                continue
            external_id = str(record.get("external_job_id") or "")
            if not title:
                rejected.append({"code": "JOB_VALIDATION_FAILED", "detail": "title is missing"})
                continue
            if url in seen_urls or (external_id and external_id in seen_ids):
                rejected.append({"code": "JOB_VALIDATION_FAILED", "detail": "duplicate job"})
                continue
            seen_urls.add(url)
            if external_id:
                seen_ids.add(external_id)
            if not record.get("description"):
                warnings.append(f"description missing for {sanitize_job_url(url)}")
            if not record.get("country") and not record.get("location"):
                warnings.append(f"location unknown for {sanitize_job_url(url)}")
            valid.append(_preview_record(record))
        return valid, rejected, warnings

    def _persist_preview(
        self,
        source: JobSource,
        canonical_url: str,
        handler: SourceExtractionHandler,
        valid: list[dict[str, Any]],
        rejected: list[dict[str, str]],
        warnings: list[str],
    ) -> SourcePreview:
        version = (self.session.scalar(select(SourcePreview.version).where(SourcePreview.source_id == source.id).order_by(SourcePreview.version.desc())) or 0) + 1
        execution_ready = bool(getattr(handler, "execution_ready", True))
        readiness = "HIGH" if execution_ready and not warnings and not rejected and valid else ("MEDIUM" if execution_ready and valid else "LOW")
        summary = {
            "source": {
                "company": source.company.name if source.company else handler.display_name,
                "original_url": canonical_url,
                "canonical_url": source.canonical_url,
                "domain": next(iter(self._handler_contract(handler, canonical_url).allowed_domains)),
            },
            "safety": {"robots": "POLICY_LOADED", "redirects": "CONTRACT_ENFORCED", "boundary": "ALLOWED"},
            "discovery": {"careers_url": canonical_url, "jobs_discovered": len(valid) + len(rejected), "jobs_verified": len(valid)},
            "extraction": {"strategy": handler.source_id, "fallback_required": False, "status": "DETERMINISTIC"},
            "validation": {"valid_jobs": len(valid), "rejected_jobs": len(rejected), "warnings": warnings, "rejections": rejected},
            "execution": {"ready": execution_ready, "handler_approved": execution_ready, "status": "READY" if execution_ready else "NOT_READY", "reason": None if execution_ready else "preview-only discovery; execution handler is not approved"},
            "readiness": {"status": "READY" if execution_ready else "EXECUTION_NOT_READY", "level": readiness},
            "sample_jobs": valid[:PREVIEW_SAMPLE_SIZE],
            "external_job_candidates": getattr(handler, "external_candidates", [])[:GENERIC_PREVIEW_MAX_JOBS],
        }
        preview = SourcePreview(
            source=source,
            version=version,
            boundary_fingerprint=source.boundary_fingerprint or "",
            status="READY",
            validation_summary=summary,
            created_at=self.now(),
            expires_at=self.now() + PREVIEW_TTL,
        )
        self.session.add(preview)
        transition_source(source, "PREVIEW_READY")
        source.readiness_status = "READY" if execution_ready else "BLOCKED"
        source.last_validated_at = self.now()
        self.session.flush()
        return preview

    def _start_trace(self, source_id: str) -> AgentRun:
        run = AgentRun(id=uuid4(), started_at=self.now(), status="RUNNING", sources_total=1)
        self.session.add(run)
        self.session.flush()
        self._event(run, "onboarding_started", source_id)
        return run

    def _event(self, run: AgentRun, event_type: str, source_id: str, *, status: str = "SUCCESS", error_code: str | None = None, error_message: str | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.session.add(ExecutionEvent(run_id=run.id, operation_id=uuid4(), source_id=source_id, event_type=event_type, status=status, error_code=error_code, error_message=sanitize_error_text(error_message), metadata_json=metadata))
        self.session.flush()

    def _complete_trace(self, run: AgentRun, status: str, error: str | None = None) -> None:
        run.status = status
        run.ended_at = self.now()
        run.sources_success = 1 if status == "SUCCESS" else 0
        run.sources_failed = 0 if status == "SUCCESS" else 1
        run.error_summary = sanitize_error_text(error)

    @staticmethod
    def _failure(code: str, message: str, *, retryable: bool = False, source_health_related: bool = False) -> OnboardingError:
        return OnboardingError(OnboardingFailure(code, message, retryable=retryable, source_health_related=source_health_related))

    @staticmethod
    def _classify_exception(error: Exception) -> OnboardingFailure:
        if isinstance(error, SourceBlockedError):
            message = str(error)
            if "robots.txt" in message or "robots" in message.lower():
                code = "ROBOTS_DISALLOWED" if "disallow" in message.lower() else "POLICY_UNRESOLVED"
            elif "redirect" in message.lower():
                code = "REDIRECT_DOMAIN_NOT_ALLOWED"
            else:
                code = "DOMAIN_NOT_ALLOWED" if "contract" in message.lower() else "PATH_NOT_ALLOWED"
            return OnboardingFailure(code, message, safety_blocked=True)
        if isinstance(error, RateLimitedError):
            return OnboardingFailure("HTTP_429", str(error), retryable=True, source_health_related=True)
        if isinstance(error, ServerError):
            return OnboardingFailure("HTTP_5XX", str(error), retryable=True, source_health_related=True)
        if isinstance(error, requests.Timeout) or isinstance(error, TimeoutError):
            return OnboardingFailure("TIMEOUT", "The source request timed out.", retryable=True, source_health_related=True)
        if isinstance(error, requests.ConnectionError) or isinstance(error, ConnectionError):
            return OnboardingFailure("NETWORK_FAILURE", "The source could not be reached.", retryable=True, source_health_related=True)
        if isinstance(error, requests.HTTPError):
            status = error.response.status_code if error.response is not None else None
            if status == 404:
                return OnboardingFailure("CAREERS_PAGE_NOT_FOUND", "The source page was not found.", user_fixable=True)
            return OnboardingFailure("EXTRACTION_FAILED", "The source returned an unusable response.", source_health_related=True)
        return OnboardingFailure("EXTRACTION_FAILED", sanitize_error_text(str(error)) or "Source extraction failed.")


def _contracts() -> dict[str, Any]:
    return {
        "solita_dk": SOLITA_CONTRACT,
        "vestas_dk": VESTAS_DRAFT_CONTRACT,
        "trifork_dk": TRIFORK_CONTRACT,
        "energinet_dk": ENERGINET_DRAFT_CONTRACT,
    }


def _boundary_fingerprint(contract: Any) -> str:
    values = {
        "source_id": contract.source_id,
        "careers_url": contract.careers_url,
        "allowed_domains": sorted(contract.allowed_domains),
        "allowed_paths": contract.allowed_paths,
        "denied_paths": contract.denied_paths,
        "crawl_delay_seconds": contract.crawl_delay_seconds,
        "max_pages": contract.max_pages,
        "max_jobs": contract.max_jobs,
        "delegated_ats_domain": getattr(contract, "delegated_ats_domain", None),
        "delegated_ats_provider": getattr(contract, "delegated_ats_provider", None),
        "pinned_tenant_params": getattr(contract, "pinned_tenant_params", {}),
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode("utf-8")).hexdigest()


def _preview_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(record.get("title") or "").strip(),
        "url": str(record.get("url") or "").strip(),
        "location": record.get("location") or "",
        "country": record.get("country") or [],
        "employment_type": record.get("employment_type") or "UNKNOWN",
        "status": record.get("status") or "unknown",
        "date_posted": record.get("date_posted"),
        "valid_through": record.get("valid_through"),
        "description": str(record.get("description") or "")[:2000],
        "external_job_id": record.get("external_job_id"),
    }
