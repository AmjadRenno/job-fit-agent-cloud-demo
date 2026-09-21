from __future__ import annotations


def normalize_company_domain(value: str) -> str:
    """Return the stable company-domain identity used for deduplication."""
    domain = value.strip().casefold().rstrip(".")
    return domain.removeprefix("www.")
