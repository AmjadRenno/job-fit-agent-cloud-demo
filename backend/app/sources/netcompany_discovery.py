from __future__ import annotations

from .discovery import DiscoveryResult, SourceBlockedError, SourceContract

NETCOMPANY_CONTRACT = SourceContract(
    source_id="netcompany_dk",
    careers_url="https://netcompany.com/careers/",
    allowed_domains=frozenset({"netcompany.com", "www.netcompany.com"}),
    allowed_paths=("/careers/", "/job/"),
    max_pages=20,
    max_jobs=50,
)


class NetcompanyDiscovery:
    """Inspection-only strategy until a human-approved source contract exists."""

    def discover(self) -> DiscoveryResult:
        raise SourceBlockedError(
            "Netcompany acquisition is blocked: robots.txt returned HTTP 404 and usage permission is unresolved"
        )
