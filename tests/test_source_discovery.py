from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.app.sources.discovery import (
    DiscoveredJobLink,
    DiscoveryError,
    SafeSourceClient,
    SourceBlockedError,
    SourceContract,
    deduplicate_links,
    next_page_url,
)
from backend.app.sources.netcompany_discovery import NetcompanyDiscovery
from backend.app.sources.solita_discovery import SOLITA_CONTRACT
from backend.app.sources.solita_discovery import SolitaDiscovery


@dataclass
class Response:
    url: str
    payload: list[dict]

    def json(self):
        return self.payload


@dataclass
class RobotsResponse:
    url: str
    text: str = ""
    status_code: int = 200


class FakeTransport:
    """Serves a live robots.txt separately from the recorded content fetches."""

    def __init__(self, response, robots_text: str = "", robots_status: int = 200):
        self.response = response
        self.robots_text = robots_text
        self.robots_status = robots_status
        self.urls = []
        self.robots_urls = []

    def get(self, url):
        if url.endswith("/robots.txt"):
            self.robots_urls.append(url)
            return RobotsResponse(url, self.robots_text, self.robots_status)
        self.urls.append(url)
        return self.response


def test_allowlist_rejects_external_domain_and_path():
    contract = SourceContract("s", "https://example.com/careers/", frozenset({"example.com"}), ("/careers/",))
    client = SafeSourceClient(contract, FakeTransport(Response("https://example.com/careers/", [])), sleep=lambda _: None)
    with pytest.raises(SourceBlockedError):
        client.get("https://evil.example/jobs")
    with pytest.raises(SourceBlockedError):
        client.get("https://example.com/admin")


def test_live_robots_is_fetched_and_enforced_before_any_content_request():
    """The live robots.txt is fetched and enforced; a disallowed path is never requested."""
    contract = SourceContract("s", "https://example.com/careers/", frozenset({"example.com"}), ("/careers/",))
    transport = FakeTransport(
        Response("https://example.com/careers/", []),
        robots_text="User-agent: *\nDisallow: /careers/private\n",
    )
    client = SafeSourceClient(contract, transport, sleep=lambda _: None)
    with pytest.raises(SourceBlockedError, match="robots.txt disallows URL"):
        client.get("https://example.com/careers/private")
    assert transport.robots_urls == ["https://example.com/robots.txt"]
    assert transport.urls == []


def test_robots_unavailable_or_disallowed_path_fails_closed():
    contract = SourceContract("s", "https://example.com/careers/", frozenset({"example.com"}), ("/careers/",))
    unavailable = FakeTransport(Response("https://example.com/careers/", []), robots_status=404)
    with pytest.raises(SourceBlockedError, match="robots.txt unavailable"):
        SafeSourceClient(contract, unavailable, sleep=lambda _: None).get("https://example.com/careers/")
    assert unavailable.urls == []


def test_allowed_path_redirect_is_rejected_when_destination_leaves_contract():
    contract = SourceContract("s", "https://example.com/careers/", frozenset({"example.com"}), ("/careers/",))
    client = SafeSourceClient(contract, FakeTransport(Response("https://evil.example/careers/", [])), sleep=lambda _: None)
    with pytest.raises(SourceBlockedError, match="redirect"):
        client.get("https://example.com/careers/")


def test_crawl_delay_and_page_limit_are_enforced():
    waits = []
    contract = SourceContract("s", "https://example.com/careers/", frozenset({"example.com"}), ("/careers/",), crawl_delay_seconds=10, max_pages=1)
    client = SafeSourceClient(contract, FakeTransport(Response("https://example.com/careers/", [])), sleep=waits.append)
    client.get("https://example.com/careers/")
    with pytest.raises(DiscoveryError):
        client.get("https://example.com/careers/next")
    assert waits == []


def test_pagination_stays_in_contract():
    contract = SourceContract("s", "https://example.com/careers/", frozenset({"example.com"}), ("/careers/",))
    assert next_page_url(contract.careers_url, "/careers/page/2", contract) == "https://example.com/careers/page/2"
    assert next_page_url(contract.careers_url, "https://evil.example/page/2", contract) is None


def test_duplicate_url_and_id_prevention_and_max_jobs():
    links = [
        DiscoveredJobLink("https://example.com/a", external_job_id="1"),
        DiscoveredJobLink("https://example.com/a", external_job_id="2"),
        DiscoveredJobLink("https://example.com/b", external_job_id="1"),
        DiscoveredJobLink("https://example.com/c", external_job_id="3"),
    ]
    result = deduplicate_links(links, 2)
    assert [item.url for item in result] == ["https://example.com/a", "https://example.com/c"]


def test_netcompany_is_explicitly_blocked_without_transport():
    with pytest.raises(SourceBlockedError, match="robots.txt returned HTTP 404"):
        NetcompanyDiscovery().discover()


def test_solita_contract_preserves_approved_api_and_limits():
    assert SOLITA_CONTRACT.source_id == "solita_dk"
    assert "www.solita.fi" in SOLITA_CONTRACT.allowed_domains
    assert SOLITA_CONTRACT.crawl_delay_seconds == 10
    assert SOLITA_CONTRACT.max_jobs == 100


def test_solita_strategy_filters_denmark_and_deduplicates():
    payload = [
        {"title": {"rendered": "Danish role"}, "link": "https://www.solita.fi/positions/danish-1/", "roles": [], "countries": [{"slug": "denmark", "name": "Denmark"}]},
        {"title": {"rendered": "Danish duplicate"}, "link": "https://www.solita.fi/positions/danish-1/", "roles": [], "countries": [{"slug": "denmark", "name": "Denmark"}]},
        {"title": {"rendered": "Finnish role"}, "link": "https://www.solita.fi/positions/finnish-2/", "roles": [], "countries": [{"slug": "finland", "name": "Finland"}]},
    ]
    client = SafeSourceClient(SOLITA_CONTRACT, FakeTransport(Response("https://www.solita.fi/wp-json/wp/v2/positions", payload)), sleep=lambda _: None)
    result = SolitaDiscovery(client).discover()
    assert [link.url for link in result.links] == ["https://www.solita.fi/positions/danish-1/"]


def test_denmark_job_is_retained_with_unrelated_or_unknown_category():
    payload = [{
        "title": {"rendered": "Operations role"},
        "link": "https://www.solita.fi/positions/operations-1/",
        "roles": [{"name": "Unknown", "slug": "unknown"}],
        "countries": [{"slug": "denmark", "name": "Denmark"}],
    }]
    client = SafeSourceClient(SOLITA_CONTRACT, FakeTransport(Response("https://www.solita.fi/wp-json/wp/v2/positions", payload)), sleep=lambda _: None)
    result = SolitaDiscovery(client).discover()
    assert len(result.links) == 1
    assert result.links[0].metadata["area"] == ["Unknown"]


def test_optional_category_filters_are_not_required():
    assert SOLITA_CONTRACT.query_params == {"country": "denmark"}
    assert not any(key in SOLITA_CONTRACT.query_params for key in ("department", "area", "category", "job_family"))


def test_geographic_filter_rejects_non_denmark_job():
    payload = [{
        "title": {"rendered": "Finnish software role"},
        "link": "https://www.solita.fi/positions/finland-1/",
        "roles": [{"name": "Software"}],
        "countries": [{"slug": "finland", "name": "Finland"}],
    }]
    client = SafeSourceClient(SOLITA_CONTRACT, FakeTransport(Response("https://www.solita.fi/wp-json/wp/v2/positions", payload)), sleep=lambda _: None)
    assert SolitaDiscovery(client).discover().links == ()
