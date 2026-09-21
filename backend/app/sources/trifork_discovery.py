"""Trifork Denmark: official listing plus tenant-bound Homerun details.

The official listing provides locations for pages without JobPosting data.
Duckwise links remain unverified external leads because its robots policy cannot
be loaded by SafeSourceClient. No detail URL is accepted unless listed by
Trifork on the current fetch.
"""

from __future__ import annotations

from datetime import date
from html import unescape
from html.parser import HTMLParser
import re
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from .discovery import SafeSourceClient, SourceContract
from .extractors import extract_job_page_record, expiry_signal


TRIFORK_CONTRACT = SourceContract(
    source_id="trifork_dk",
    careers_url="https://trifork.com/join/job-openings/",
    allowed_domains=frozenset({"trifork.com", "trifork.homerun.co"}),
    # Homerun serves job slugs at the tenant subdomain root. The handler only
    # fetches slugs linked by Trifork's current official listing.
    allowed_paths=("/",),
    denied_paths=("/apply", "/application"),
    crawl_delay_seconds=5,
    max_pages=21,
    max_jobs=20,
)


class _ListingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.jobs: list[dict[str, str]] = []
        self._job: dict[str, str] | None = None
        self._depth = 0
        self._capture: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if self._job is None and tag == "a" and "job-item" in classes:
            self._job = {
                "href": attributes.get("href") or "",
                "location_key": attributes.get("data-location") or "",
                "title": "", "location": "", "employment_type": "",
            }
            self._depth = 1
            return
        if self._job is None:
            return
        if tag == "a":
            self._depth += 1
        if tag == "h3":
            self._capture = "title"
        elif "job-pos-location" in classes:
            self._capture = "location"
        elif "job-pos-type" in classes:
            self._capture = "employment_type"

    def handle_endtag(self, tag: str) -> None:
        if self._job is None:
            return
        if tag == "a":
            self._depth -= 1
            if self._depth == 0:
                self.jobs.append(self._job)
                self._job = None
                self._capture = None
        elif tag == "h3" and self._capture == "title":
            self._capture = None
        elif tag == "div" and self._capture in {"location", "employment_type"}:
            self._capture = None

    def handle_data(self, data: str) -> None:
        if self._job is not None and self._capture is not None:
            self._job[self._capture] += data + " "


def _job_posting(items: Any) -> dict[str, Any] | None:
    if isinstance(items, list):
        return next((found for item in items if (found := _job_posting(item)) is not None), None)
    if isinstance(items, dict):
        if items.get("@type") == "JobPosting":
            return items
        return _job_posting(items.get("@graph"))
    return None


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _description(record: dict[str, Any], posting: dict[str, Any] | None) -> str:
    if posting:
        raw = str(posting.get("description") or "")
        parser = _PlainText()
        parser.feed(raw)
        return " ".join(unescape(" ".join(parser.parts)).split())
    raw = str(record.get("description") or "")
    # Homerun details include a short sharing/application toolbar before the
    # actual job. The official listing title is kept separately for display.
    return " ".join(raw.split())


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


class _ApplicationControlParser(HTMLParser):
    """Read the job-specific Homerun apply/notify control without opening it."""

    def __init__(self, job_url: str) -> None:
        super().__init__()
        self.job_url = job_url.rstrip("/")
        self.labels: list[str] = []
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        target = urlsplit(urljoin(self.job_url, href))
        expected = urlsplit(self.job_url)
        if (
            target.scheme == "https"
            and target.hostname == expected.hostname
            and re.fullmatch(re.escape(expected.path) + r"/[a-z]{2}/apply/?", target.path)
        ):
            self._href = href
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.labels.append(" ".join(" ".join(self._parts).split()).casefold())
            self._href = None
            self._parts = []


def _application_status(html: str, job_url: str) -> str:
    parser = _ApplicationControlParser(job_url)
    parser.feed(html)
    parser.close()
    if any(label == "apply now" for label in parser.labels):
        return "active"
    if parser.labels and all(label == "notify me" for label in parser.labels):
        return "closed"
    return "unknown"


class TriforkSourceHandler:
    source_id = "trifork_dk"
    display_name = "Trifork Denmark"
    contract = TRIFORK_CONTRACT
    execution_ready = True

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.external_candidates: list[dict[str, str]] = []

    def can_handle(self, url: str) -> bool:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https":
            return False
        if host == "trifork.com":
            return parsed.path == "/join/job-openings/"
        return (
            host == "trifork.homerun.co"
            and not any(parsed.path.startswith(path) for path in self.contract.denied_paths)
            and bool(re.fullmatch(r"/[a-z0-9-]+/?", parsed.path))
        )

    def extract_record(
        self, url: str, *, html_content: str | None = None, transport: Any | None = None
    ) -> dict[str, Any]:
        if html_content is None:
            if transport is None:
                from .phase1_trial import _RequestsTransport
                transport = _RequestsTransport()
            client = transport if isinstance(transport, SafeSourceClient) else SafeSourceClient(self.contract, transport)
            response = client.get(url)
            response.raise_for_status()
            html_content = response.text
        record = extract_job_page_record(html_content, source=self.source_id, url=url)
        posting = _job_posting(record.get("json_ld"))
        record["description"] = _description(record, posting)
        record["status"] = "expired" if expiry_signal(record["description"]) else "unknown"
        record["external_job_id"] = urlsplit(url).path.strip("/")
        return record

    def discover_jobs(self, careers_url: str, *, transport: Any | None = None) -> list[dict[str, Any]]:
        if careers_url != self.contract.careers_url:
            raise ValueError("Trifork discovery requires its official listing URL")
        if transport is None:
            from .phase1_trial import _RequestsTransport
            transport = _RequestsTransport()
        sleep = getattr(transport, "sleep", None)
        client = SafeSourceClient(self.contract, transport, **({"sleep": sleep} if callable(sleep) else {}))
        landing = client.get(careers_url)
        landing.raise_for_status()
        parser = _ListingParser()
        parser.feed(landing.text)
        parser.close()
        self.warnings = []
        self.external_candidates = []
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in parser.jobs:
            if len(records) >= self.contract.max_jobs or client.pages_fetched >= self.contract.max_pages:
                break
            parsed = urlsplit(urljoin(careers_url, item["href"]))
            url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
            title = " ".join(item["title"].split())
            location = " ".join(item["location"].replace("Location", "", 1).split())
            if url in seen or not title or not location:
                continue
            seen.add(url)
            if item["location_key"].lower() not in {"aarhus", "copenhagen"}:
                continue
            if not self.can_handle(url):
                self.external_candidates.append({"title": title, "url": url, "host": parsed.hostname or ""})
                self.warnings.append(f"EXTERNAL_DETAIL_NOT_VERIFIED: {url}")
                continue
            try:
                detail = client.get(url)
                detail.raise_for_status()
                record = self.extract_record(url, html_content=detail.text)
            except Exception as error:
                self.warnings.append(f"DETAIL_NOT_VERIFIED: {url} ({type(error).__name__})")
                continue
            posting = _job_posting(record.get("json_ld"))
            if len(record["description"]) < 200 or not title.casefold().split()[0] in (record["title"] + " " + record["description"][:250]).casefold():
                self.warnings.append(f"JOB_DETAIL_NOT_VERIFIED: {url}")
                continue
            valid_through = _date(posting.get("validThrough")) if posting else None
            date_posted = _date(posting.get("datePosted")) if posting else None
            if valid_through and valid_through < date.today() or date_posted and date_posted > date.today():
                self.warnings.append(f"JOB_DATE_NOT_CURRENT: {url}")
                continue
            application_status = _application_status(detail.text, url)
            if application_status != "active" or record["status"] == "expired":
                self.warnings.append(f"APPLICATION_{application_status.upper()}: {url}")
                continue
            record.update({
                "title": title, "location": location, "country": ["Denmark"],
                "employment_type": "FULL_TIME" if "full-time" in item["employment_type"].lower() else "UNKNOWN",
                "status": "active",
                "availability_signal": "current official Trifork listing and job-specific Apply now control",
                "date_posted": date_posted.isoformat() if date_posted else None,
                "valid_through": valid_through.isoformat() if valid_through else None,
                "source": self.source_id,
            })
            records.append(record)
        return records
