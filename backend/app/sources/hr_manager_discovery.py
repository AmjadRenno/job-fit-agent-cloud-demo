"""Generic HR-Manager (Talentech) delegated ATS discovery and extraction."""

from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import json
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlparse

import requests

from backend.app.sources.discovery import (
    DiscoveryError,
    DiscoveryResult,
    DiscoveredJobLink,
    SafeSourceClient,
    SourceBlockedError,
    SourceContract,
    deduplicate_links,
)
from backend.app.sources.extractors import (
    expiry_signal,
    extract_job_page_record,
    hr_manager_external_id,
)
from backend.app.sources.energinet_contract import ENERGINET_DRAFT_CONTRACT


class _HrManagerListingParser(HTMLParser):
    """Extract vacancy links and anchor text from HR-Manager listing HTML."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[tuple[str, str]] = []
        self.position_list_value: str | None = None
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            attrs_dict = dict(attrs)
            href = attrs_dict.get("href")
            if href:
                self._current_href = href
                self._current_text = []
        elif tag.lower() == "input":
            attrs_dict = dict(attrs)
            if attrs_dict.get("type", "").lower() == "hidden":
                input_name = attrs_dict.get("id") or attrs_dict.get("name") or ""
                if input_name.endswith("HiddenField_PositionList"):
                    self.position_list_value = attrs_dict.get("value")

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._current_href is not None:
            text = " ".join(" ".join(self._current_text).split())
            self.links.append((self._current_href, text))
            self._current_href = None
            self._current_text = []


class HrManagerDiscovery:
    """Generic discovery strategy for candidate.hr-manager.net tenants."""

    _ATS_HOST_FALLBACK = "candidate.hr-manager.net"
    # Pinned Energinet tenant identifier used by the canonical
    # ``/ApplicationInit.aspx`` vacancy/advertisement page (``cid=316``).
    # The ATS path never carries ``customer=Energinet``; that scheme applies
    # only to the legacy ``/vacancies/`` listing path.
    _APPLICATION_INIT_CID = "316"
    _APPLICATION_INIT_PATH = "/ApplicationInit.aspx"
    # Defense in depth: never surface application/submission pages as vacancies,
    # even if a future allowed_paths change would admit them. The canonical
    # advertisement page (``/ApplicationInit.aspx`` without
    # ``SkipAdvertisement=true``) is intentionally NOT in this blocklist: the
    # live listing JS reads ``AdvertisementUrlSecure`` for the position link.
    _APPLICATION_PATH_MARKERS = ("apply.aspx", "/apply")

    def __init__(
        self,
        client: SafeSourceClient,
        *,
        tenant: str | None = None,
    ) -> None:
        self.client = client
        self.contract = client.contract
        self.tenant = tenant or self.contract.pinned_tenant_params.get("customer")

    def _listing_url(self) -> str:
        if self.tenant:
            ats_host = self.contract.delegated_ats_domain or "candidate.hr-manager.net"
            return f"https://{ats_host}/vacancies/list.aspx?customer={self.tenant}"
        return self.contract.careers_url

    @staticmethod
    def _is_application_path(path: str) -> bool:
        lowered = path.lower()
        return any(marker in lowered for marker in HrManagerDiscovery._APPLICATION_PATH_MARKERS)

    def _link_from_url(self, url: Any, title: str, base_url: str) -> DiscoveredJobLink | None:
        """Validate one candidate vacancy URL against every contract boundary.

        Shared by the normal ``<a>`` anchor path and the embedded position
        list so both honour the exact same security checks.
        """
        if not isinstance(url, str) or not url.strip():
            return None
        candidate = urljoin(base_url, url.strip())
        parsed = urlparse(candidate)
        if parsed.scheme != "https":
            return None
        ats_host = (self.contract.delegated_ats_domain or self._ATS_HOST_FALLBACK).lower()
        if (parsed.hostname or "").lower() != ats_host.lower():
            return None
        path = parsed.path.lower()
        query_dict = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if path.startswith(self._APPLICATION_INIT_PATH.lower()):
            return self._advertisement_link(candidate, query_dict, title)
        # Legacy listing-scheme vacancy pages (``/vacancies/vacancy.aspx``).
        # Must be a vacancy detail page, not the listing or an application.
        if "vacancy.aspx" not in path:
            return None
        if self._is_application_path(path):
            return None
        # Strictly enforce tenant / customer parameter matching.
        customer_in_link = query_dict.get("customer")
        if not customer_in_link or customer_in_link.lower() != self.tenant.lower():
            return None
        # Check if URL passes contract boundaries (domain allowlist, allowed and
        # denied paths, pinned tenant params; robots and redirects are enforced
        # by the SafeSourceClient at fetch time).
        if not self.contract.allows(candidate):
            return None
        job_id = hr_manager_external_id(candidate)
        return DiscoveredJobLink(
            url=candidate,
            title=title,
            external_job_id=job_id,
            metadata={
                "source": self.contract.source_id,
                "customer": self.tenant,
                "country": ["Denmark"],
            },
        )

    def _advertisement_link(
        self,
        candidate: str,
        query_dict: dict[str, str],
        title: str,
    ) -> DiscoveredJobLink | None:
        """Validate a canonical ``/ApplicationInit.aspx`` advertisement URL.

        The live listing JS uses each item's ``AdvertisementUrlSecure``
        (``cid=316&ProjectId=<id>&DepartmentId=<id>&MediaId=5``) as the
        position link. Only the advertisement view (no
        ``SkipAdvertisement=true``) is a vacancy page; the direct application
        form must never be surfaced as a vacancy. Tenant isolation is pinned
        to ``cid=316``.
        """
        if query_dict.get("cid") != self._APPLICATION_INIT_CID:
            return None
        skip = query_dict.get("SkipAdvertisement") or query_dict.get("skipadvertisement")
        if isinstance(skip, str) and skip.lower() == "true":
            return None
        project_id = query_dict.get("ProjectId") or query_dict.get("projectid")
        if not project_id or not str(project_id).isdigit():
            return None
        if not self.contract.allows(candidate):
            return None
        return DiscoveredJobLink(
            url=candidate,
            title=title,
            external_job_id=str(project_id),
            metadata={
                "source": self.contract.source_id,
                "customer": self.tenant,
                "country": ["Denmark"],
            },
        )

    def _canonical_advertisement_url(self, job_id: Any, item: dict[str, Any]) -> str | None:
        """Build the tenant-pinned ``/ApplicationInit.aspx`` advertisement URL.

        This is the only vacancy page the live ATS serves (verified 200 for a
        real Energinet ``ProjectId``); the legacy
        ``/vacancies/vacancy.aspx?customer=...&id=...`` guess 404s to
        ``Error404.html?aspxerrorpath=/vacancies/vacancy.aspx``. Extra context
        params (DepartmentId/MediaId) are preserved when numeric; the apply
        variant (``SkipAdvertisement=true``) is never constructed here.
        """
        if job_id is None:
            return None
        str_id = str(job_id).strip()
        if not str_id.isdigit():
            return None
        ats_host = self.contract.delegated_ats_domain or self._ATS_HOST_FALLBACK
        parts = [f"cid={self._APPLICATION_INIT_CID}", f"ProjectId={str_id}"]
        department = item.get("Department")
        department_id = department.get("Id") if isinstance(department, dict) else item.get("DepartmentId")
        if department_id is not None and str(department_id).strip().isdigit():
            parts.append(f"DepartmentId={str(department_id).strip()}")
        media_id = item.get("MediaId", 5)
        if media_id is not None and str(media_id).strip().isdigit():
            parts.append(f"MediaId={str(media_id).strip()}")
        return f"https://{ats_host}{self._APPLICATION_INIT_PATH}?" + "&".join(parts)

    def _canonical_vacancy_url(self, job_id: Any) -> str | None:
        """Legacy ``/vacancies/vacancy.aspx`` URL builder (kept for compat)."""
        if job_id is None:
            return None
        str_id = str(job_id).strip()
        if not str_id.isdigit():
            return None
        ats_host = self.contract.delegated_ats_domain or self._ATS_HOST_FALLBACK
        return f"https://{ats_host}/vacancies/vacancy.aspx?customer={self.tenant}&id={str_id}"

    def _embedded_item_link(self, item: Any, base_url: str) -> DiscoveredJobLink | None:
        """Turn one embedded position list item into a validated job link."""
        if not isinstance(item, dict):
            return None
        job_id = item.get("Id")
        # The live listing JS uses each item's ``AdvertisementUrlSecure`` as
        # the position link; prefer it verbatim (it is re-validated below).
        # Fall back to the canonical tenant-pinned advertisement URL so an
        # item carrying only a numeric Id still resolves to the real page.
        # Never fall back to the legacy ``/vacancies/vacancy.aspx`` scheme:
        # the ATS has no such page and answers Error404 for it.
        url = (
            item.get("AdvertisementUrlSecure")
            or item.get("AdvertisementUrl")
            or item.get("Url")
            or item.get("url")
        )
        if url is None:
            url = self._canonical_advertisement_url(job_id, item)
        if url is None:
            return None
        title = str(item.get("Name") or item.get("Title") or "").strip() or "Untitled position"
        return self._link_from_url(url, title, base_url)

    def _embedded_links(self, html: str, listing_url: str) -> list[DiscoveredJobLink]:
        """Extract vacancies from the entity-encoded ``HiddenField_PositionList`` JSON.

        The live candidate.hr-manager.net listing renders vacancy data through a
        hidden input (``...HiddenField_PositionList``) instead of server-side
        ``<a href=\"vacancy.aspx...\">`` anchors. The value is an HTML-entity
        encoded JSON payload carrying ``PositionList.Items`` (``Id``/``Name``)
        plus a ``CustomerAlias`` that must match the pinned tenant.
        """
        parser = _HrManagerListingParser(listing_url)
        parser.feed(html)
        parser.close()
        raw_value = parser.position_list_value
        if raw_value is None or not raw_value.strip():
            return []

        try:
            payload = json.loads(unescape(raw_value))
        except (json.JSONDecodeError, TypeError, ValueError):
            raise DiscoveryError(
                "HR-Manager position list hidden field is not valid JSON"
            ) from None
        if not isinstance(payload, dict):
            raise DiscoveryError("HR-Manager position list hidden field is not a JSON object")

        position_list = payload.get("PositionList")
        alias = payload.get("CustomerAlias")
        if isinstance(position_list, dict):
            alias = alias or position_list.get("CustomerAlias")
        # Reject listings whose customer alias cannot be proven to match the
        # pinned tenant; never ingest cross-tenant vacancy data silently.
        if not alias or str(alias).lower() != self.tenant.lower():
            raise SourceBlockedError(
                f"HR-Manager position list customer alias {alias!r} does not match "
                f"pinned tenant {self.tenant!r}"
            )

        links: list[DiscoveredJobLink] = []
        if isinstance(position_list, dict) and isinstance(position_list.get("Items"), list):
            for item in position_list["Items"]:
                link = self._embedded_item_link(item, listing_url)
                if link is not None:
                    links.append(link)
        return links

    def discover(self) -> DiscoveryResult:
        if not self.tenant:
            raise SourceBlockedError(
                f"HR-Manager discovery requires a pinned tenant/customer parameter for source {self.contract.source_id}"
            )

        listing_url = self._listing_url()
        response = self.client.get(listing_url)
        html = getattr(response, "text", "") if not isinstance(response, str) else response

        raw_links: list[DiscoveredJobLink] = []

        # Client-rendered listings expose their vacancies as embedded JSON
        # (HiddenField_PositionList); prefer those structured entries first so
        # their real titles win any deduplication.
        raw_links.extend(self._embedded_links(html, listing_url))

        # Keep normal <a> extraction as a fallback for server-rendered listings.
        parser = _HrManagerListingParser(listing_url)
        parser.feed(html)
        parser.close()
        for href, text in parser.links:
            link = self._link_from_url(href, text or "Untitled position", listing_url)
            if link is not None:
                raw_links.append(link)

        deduped = deduplicate_links(raw_links, self.contract.max_jobs)
        return DiscoveryResult(
            source_id=self.contract.source_id,
            links=deduped,
            pages_fetched=self.client.pages_fetched,
        )


class HrManagerSourceHandler:
    """Extraction handler for HR-Manager delegated ATS sources.

    Availability rule: UNKNOWN — never infer ACTIVE from HTTP 200 or page existence.
    """

    execution_ready: bool = True

    def __init__(
        self,
        contract: SourceContract | None = None,
        *,
        source_id: str = "energinet_dk",
        display_name: str = "Energinet Denmark",
        tenant: str = "Energinet",
    ) -> None:
        self.contract = contract or ENERGINET_DRAFT_CONTRACT
        self.source_id = source_id
        self.display_name = display_name
        self.tenant = tenant

    def _active_contract(self) -> SourceContract:
        """Return the contract exactly as configured.

        The handler must NEVER self-authorize: a disabled or non-APPROVED
        contract stays disabled so ``SafeSourceClient`` refuses to fetch.
        Approval is a human decision, not something the adapter can grant.
        """
        return self.contract

    def can_handle(self, url: str) -> bool:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if hostname == (self.contract.delegated_ats_domain or "candidate.hr-manager.net").lower():
            query_dict = dict(parse_qsl(parsed.query, keep_blank_values=True))
            if query_dict.get("customer", "").lower() == self.tenant.lower():
                return True
            # Canonical vacancy/advertisement page pins the tenant via cid=316.
            if parsed.path.lower().startswith("/applicationinit.aspx"):
                return query_dict.get("cid") == HrManagerDiscovery._APPLICATION_INIT_CID
            return False
        if hostname in self.contract.allowed_domains:
            return any(parsed.path.startswith(p) for p in self.contract.allowed_paths)
        return False

    def extract_record(
        self,
        url: str,
        *,
        html_content: str | None = None,
        transport: Any | None = None,
    ) -> dict[str, Any]:
        active_contract = self._active_contract()
        if html_content is None:
            if transport is None:
                from backend.app.sources.phase1_trial import _RequestsTransport
                transport = _RequestsTransport()
            if isinstance(transport, SafeSourceClient):
                client = transport
            else:
                sleep = getattr(transport, "sleep", None)
                inner = transport.transport if isinstance(transport, SafeSourceClient) else transport
                client = SafeSourceClient(
                    active_contract,
                    inner,
                    **({"sleep": sleep} if callable(sleep) else {}),
                )
            response = client.get(url)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            response_text = getattr(response, "text", None)
            html_content = response_text if isinstance(response_text, str) else str(response)

        record = extract_job_page_record(html_content, source=self.source_id, url=url)
        external_id = hr_manager_external_id(url)

        # Availability is intentional UNKNOWN unless an explicit closure marker
        # exists: HTTP 200 and page existence alone never imply ACTIVE, and no
        # deterministic vacancy-open signal exists on the HR-Manager page
        # (Decision B).
        status = "expired" if expiry_signal(record.get("description") or "") else "unknown"

        record.update({
            "status": status,
            "availability_signal": None,
            "external_job_id": external_id,
            "source": self.source_id,
            "country": ["Denmark"],
        })
        return record

    def discover_jobs(
        self,
        careers_url: str,
        *,
        transport: Any | None = None,
    ) -> list[dict[str, Any]]:
        if transport is None:
            from backend.app.sources.phase1_trial import _RequestsTransport
            transport = _RequestsTransport()
        active_contract = self._active_contract()
        sleep = getattr(transport, "sleep", None)
        client = (
            transport
            if isinstance(transport, SafeSourceClient)
            else SafeSourceClient(
                active_contract,
                transport,
                **({"sleep": sleep} if callable(sleep) else {}),
            )
        )
        discovery = HrManagerDiscovery(client, tenant=self.tenant)
        result = discovery.discover()

        detail_transport = client.transport
        detail_sleep = getattr(detail_transport, "sleep", None)
        records: list[dict[str, Any]] = []

        for link in result.links:
            try:
                detail_client = SafeSourceClient(
                    active_contract,
                    detail_transport,
                    **({"sleep": detail_sleep} if callable(detail_sleep) else {}),
                )
                record = self.extract_record(link.url, transport=detail_client)
            except requests.HTTPError as error:
                if error.response is None or error.response.status_code != 404:
                    raise
                records.append({
                    "source": self.source_id,
                    "title": link.title or "Untitled position",
                    "url": link.url,
                    "description": None,
                    "location": "",
                    "employment_type": "UNKNOWN",
                    "status": "unavailable",
                    "external_job_id": link.external_job_id,
                    "country": ["Denmark"],
                })
                continue
            record["title"] = record.get("title") or link.title or "Untitled position"
            record["external_job_id"] = link.external_job_id or record.get("external_job_id")
            records.append(record)
        return records
