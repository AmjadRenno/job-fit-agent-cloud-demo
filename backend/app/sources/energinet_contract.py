"""BLOCKED Energinet contract for the delegated HR-Manager ATS.

Status: BLOCKED / disabled / execution NOT_READY.

BLOCKED_REASON (2026-09-17):
    "BLOCKED — job listing path (/vacancies/list.aspx) is disallowed by the
    live robots.txt at candidate.hr-manager.net; no compliant discovery path
    was found."

The live robots.txt at candidate.hr-manager.net is:

    User-agent: *
    Allow: /ApplicationInit.aspx?
    Disallow: /ApplicationInit.aspx?*SkipAdvertisement=True*
    Disallow: /

The only job-listing path Energinet exposes is the HR-Manager iframe at
``/vacancies/list.aspx?customer=Energinet`` (embedded in
``https://www.energinet.dk/karriere/ledige-job/``). That path is disallowed by
the real robots.txt (``Disallow: /``), and the single allowed path
(``/ApplicationInit.aspx?``) is an individual advertisement page that requires
a known ``ProjectId`` and cannot enumerate vacancies. There is therefore no
compliant discovery path, and the source must not be fetched.

Retained on disk (archived, not deleted) so the block is explicit and the
future reader sees why. This contract must never be re-enabled without a
robots-compliant listing path.
"""

from __future__ import annotations

from .discovery import SourceContract

BLOCKED_REASON = (
    "BLOCKED — job listing path (/vacancies/list.aspx) is disallowed by the "
    "live robots.txt at candidate.hr-manager.net; no compliant discovery path "
    "was found."
)

ENERGINET_DRAFT_CONTRACT = SourceContract(
    source_id="energinet_dk",
    careers_url="https://www.energinet.dk/karriere/ledige-job/",
    allowed_domains=frozenset({"www.energinet.dk", "energinet.dk", "candidate.hr-manager.net"}),
    allowed_paths=("/karriere/", "/jobs/", "/vacancies/", "/ApplicationInit.aspx"),
    denied_paths=(
        "/vacancies/apply.aspx",
        "/vacancies/Apply.aspx",
        "/apply",
        "/services/",
    ),
    delegated_ats_domain="candidate.hr-manager.net",
    delegated_ats_provider="hr_manager",
    # Tenant pinning is path-aware: the legacy listing path uses
    # ``customer=Energinet`` while the canonical vacancy/advertisement page
    # (``/ApplicationInit.aspx``) identifies the Energinet tenant via
    # ``cid=316``. ``contract.allows()`` enforces only the parameter that
    # matches the request path, so requesting one scheme must not require
    # the other scheme's parameter.
    pinned_tenant_params={"customer": "Energinet", "cid": "316"},
    crawl_delay_seconds=10.0,
    max_pages=3,
    max_jobs=20,
    enabled=False,
    approval_status="BLOCKED",
)
