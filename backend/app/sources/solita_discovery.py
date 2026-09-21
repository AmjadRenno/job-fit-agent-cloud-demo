from __future__ import annotations

from html import unescape

from .discovery import (
    DiscoveredJobLink,
    DiscoveryResult,
    DiscoveryStrategy,
    SafeSourceClient,
    SourceContract,
    deduplicate_links,
)
from .extractors import solita_external_id

SOLITA_CONTRACT = SourceContract(
    source_id="solita_dk",
    careers_url="https://www.solita.fi/join-us/?country=denmark",
    allowed_domains=frozenset({"www.solita.fi"}),
    allowed_paths=("/join-us/", "/wp-json/wp/v2/positions", "/positions/"),
    crawl_delay_seconds=10,
    max_pages=101,
    max_jobs=100,
    query_params={"country": "denmark"},
)


class SolitaDiscovery(DiscoveryStrategy):
    def __init__(self, client: SafeSourceClient) -> None:
        self.client = client

    def discover(self) -> DiscoveryResult:
        url = SOLITA_CONTRACT.careers_url
        response = self.client.get(
            "https://www.solita.fi/wp-json/wp/v2/positions?per_page=100&_fields=title,link,roles,countries"
        )
        positions = response.json()
        links: list[DiscoveredJobLink] = []
        for position in positions:
            countries = position.get("countries", [])
            if not any(country.get("slug") == "denmark" for country in countries):
                continue
            link = position.get("link")
            if not isinstance(link, str) or not SOLITA_CONTRACT.allows(link):
                continue
            links.append(DiscoveredJobLink(
                url=link,
                title=unescape(position.get("title", {}).get("rendered", "")),
                external_job_id=solita_external_id(link),
                metadata={
                    "area": [unescape(role.get("name", "")) for role in position.get("roles", [])],
                    "country": [unescape(country.get("name", "")) for country in countries],
                },
            ))
        return DiscoveryResult(
            source_id=SOLITA_CONTRACT.source_id,
            links=deduplicate_links(links, SOLITA_CONTRACT.max_jobs),
            pages_fetched=self.client.pages_fetched,
        )
