from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
from urllib.parse import urljoin, urlparse, urlunsplit
from urllib.robotparser import RobotFileParser

# Crawler identity sent when evaluating robots.txt and when fetching content.
# It is deliberately NOT a named AI crawler, so the wildcard group applies.
USER_AGENT = "JobFitAgent/2.0 (+personal-use)"


class DiscoveryError(RuntimeError):
    pass


class SourceBlockedError(DiscoveryError):
    pass


class RateLimitedError(DiscoveryError):
    """HTTP 429 persisted beyond the documented bounded retry policy."""


class ServerError(DiscoveryError):
    """HTTP 5xx persisted beyond the documented bounded retry policy."""


# Documented in docs/failure-recovery.md §2/§7: HTTP 429 backoff 30s/60s/120s
# and HTTP 5xx backoff 2s/4s/8s, each with 3 retry opportunities.
HTTP_429_BACKOFF_SECONDS = (30.0, 60.0, 120.0)
HTTP_5XX_BACKOFF_SECONDS = (2.0, 4.0, 8.0)
# An upper bound so a misbehaving Retry-After value cannot stall a run forever
# while still respecting normal server instructions.
MAX_RETRY_AFTER_SECONDS = 900.0


def retry_after_seconds(response: Any, fallback: float) -> float:
    """Parse ``Retry-After`` (integer seconds or an HTTP-date) into seconds.

    Falls back to ``fallback`` when the header is absent or unparseable. The
    result is clamped to ``MAX_RETRY_AFTER_SECONDS`` to keep the policy bounded.
    """
    headers = getattr(response, "headers", None)
    if not headers:
        return fallback
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if not raw:
        return fallback
    raw = str(raw).strip()
    if not raw:
        return fallback
    if raw.isdigit():
        seconds = float(raw)
    else:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return fallback
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        seconds = (parsed - datetime.now(timezone.utc)).total_seconds()
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


class Transport(Protocol):
    def get(self, url: str) -> Any: ...


@dataclass(frozen=True)
class SourceContract:
    source_id: str
    careers_url: str
    allowed_domains: frozenset[str]
    allowed_paths: tuple[str, ...]
    crawl_delay_seconds: float = 5.0
    max_pages: int = 10
    max_jobs: int = 100
    query_params: dict[str, str] = field(default_factory=dict)
    denied_paths: tuple[str, ...] = ()
    enabled: bool = True
    approval_status: str = "APPROVED"
    delegated_ats_domain: str | None = None
    delegated_ats_provider: str | None = None
    pinned_tenant_params: dict[str, str] = field(default_factory=dict)
    # NOTE: an embedded robots policy is deliberately NOT a contract field.
    # robots.txt is fetched live from the host being contacted and enforced in
    # SafeSourceClient, so no source can carry a hand-written exception that
    # disagrees with the site's real policy.

    def allows(self, url: str) -> bool:
        from urllib.parse import parse_qsl
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not hostname or hostname not in self.allowed_domains:
            return False
        if not self.enabled or self.approval_status != "APPROVED":
            return False
        if any(parsed.path.startswith(path) for path in self.denied_paths):
            return False
        if self.delegated_ats_domain and hostname == self.delegated_ats_domain.lower():
            if self.pinned_tenant_params:
                query_dict = dict(parse_qsl(parsed.query, keep_blank_values=True))
                path_lower = parsed.path.lower()
                if path_lower.startswith("/applicationinit.aspx"):
                    # The canonical vacancy/advertisement page identifies the
                    # tenant via ``cid`` (Energinet is cid=316); the legacy
                    # ``customer`` scheme does not apply to this path.
                    expected_cid = self.pinned_tenant_params.get("cid")
                    if expected_cid is not None and query_dict.get("cid") != expected_cid:
                        return False
                    # The application-form variant is the apply flow, never a
                    # readable vacancy, so it must not satisfy the contract.
                    skip = query_dict.get("SkipAdvertisement", query_dict.get("skipadvertisement", ""))
                    if str(skip).lower() == "true":
                        return False
                elif path_lower.startswith("/vacancies/"):
                    # The listing path identifies the tenant via ``customer``.
                    expected_customer = self.pinned_tenant_params.get("customer")
                    if expected_customer is not None and query_dict.get("customer") != expected_customer:
                        return False
                else:
                    # Unknown ATS path: any pinned param present must match,
                    # and at least one pinned tenant param must be present
                    # with the correct value so a tenant-less URL cannot
                    # piggyback on a future allowed_paths entry.
                    matched = False
                    for key, val in self.pinned_tenant_params.items():
                        if key in query_dict:
                            if query_dict.get(key) != val:
                                return False
                            matched = True
                    if not matched:
                        return False
        return any(parsed.path.startswith(path) for path in self.allowed_paths)


@dataclass(frozen=True)
class DiscoveredJobLink:
    url: str
    title: str | None = None
    external_job_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoveryResult:
    source_id: str
    links: tuple[DiscoveredJobLink, ...]
    pages_fetched: int
    warnings: tuple[str, ...] = ()


class DiscoveryStrategy(Protocol):
    def discover(self) -> DiscoveryResult: ...


class SafeSourceClient:
    def __init__(
        self,
        contract: SourceContract,
        transport: Transport,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.contract = contract
        self.transport = transport
        self.sleep = sleep
        self._last_request = 0.0
        self.pages_fetched = 0
        # Live robots.txt policies are fetched once per host per client.
        self._robots_cache: dict[str, RobotFileParser] = {}

    def get(self, url: str) -> Any:
        if not self.contract.enabled or self.contract.approval_status != "APPROVED":
            raise SourceBlockedError(f"source contract is not approved or enabled: {self.contract.source_id}")
        if not self.contract.allows(url):
            raise SourceBlockedError(f"URL is outside source contract: {url}")
        if self.pages_fetched >= self.contract.max_pages:
            raise DiscoveryError("maximum page limit reached")
        self._enforce_live_robots(url)
        return self._fetch_with_retry(url)

    def _enforce_live_robots(self, url: str) -> None:
        """Fetch and enforce the *live* robots.txt of the host being contacted.

        This is the only robots authority: no contract may carry an embedded
        policy that could permit a path the site's real robots.txt disallows.
        The robots request is not counted against the page budget, is not
        retried, and does not advance the crawl-delay clock, so content
        fetches keep exactly the pacing they had before.
        """
        hostname = (urlparse(url).hostname or "").lower()
        robots = self._robots_cache.get(hostname)
        if robots is None:
            robots = self._load_live_robots(hostname)
            self._robots_cache[hostname] = robots
        if not robots.can_fetch(USER_AGENT, url):
            raise SourceBlockedError(f"robots.txt disallows URL: {url}")

    def _load_live_robots(self, hostname: str) -> RobotFileParser:
        """Load ``https://<host>/robots.txt`` live; fail closed on any problem."""
        robots_url = urlunsplit(("https", hostname, "/robots.txt", "", ""))
        try:
            response = self.transport.get(robots_url)
        except Exception as error:  # network/transport failure => fail closed
            raise SourceBlockedError(f"robots.txt unavailable: {robots_url}") from error
        status = getattr(response, "status_code", 200)
        if status != 200:
            raise SourceBlockedError(f"robots.txt unavailable: {robots_url} (HTTP {status})")
        text = getattr(response, "text", None)
        robots = RobotFileParser()
        robots.set_url(robots_url)
        robots.parse((text if isinstance(text, str) else "").splitlines())
        return robots

    def _fetch_with_retry(self, url: str) -> Any:
        """Fetch a URL with the documented HTTP 429/5xx bounded retry policy.

        Sleeping is injectable through ``self.sleep`` so tests never actually
        wait. Page budget only counts requests that produced a usable response.
        """
        retries_429 = 0
        retries_5xx = 0
        while True:
            remaining = self.contract.crawl_delay_seconds - (time.monotonic() - self._last_request)
            if remaining > 0:
                self.sleep(remaining)
            response = self.transport.get(url)
            self._last_request = time.monotonic()
            status = getattr(response, "status_code", None)
            final_url = getattr(response, "url", None) or url
            if not self.contract.allows(final_url):
                raise SourceBlockedError(f"redirect leaves source contract: {final_url}")
            if status == 429:
                if retries_429 >= len(HTTP_429_BACKOFF_SECONDS):
                    raise RateLimitedError(
                        f"source {self.contract.source_id} is RATE_LIMITED after "
                        f"{len(HTTP_429_BACKOFF_SECONDS)} retries: {url}"
                    )
                wait = retry_after_seconds(response, HTTP_429_BACKOFF_SECONDS[retries_429])
                self.sleep(wait)
                retries_429 += 1
                continue
            if status is not None and 500 <= status < 600:
                if retries_5xx >= len(HTTP_5XX_BACKOFF_SECONDS):
                    raise ServerError(
                        f"source {self.contract.source_id} SERVER_ERROR after "
                        f"{len(HTTP_5XX_BACKOFF_SECONDS)} retries: HTTP {status} {url}"
                    )
                self.sleep(HTTP_5XX_BACKOFF_SECONDS[retries_5xx])
                retries_5xx += 1
                continue
            self.pages_fetched += 1
            return response


def deduplicate_links(links: list[DiscoveredJobLink], max_jobs: int) -> tuple[DiscoveredJobLink, ...]:
    result: list[DiscoveredJobLink] = []
    seen_urls: set[str] = set()
    seen_ids: set[str] = set()
    for link in links:
        if link.url in seen_urls or (link.external_job_id and link.external_job_id in seen_ids):
            continue
        seen_urls.add(link.url)
        if link.external_job_id:
            seen_ids.add(link.external_job_id)
        result.append(link)
        if len(result) >= max_jobs:
            break
    return tuple(result)


def next_page_url(current_url: str, href: str | None, contract: SourceContract) -> str | None:
    if not href:
        return None
    candidate = urljoin(current_url, href)
    return candidate if contract.allows(candidate) else None
