"""Vestas Denmark discovery adapter (Phase 12).

All Vestas-specific acquisition logic lives in this module. Shared,
source-agnostic protections (HTTPS/domain/path allowlist, robots.txt,
redirect re-check, crawl-delay, page/job budgets, bounded HTTP 429/5xx
retry with injectable sleep, deterministic extraction, expiry and
suspicious-content flags) are reused unchanged from ``discovery`` and
``extractors``. The approved draft contract in ``vestas_contract.py``
remains the authority: no allowlist, delay, or cap value is broadened
here.

Listing mechanism: official server-rendered search pages with the DK
facet (``optionsFacetsDD_country=DK``) and ``startrow`` pagination
(page size 10, at most 3 pages / 20 jobs per the contract caps).
"""

from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from .discovery import (
    DiscoveredJobLink,
    DiscoveryResult,
    DiscoveryStrategy,
    SafeSourceClient,
    deduplicate_links,
)
from .extractors import vestas_external_id
from .vestas_contract import VESTAS_DRAFT_CONTRACT

VESTAS_PAGE_SIZE = 10


def clean_vestas_description(description: str, title: str) -> str:
    """Remove SuccessFactors navigation surrounding a Vestas job detail.

    The title/requisition and footer anchors are both required when the known
    site shell is present. A changed template fails closed instead of storing
    the language selector and navigation as a job description.
    """
    text = " ".join(description.split())
    if "Talent Universe Language" not in text and not text.startswith("Skip to main content"):
        return text
    start = text.find(f"{title} Requisition ID:")
    footer = re.search(r"Apply now\s*»\s*Find similar jobs:", text[start:] if start >= 0 else "")
    if start < 0 or footer is None:
        raise ValueError("Vestas job detail boundaries are not verified")
    end = start + footer.start()
    for marker in (
        "Our commitment to a fair hiring.",
        "DEIB Statement",
        "BEWARE – RECRUITMENT FRAUD",
        "About Vestas",
    ):
        position = text.find(marker, start, end)
        if position >= 0:
            end = position
    return text[start:end].strip()


def vestas_page_url(page_index: int) -> str:
    """Return the search URL for a zero-based page, preserving the DK facet."""
    parsed = urlparse(VESTAS_DRAFT_CONTRACT.careers_url)
    params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() != "startrow"
    ]
    if page_index > 0:
        params.append(("startrow", str(page_index * VESTAS_PAGE_SIZE)))
    return urlunparse(parsed._replace(query=urlencode(params)))


class _VestasListingParser(HTMLParser):
    """Collect (href, anchor text) pairs from a search-result page.

    This is a source-specific listing helper, not a second generic job
    parser: detail-page extraction still uses the shared
    ``extract_job_page_record`` pipeline.
    """

    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self._href = value
                self._text = []
                break

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text)))
            self._href = None
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)


class VestasDiscovery(DiscoveryStrategy):
    """Discover DK job links from Vestas search pages via ``SafeSourceClient``."""

    def __init__(self, client: SafeSourceClient) -> None:
        self.client = client

    def discover(self) -> DiscoveryResult:
        links: list[DiscoveredJobLink] = []
        page_index = 0
        seen_pages = 0
        while self.client.pages_fetched < VESTAS_DRAFT_CONTRACT.max_pages:
            url = vestas_page_url(page_index)
            response = self.client.get(url)
            seen_pages += 1
            text = getattr(response, "text", None)
            page_links = self.parse_listing(text if isinstance(text, str) else "", url)
            if not page_links:
                break
            links.extend(page_links)
            if len(links) >= VESTAS_DRAFT_CONTRACT.max_jobs:
                break
            if seen_pages >= VESTAS_DRAFT_CONTRACT.max_pages:
                break
            page_index += 1
        return DiscoveryResult(
            source_id=VESTAS_DRAFT_CONTRACT.source_id,
            links=deduplicate_links(links, VESTAS_DRAFT_CONTRACT.max_jobs),
            pages_fetched=self.client.pages_fetched,
        )

    @staticmethod
    def parse_listing(html: str, page_url: str) -> list[DiscoveredJobLink]:
        """Parse one search page; malformed HTML fails closed to no links."""
        parser = _VestasListingParser()
        try:
            parser.feed(html)
            parser.close()
        except Exception:
            return []
        links: list[DiscoveredJobLink] = []
        for href, text in parser.anchors:
            candidate = urljoin(page_url, href).split("#", 1)[0]
            if "/job/" not in urlparse(candidate).path:
                continue
            if not VESTAS_DRAFT_CONTRACT.allows(candidate):
                continue
            title = unescape(" ".join(text.split())) or None
            links.append(DiscoveredJobLink(
                url=candidate,
                title=title,
                external_job_id=vestas_external_id(candidate),
                metadata={"country": ["Denmark"]},
            ))
        return links
