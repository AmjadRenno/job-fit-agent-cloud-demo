from __future__ import annotations

from dataclasses import replace
import html
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.analysis.context import (
    ANALYSIS_ELIGIBLE_SOURCES,
    AnalysisPreconditionError,
    CandidateProfile,
    build_context,
)
from backend.app.analysis.service import JobAnalysisService
from backend.app.db.models import (
    Base,
    Job,
    JobAnalysis as JobAnalysisRow,
    JobSource,
    MatchResult as MatchResultRow,
    SourcePreview,
)
from backend.app.matching.service import (
    MATCHING_ELIGIBLE_SOURCES,
    JobMatchingService,
    MatchingPreconditionError,
)
from backend.app.workflow.processing import JobProcessingService
from backend.app.sources.discovery import (
    DiscoveredJobLink,
    DiscoveryError,
    SafeSourceClient,
    SourceBlockedError,
    SourceContract,
)
from backend.app.sources.energinet_contract import ENERGINET_DRAFT_CONTRACT
from backend.app.sources.extractors import hr_manager_external_id
from backend.app.sources.hr_manager_discovery import (
    HrManagerDiscovery,
    HrManagerSourceHandler,
    _HrManagerListingParser,
)
from backend.app.sources.onboarding import OnboardingError, OnboardingTrialService
from backend.app.workflow.sources import resolve_source_handler


class MockResponse:
    def __init__(self, text: str, status_code: int = 200, url: str | None = None, headers: dict[str, str] | None = None) -> None:
        self.text = text
        self.status_code = status_code
        self.url = url
        self.headers = headers or {}


class MockTransport:
    """Serves the live robots.txt separately from recorded content fetches."""

    def __init__(self, responses: dict[str, MockResponse] | None = None) -> None:
        self.responses = responses or {}
        self.requested_urls: list[str] = []
        self.robots_urls: list[str] = []

    def get(self, url: str) -> MockResponse:
        if url.endswith("/robots.txt"):
            self.robots_urls.append(url)
            if url in self.responses:
                return self.responses[url]
            return MockResponse("User-agent: *\nAllow: /\n", 200, url=url)
        self.requested_urls.append(url)
        if url in self.responses:
            return self.responses[url]
        # Return empty 200 by default
        return MockResponse("<html><body></body></html>", 200, url=url)


SAMPLE_LISTING_HTML = """
<html>
<body>
  <h1>Ledige stillinger hos Energinet</h1>
  <div class="vacancies">
    <a href="vacancy.aspx?customer=Energinet&id=101">Senior .NET Udvikler</a>
    <a href="/vacancies/vacancy.aspx?customer=Energinet&id=102">Data Engineer</a>
    <!-- Cross-tenant vacancy link -->
    <a href="vacancy.aspx?customer=OtherTenant&id=999">Anden virksomhed</a>
    <!-- Missing customer parameter -->
    <a href="vacancy.aspx?id=888">Uden kunde param</a>
    <!-- Denied application link -->
    <a href="/vacancies/apply.aspx?customer=Energinet&id=101">Ansøg her</a>
    <!-- External link -->
    <a href="https://untrusted-domain.com/jobs/1">Eksternt link</a>
  </div>
</body>
</html>
"""

SAMPLE_DETAIL_HTML = """
<html>
<body>
  <h1>Senior .NET Udvikler</h1>
  <div class="job-description">
    <p>Vi søger en dygtig .NET udvikler til Energinet i Fredericia.</p>
    <p>Primære opgaver: C#, Azure, SQL.</p>
  </div>
</body>
</html>
"""


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def active_energinet_contract() -> SourceContract:
    return replace(ENERGINET_DRAFT_CONTRACT, enabled=True, approval_status="APPROVED")


def test_contract_allows_correct_customer_parameter():
    contract = active_energinet_contract()
    valid_url = "https://candidate.hr-manager.net/vacancies/vacancy.aspx?customer=Energinet&id=101"
    assert contract.allows(valid_url) is True


def test_contract_rejects_wrong_or_missing_customer_parameter():
    contract = active_energinet_contract()
    # Wrong tenant
    wrong_tenant = "https://candidate.hr-manager.net/vacancies/vacancy.aspx?customer=OtherTenant&id=101"
    assert contract.allows(wrong_tenant) is False

    # Missing customer parameter
    missing_tenant = "https://candidate.hr-manager.net/vacancies/vacancy.aspx?id=101"
    assert contract.allows(missing_tenant) is False


def test_handler_rejects_job_from_a_different_hr_manager_cid_before_any_fetch():
    """Security boundary: a vacancy URL carrying another HR-Manager tenant's
    ``cid`` (anything other than Energinet's pinned ``cid=316``) must be
    rejected by the contract and by HrManagerSourceHandler before any network
    fetch, so a cross-tenant job can never reach storage or analysis."""
    contract = active_energinet_contract()
    foreign_cid_url = (
        "https://candidate.hr-manager.net/ApplicationInit.aspx"
        "?cid=999&ProjectId=188785&DepartmentId=21680&MediaId=5"
    )

    # Tenant boundary check: the contract only admits the pinned Energinet cid.
    assert contract.allows(foreign_cid_url) is False
    # The approved handler is not even resolvable for a foreign-cid advertisement URL.
    assert resolve_source_handler(foreign_cid_url) is None

    # Direct handler path: extraction refuses the URL before any transport call,
    # so a foreign-tenant job cannot be fetched, persisted, or analyzed.
    transport = MockTransport()
    handler = HrManagerSourceHandler(contract=contract)
    with pytest.raises(SourceBlockedError, match="outside source contract"):
        handler.extract_record(foreign_cid_url, transport=transport)
    assert transport.requested_urls == []

    # Control: the same handler/contract still admits the pinned Energinet cid.
    pinned_url = (
        "https://candidate.hr-manager.net/ApplicationInit.aspx"
        "?cid=316&ProjectId=188785&DepartmentId=21680&MediaId=5"
    )
    assert contract.allows(pinned_url) is True
    assert handler.can_handle(pinned_url) is True


def test_contract_rejects_untrusted_ats_domain_and_denied_application_paths():
    contract = active_energinet_contract()
    # Untrusted domain
    untrusted = "https://evil-ats.com/vacancies/vacancy.aspx?customer=Energinet&id=101"
    assert contract.allows(untrusted) is False

    # Denied apply path
    apply_url = "https://candidate.hr-manager.net/vacancies/apply.aspx?customer=Energinet&id=101"
    assert contract.allows(apply_url) is False


LIVE_HR_MANAGER_ROBOTS = (
    "User-agent: *\n"
    "Allow: /ApplicationInit.aspx?\n"
    "Disallow: /ApplicationInit.aspx?*SkipAdvertisement=True*\n"
    "Disallow: /\n"
)


def test_live_hr_manager_robots_blocks_the_listing_path_before_any_request():
    """Regression for the confirmed robots violation.

    Given the real candidate.hr-manager.net robots.txt, requesting the Energinet
    listing path (/vacancies/list.aspx?customer=Energinet) must be blocked by the
    live robots check, and the disallowed URL must never be requested. The
    advertisement page (/ApplicationInit.aspx?) is the only permitted path.
    """
    contract = active_energinet_contract()
    listing_url = "https://candidate.hr-manager.net/vacancies/list.aspx?customer=Energinet"
    advertisement_url = (
        "https://candidate.hr-manager.net/ApplicationInit.aspx"
        "?cid=316&ProjectId=188785&DepartmentId=21680&MediaId=5"
    )
    transport = MockTransport({
        "https://candidate.hr-manager.net/robots.txt": MockResponse(
            LIVE_HR_MANAGER_ROBOTS, 200, url="https://candidate.hr-manager.net/robots.txt"
        ),
        advertisement_url: MockResponse(SAMPLE_DETAIL_HTML, 200, url=advertisement_url),
    })
    client = SafeSourceClient(contract, transport)

    with pytest.raises(SourceBlockedError, match="robots.txt disallows URL"):
        client.get(listing_url)

    # Only the live robots.txt was fetched; the disallowed listing was never requested.
    assert transport.robots_urls == ["https://candidate.hr-manager.net/robots.txt"]
    assert listing_url not in transport.requested_urls
    assert transport.requested_urls == []

    # Positive control: the one permitted path is still allowed by the same policy.
    assert client.get(advertisement_url).text == SAMPLE_DETAIL_HTML
    assert transport.requested_urls == [advertisement_url]


def test_safe_source_client_enforces_live_ats_robots_policy():
    contract = active_energinet_contract()
    private_url = "https://candidate.hr-manager.net/vacancies/private/job.aspx?customer=Energinet"
    transport = MockTransport({
        "https://candidate.hr-manager.net/robots.txt": MockResponse(
            "User-agent: *\nDisallow: /vacancies/private/\n", 200,
            url="https://candidate.hr-manager.net/robots.txt",
        ),
        private_url: MockResponse("private", 200),
    })
    client = SafeSourceClient(contract, transport)

    with pytest.raises(SourceBlockedError, match="robots.txt disallows URL"):
        client.get(private_url)
    assert transport.requested_urls == []


def test_safe_source_client_rejects_redirect_to_untrusted_or_cross_tenant_destination():
    contract = active_energinet_contract()
    # Redirect to another domain
    transport_external = MockTransport({
        "https://candidate.hr-manager.net/vacancies/list.aspx?customer=Energinet": MockResponse(
            "redirected",
            status_code=200,
            url="https://evil-tracker.com/listing",
        ),
    })
    client1 = SafeSourceClient(contract, transport_external)
    with pytest.raises(SourceBlockedError, match="redirect leaves source contract"):
        client1.get("https://candidate.hr-manager.net/vacancies/list.aspx?customer=Energinet")

    # Redirect to another tenant on HR-Manager
    transport_cross_tenant = MockTransport({
        "https://candidate.hr-manager.net/vacancies/list.aspx?customer=Energinet": MockResponse(
            "redirected",
            status_code=200,
            url="https://candidate.hr-manager.net/vacancies/list.aspx?customer=OtherCompany",
        ),
    })
    client2 = SafeSourceClient(contract, transport_cross_tenant)
    with pytest.raises(SourceBlockedError, match="redirect leaves source contract"):
        client2.get("https://candidate.hr-manager.net/vacancies/list.aspx?customer=Energinet")


def test_hr_manager_discovery_parses_listing_and_filters_strictly():
    contract = active_energinet_contract()
    transport = MockTransport({
        "https://candidate.hr-manager.net/vacancies/list.aspx?customer=Energinet": MockResponse(
            SAMPLE_LISTING_HTML,
            status_code=200,
            url="https://candidate.hr-manager.net/vacancies/list.aspx?customer=Energinet",
        ),
    })
    client = SafeSourceClient(contract, transport)
    discovery = HrManagerDiscovery(client, tenant="Energinet")
    result = discovery.discover()

    assert result.source_id == "energinet_dk"
    assert len(result.links) == 2
    urls = [link.url for link in result.links]
    assert "https://candidate.hr-manager.net/vacancies/vacancy.aspx?customer=Energinet&id=101" in urls
    assert "https://candidate.hr-manager.net/vacancies/vacancy.aspx?customer=Energinet&id=102" in urls
    # Ensure cross-tenant, untrusted, and application links were filtered out
    for link in result.links:
        assert "OtherTenant" not in link.url
        assert "apply.aspx" not in link.url
        assert "untrusted-domain" not in link.url


def test_hr_manager_handler_discover_jobs_and_extract_record():
    contract = active_energinet_contract()
    listing_url = "https://candidate.hr-manager.net/vacancies/list.aspx?customer=Energinet"
    detail_url = "https://candidate.hr-manager.net/vacancies/vacancy.aspx?customer=Energinet&id=101"
    transport = MockTransport({
        listing_url: MockResponse(SAMPLE_LISTING_HTML, 200, url=listing_url),
        detail_url: MockResponse(SAMPLE_DETAIL_HTML, 200, url=detail_url),
        "https://candidate.hr-manager.net/vacancies/vacancy.aspx?customer=Energinet&id=102": MockResponse(
            "<html><body><h1>Data Engineer</h1><p>Description</p></body></html>", 200
        ),
    })
    handler = HrManagerSourceHandler(contract=contract)
    assert handler.execution_ready is True

    records = handler.discover_jobs(listing_url, transport=transport)
    assert len(records) == 2
    first = records[0]
    assert first["source"] == "energinet_dk"
    assert first["title"] == "Senior .NET Udvikler"
    assert first["status"] == "unknown"
    assert first["external_job_id"] == "101"
    assert "C#, Azure, SQL" in first["description"]


def test_onboarding_blocked_energinet_fails_closed_before_any_fetch(session):
    """Energinet is BLOCKED: the live robots.txt at candidate.hr-manager.net
    disallows its only listing path (/vacancies/list.aspx), so onboarding must
    fail closed (POLICY_UNRESOLVED) before any fetch. The HR-Manager ATS must
    never be contacted, and no source/preview state may be created."""
    careers_url = "https://www.energinet.dk/karriere/ledige-job/"
    transport = MockTransport()

    service = OnboardingTrialService(session, transport=transport)
    with pytest.raises(OnboardingError) as excinfo:
        service.run(
            careers_url,
            company_name="Energinet",
            company_domain="energinet.dk",
            source_id="energinet_dk",
        )
    assert excinfo.value.code == "POLICY_UNRESOLVED"
    assert excinfo.value.failure.safety_blocked is True
    assert "BLOCKED" in str(excinfo.value)

    # Request-log evidence: not a single HTTP request was made — the blocked
    # contract is refused before discovery, so no robots.txt or page fetch
    # (and in particular nothing on candidate.hr-manager.net) occurred.
    assert transport.requested_urls == []
    assert transport.robots_urls == []
    assert session.query(JobSource).count() == 0


def test_blocked_energinet_source_can_never_be_approved_or_activated(session):
    """The BLOCKED contract cannot be smuggled through approval: onboarding
    fails closed, so no preview exists that could satisfy the activation gate,
    and the persisted source stays disabled."""
    from backend.app.db.import_json import SOURCE_DEFAULTS
    from backend.app.db.repository import ensure_source
    from backend.app.sources_management.service import SourceManagementError, SourceManagementService

    # A pre-existing persisted row keeps the disabled/blocked defaults.
    source = ensure_source(session, source_id="energinet_dk", **dict(SOURCE_DEFAULTS["energinet_dk"]))
    session.flush()
    assert source.enabled is False

    careers_url = "https://www.energinet.dk/karriere/ledige-job/"
    transport = MockTransport()
    service = OnboardingTrialService(session, transport=transport)
    with pytest.raises(OnboardingError) as excinfo:
        service.run(
            careers_url,
            company_name="Energinet",
            company_domain="energinet.dk",
            source_id="energinet_dk",
        )
    assert excinfo.value.code == "POLICY_UNRESOLVED"
    # The robots-blocked ATS was never contacted during the refused onboarding.
    assert all("candidate.hr-manager.net" not in url for url in transport.requested_urls)

    # No preview exists for the blocked source, so the activation gate cannot
    # be satisfied and human approval is refused.
    assert session.query(SourcePreview).filter_by(source_id=source.id).all() == []
    source_mgmt = SourceManagementService(session)
    with pytest.raises((SourceManagementError, LookupError)):
        # approve() refuses: no preview exists for the blocked source
        # (SourceManagementError), or the preview lookup fails outright
        # (LookupError). Either way the source can never become ACTIVE.
        source_mgmt.approve(source.id, 1)
    assert source.lifecycle_status != "ACTIVE"
    assert source.enabled is False


def test_daily_run_excludes_inactive_energinet_and_fails_closed_if_activated(session, tmp_path):
    from backend.app.db.import_json import SOURCE_DEFAULTS
    from scripts.run_daily_search import DailyRun
    from scripts.source_registry import resolve_source_runner

    # 1. When Energinet is PREVIEW_READY / enabled=False, DailyRun excludes it
    source = session.query(JobSource).filter_by(source_id="energinet_dk").first()
    if not source:
        from backend.app.db.repository import ensure_source
        source = ensure_source(session, source_id="energinet_dk", **dict(SOURCE_DEFAULTS.get("energinet_dk", {})))
        session.flush()
    source.lifecycle_status = "PREVIEW_READY"
    source.enabled = False
    session.commit()

    summary1 = DailyRun(session, tmp_path).execute()
    assert not any(s["source"] == "energinet_dk" for s in summary1["sources_attempted"])

    # 2. Energinet is BLOCKED and has no registered DailyRun runner. Even if the
    # persisted row were flipped to ACTIVE/enabled, the run must fail closed:
    # the source is attempted but fails with UnsupportedSourceRunnerError
    # before any fetch to the robots-disallowed ATS can occur.
    assert resolve_source_runner("energinet_dk") is None
    source.lifecycle_status = "ACTIVE"
    source.enabled = True
    session.commit()

    summary2 = DailyRun(session, tmp_path).execute()
    attempted = [s for s in summary2["sources_attempted"] if s["source"] == "energinet_dk"]
    assert len(attempted) == 1
    assert attempted[0]["status"] == "FAILED"
    assert any(
        f["source"] == "energinet_dk" and f["code"] == "UnsupportedSourceRunnerError"
        for f in summary2["failures"]
    )


def test_energinet_urls_are_not_resolved_to_an_execution_handler():
    """Energinet is BLOCKED (live robots.txt disallows its only listing path),
    so none of its URLs may resolve to a fetch-capable execution handler."""
    assert resolve_source_handler("https://www.energinet.dk/karriere/ledige-job/") is None
    assert resolve_source_handler("https://candidate.hr-manager.net/vacancies/list.aspx?customer=Energinet") is None
    assert resolve_source_handler(
        "https://candidate.hr-manager.net/ApplicationInit.aspx?cid=316&ProjectId=188785&DepartmentId=21680&MediaId=5"
    ) is None
    assert resolve_source_handler("https://random-company.dk/careers") is None

    # The HR-Manager handler class still exists for audit/tests, but it must not
    # be part of the approved execution registry.
    from backend.app.workflow.sources import APPROVED_HANDLERS
    assert all(handler.source_id != "energinet_dk" for handler in APPROVED_HANDLERS)


def test_hr_manager_external_id_helper():
    assert hr_manager_external_id("https://candidate.hr-manager.net/vacancies/vacancy.aspx?customer=Energinet&id=14552") == "14552"
    assert hr_manager_external_id("https://candidate.hr-manager.net/vacancies/vacancy.aspx?customer=Energinet&vacancyId=98765") == "98765"
    assert hr_manager_external_id("https://candidate.hr-manager.net/ApplicationInit.aspx?cid=316&ProjectId=188785&DepartmentId=21680&MediaId=5") == "188785"
    assert hr_manager_external_id("https://candidate.hr-manager.net/vacancies/list.aspx?customer=Energinet") is None
# ---------------------------------------------------------------------------
# HR-Manager embedded JSON (HiddenField_PositionList) + transport defaults
# ---------------------------------------------------------------------------


def _embedded_position_payload(items: list[dict] | None = None, alias: str | None = "energinet") -> dict:
    items = items or []
    payload = {
        "DepartmentList": {"Items": []},
        "PositionCategoryList": {"Items": []},
        "PositionLocationList": {"Items": []},
        "PositionList": {"PositionCountList": len(items), "Items": items},
        "CustomerAlias": alias,
        "CustomerName": "Energinet",
    }
    if alias is not None:
        payload["PositionList"]["CustomerAlias"] = alias
    else:
        payload["PositionList"].pop("CustomerAlias", None)
        payload.pop("CustomerAlias", None)
    return payload


def _embedded_listing_html(payload: dict) -> str:
    encoded = html.escape(json.dumps(payload), quote=True)
    return (
        '<html><body><h1>Ledige stillinger</h1>'
        '<input type="hidden" '
        'id="ctl00_ctl00_ContentPlaceHolder_MainContent_ContentPlaceHolder_PageContent_ctl00_HiddenField_PositionList" '
        f'value="{encoded}" /></body></html>'
    )


def _embedded_listing_for(items: list[dict] | None = None, alias: str | None = "energinet") -> str:
    return _embedded_listing_html(_embedded_position_payload(items, alias))


LISTING_URL = "https://candidate.hr-manager.net/vacancies/list.aspx?customer=Energinet"


def test_hr_manager_handler_defaults_transport_when_none(monkeypatch):
    from backend.app.sources import phase1_trial

    detail_url_1 = "https://candidate.hr-manager.net/vacancies/vacancy.aspx?customer=Energinet&id=101"
    detail_url_2 = "https://candidate.hr-manager.net/vacancies/vacancy.aspx?customer=Energinet&id=102"
    transport = MockTransport({
        LISTING_URL: MockResponse(SAMPLE_LISTING_HTML, 200, url=LISTING_URL),
        detail_url_1: MockResponse(SAMPLE_DETAIL_HTML, 200, url=detail_url_1),
        detail_url_2: MockResponse("<html><body><h1>Data Engineer</h1><p>Description</p></body></html>", 200, url=detail_url_2),
    })
    monkeypatch.setattr(phase1_trial, "_RequestsTransport", lambda: transport)

    handler = HrManagerSourceHandler(contract=active_energinet_contract())
    records = handler.discover_jobs(LISTING_URL)  # transport deliberately omitted
    assert len(records) == 2
    assert transport.requested_urls[0] == LISTING_URL
    assert detail_url_1 in transport.requested_urls
    assert detail_url_2 in transport.requested_urls

    # extract_record() also defaults the transport when html_content is absent.
    handler2 = HrManagerSourceHandler(contract=active_energinet_contract())
    record = handler2.extract_record(detail_url_1)
    assert record["source"] == "energinet_dk"
    assert record["external_job_id"] == "101"


def test_embedded_position_list_json_extracts_valid_vacancy_ids():
    handler = HrManagerSourceHandler(contract=active_energinet_contract())
    client = SafeSourceClient(handler._active_contract(), MockTransport())
    discovery = HrManagerDiscovery(client, tenant="Energinet")
    listings_html = _embedded_listing_for([
        {"Id": 14552, "Name": "Senior .NET Udvikler"},
        {"Id": 14553, "Name": "Data Engineer"},
    ])
    links = discovery._embedded_links(listings_html, LISTING_URL)
    assert [link.external_job_id for link in links] == ["14552", "14553"]
    assert [link.title for link in links] == ["Senior .NET Udvikler", "Data Engineer"]
    assert [link.url for link in links] == [
        "https://candidate.hr-manager.net/ApplicationInit.aspx?cid=316&ProjectId=14552&MediaId=5",
        "https://candidate.hr-manager.net/ApplicationInit.aspx?cid=316&ProjectId=14553&MediaId=5",
    ]


def test_embedded_position_list_entity_encoded_json_is_decoded():
    client = SafeSourceClient(active_energinet_contract(), MockTransport())
    discovery = HrManagerDiscovery(client, tenant="Energinet")
    payload = _embedded_position_payload([{"Id": 2001, "Name": "Test & Design Engineer"}], alias="energinet")
    raw_json = json.dumps(payload)
    encoded = html.escape(raw_json, quote=True)
    assert "&quot;" in encoded and "&amp;" in encoded
    listing = (
        '<input type="hidden" '
        'id="ctl00_ctl00_ContentPlaceHolder_MainContent_ContentPlaceHolder_PageContent_ctl00_HiddenField_PositionList" '
        f'value="{encoded}" />'
    )
    links = discovery._embedded_links(listing, LISTING_URL)
    assert [link.external_job_id for link in links] == ["2001"]
    assert links[0].title == "Test & Design Engineer"


def test_embedded_position_list_rejects_wrong_or_missing_customer_alias():
    client = SafeSourceClient(active_energinet_contract(), MockTransport())
    discovery = HrManagerDiscovery(client, tenant="Energinet")

    with pytest.raises(SourceBlockedError):
        discovery._embedded_links(
            _embedded_listing_for([{"Id": 1, "Name": "X"}], alias="OtherTenant"),
            LISTING_URL,
        )

    payload = _embedded_position_payload([{"Id": 1, "Name": "X"}], alias=None)
    with pytest.raises(SourceBlockedError):
        discovery._embedded_links(_embedded_listing_html(payload), LISTING_URL)
def test_embedded_position_list_rejects_cross_tenant_and_application_urls():
    client = SafeSourceClient(active_energinet_contract(), MockTransport())
    discovery = HrManagerDiscovery(client, tenant="Energinet")
    items = [
        {"Id": 101, "Name": "Valid role"},
        {
            "Id": 102,
            "Name": "Cross-tenant URL",
            "Url": "https://candidate.hr-manager.net/vacancies/vacancy.aspx?customer=OtherTenant&id=102",
        },
        {
            "Id": 103,
            "Name": "Application init URL without tenant pin",
            "Url": "https://candidate.hr-manager.net/ApplicationInit.aspx?cid=999&ProjectId=103&MediaId=5",
        },
        {
            "Id": 104,
            "Name": "Apply URL",
            "Url": "https://candidate.hr-manager.net/vacancies/apply.aspx?customer=Energinet&id=104",
        },
    ]
    links = discovery._embedded_links(_embedded_listing_for(items), LISTING_URL)
    assert [link.external_job_id for link in links] == ["101"]


def test_embedded_position_list_malformed_json_fails_safely():
    client = SafeSourceClient(active_energinet_contract(), MockTransport())
    discovery = HrManagerDiscovery(client, tenant="Energinet")

    hidden_input = (
        'id="ctl00_ctl00_ContentPlaceHolder_MainContent_ContentPlaceHolder_PageContent_ctl00_HiddenField_PositionList" '
    )
    malformed = f'<input type="hidden" {hidden_input}value="{{not valid json}}" />'
    with pytest.raises(DiscoveryError):
        discovery._embedded_links(malformed, LISTING_URL)

    non_object = f'<input type="hidden" {hidden_input}value="[1,2,3]" />'
    with pytest.raises(DiscoveryError):
        discovery._embedded_links(non_object, LISTING_URL)

    absent = "<html><body><p>no hidden field</p></body></html>"
    assert discovery._embedded_links(absent, LISTING_URL) == []


def test_discover_falls_back_to_anchors_when_no_embedded_position_field():
    contract = active_energinet_contract()
    transport = MockTransport({LISTING_URL: MockResponse(SAMPLE_LISTING_HTML, 200, url=LISTING_URL)})
    client = SafeSourceClient(contract, transport)
    result = HrManagerDiscovery(client, tenant="Energinet").discover()
    assert [link.external_job_id for link in result.links] == ["101", "102"]


    # NOTE: the former test_onboarding_embedded_listing_produces_execution_
    # ready_preview was removed: its premise (the embedded HR-Manager listing
    # producing an execution-ready Energinet preview) is exactly the path that
    # live robots.txt disallows. Coverage now lives in
    # test_onboarding_blocked_energinet_fails_closed_before_any_fetch and
    # test_blocked_energinet_source_can_never_be_approved_or_activated.


def test_legacy_energinet_jobs_url_is_not_resolved_to_a_fetch_capable_handler():
    """The legacy /jobs/ path is equally robots-blocked on the ATS host; it
    must not resolve to any fetch-capable execution handler."""
    assert resolve_source_handler("https://www.energinet.dk/jobs/") is None



def test_embedded_position_list_builds_canonical_application_init_urls():
    """Live Energinet listing carries no vacancy.aspx links.

    The page JS reads ``AdvertisementUrlSecure`` for the position link, so the
    parser must construct the canonical ``/ApplicationInit.aspx`` vacancy URL
    (pinned ``cid=316`` + ``ProjectId``) instead of the legacy
    ``/vacancies/vacancy.aspx`` form that 404-redirects to Error404.html.
    """
    client = SafeSourceClient(active_energinet_contract(), MockTransport())
    discovery = HrManagerDiscovery(client, tenant="Energinet")
    items = [
        {
            "Id": 188785,
            "Name": "Administrationselev til Energinets kontrolcenter",
            "AdvertisementUrlSecure": "https://candidate.hr-manager.net/ApplicationInit.aspx?cid=316&ProjectId=188785&DepartmentId=21680&MediaId=5",
        },
        {"Id": 188731, "Name": "Bliv transformer-specialist"},
    ]
    links = discovery._embedded_links(_embedded_listing_for(items), LISTING_URL)
    assert [link.external_job_id for link in links] == ["188785", "188731"]
    assert links[0].url == (
        "https://candidate.hr-manager.net/ApplicationInit.aspx?cid=316&ProjectId=188785&DepartmentId=21680&MediaId=5"
    )
    assert links[1].url == (
        "https://candidate.hr-manager.net/ApplicationInit.aspx?cid=316&ProjectId=188731&MediaId=5"
    )
    for link in links:
        assert "vacancy.aspx" not in link.url
        assert active_energinet_contract().allows(link.url) is True


def test_legacy_vacancy_url_error404_redirect_is_blocked():
    """The reported onboarding failure: legacy vacancy.aspx 404-redirects to
    Error404.html, which drops the pinned tenant param and must stay blocked.
    """
    contract = active_energinet_contract()
    legacy_url = "https://candidate.hr-manager.net/vacancies/vacancy.aspx?customer=Energinet&id=101"
    assert contract.allows(legacy_url) is True
    error_url = "https://candidate.hr-manager.net/Error404.html?aspxerrorpath=/vacancies/vacancy.aspx"
    assert contract.allows(error_url) is False
    transport = MockTransport({
        legacy_url: MockResponse("redirected", status_code=200, url=error_url),
    })
    client = SafeSourceClient(contract, transport)
    with pytest.raises(SourceBlockedError, match="redirect leaves source contract"):
        client.get(legacy_url)


def test_canonical_application_init_redirect_stays_in_contract():
    """A same-tenant redirect on the canonical advertisement page is followed."""
    contract = active_energinet_contract()
    start = "https://candidate.hr-manager.net/ApplicationInit.aspx?cid=316&ProjectId=188785&DepartmentId=21680&MediaId=5"
    same_tenant = "https://candidate.hr-manager.net/ApplicationInit.aspx?cid=316&ProjectId=188785&MediaId=5"
    assert contract.allows(same_tenant) is True
    transport = MockTransport({
        start: MockResponse("<html><body><h1>Job</h1></body></html>", status_code=200, url=same_tenant),
    })
    response = SafeSourceClient(contract, transport).get(start)
    assert response.url == same_tenant


# ---------------------------------------------------------------------------
# Energinet analysis/matching eligibility slice (allowlist synced with the
# approved source lifecycle).
#
# Energinet is archived and has no approved execution handler. Historical jobs
# remain stored, but are excluded from analysis and matching regardless of
# their recorded availability.
# ---------------------------------------------------------------------------

PROFILE = Path("data/candidate/profile.md")
ENERGINET_APPLICATION_INIT_URL = (
    "https://candidate.hr-manager.net/ApplicationInit.aspx"
    "?cid=316&ProjectId=188785&DepartmentId=21680&MediaId=5"
)


class _CountingAnalyzer:
    """Counts invocations; a blocked job must never reach the analyzer."""

    model = "counting-energinet-analyzer"

    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, context):  # pragma: no cover - must not be reachable
        self.calls += 1
        raise AssertionError("analysis must not run for a blocked job")


def _job_source(session: Session, source_id: str) -> JobSource:
    if source_id == "energinet_dk":
        source = JobSource(
            source_id="energinet_dk",
            display_name="Energinet Denmark",
            official_jobs_url="https://www.energinet.dk/karriere/ledige-job/",
            allowed_domains=["www.energinet.dk", "energinet.dk", "candidate.hr-manager.net"],
            crawl_delay_seconds=10,
            enabled=True,
        )
    else:
        source = JobSource(
            source_id=source_id,
            display_name=source_id.replace("_", " ").title(),
            official_jobs_url="https://example.invalid/",
            allowed_domains=["example.invalid"],
            crawl_delay_seconds=10,
            enabled=True,
        )
    session.add(source)
    session.flush()
    return source


def _job(
    session: Session,
    source_id: str,
    url: str,
    *,
    availability_status: str = "UNKNOWN",
    country_code: str = "DK",
) -> Job:
    source = _job_source(session, source_id)
    job = Job(
        source_id=source.id,
        title="Senior .NET Udvikler",
        source_job_url=url,
        description="C#, Azure, SQL.",
        country_code=country_code,
        employment_type="UNKNOWN",
        availability_status=availability_status,
    )
    session.add(job)
    session.flush()
    return job


def test_archived_energinet_source_is_excluded_at_the_allowlist_layer(session):
    assert "energinet_dk" not in ANALYSIS_ELIGIBLE_SOURCES
    assert "energinet_dk" not in MATCHING_ELIGIBLE_SOURCES

    job = _job(
        session,
        "energinet_dk",
        ENERGINET_APPLICATION_INIT_URL,
        availability_status="ACTIVE",
    )
    with pytest.raises(AnalysisPreconditionError, match="not approved for analysis"):
        build_context(job, CandidateProfile.load(PROFILE))


def test_energinet_unknown_availability_is_still_blocked_before_analysis(session):
    job = _job(session, "energinet_dk", ENERGINET_APPLICATION_INIT_URL)
    analyzer = _CountingAnalyzer()

    with pytest.raises(AnalysisPreconditionError) as error:
        JobAnalysisService(session, analyzer).analyze_job(job.id, PROFILE)

    assert "not approved for analysis" in str(error.value)
    assert analyzer.calls == 0


def test_energinet_unknown_availability_cannot_produce_a_match(session):
    job = _job(session, "energinet_dk", ENERGINET_APPLICATION_INIT_URL)
    with pytest.raises(MatchingPreconditionError, match="not approved for matching"):
        JobMatchingService(session).match_job(job.id, PROFILE)
    assert session.query(MatchResultRow).count() == 0


def test_energinet_unknown_record_is_persisted_but_never_analyzed_or_matched(session):
    _job_source(session, "energinet_dk")
    processing = JobProcessingService(
        session, analyzer=_CountingAnalyzer(), profile_path=PROFILE
    )
    result = processing.process_record({
        "source": "energinet_dk",
        "title": "Senior .NET Udvikler",
        "url": ENERGINET_APPLICATION_INIT_URL,
        "description": "C#, Azure, SQL.",
        "location": "",
        "country": ["Denmark"],
        "employment_type": "unknown",
        "status": "unknown",
    })

    assert result.job.availability_status == "UNKNOWN"
    assert result.analysis_performed is False
    assert result.match_performed is False
    assert session.query(JobAnalysisRow).count() == 0
    assert session.query(MatchResultRow).count() == 0


def test_existing_source_eligibility_and_availability_gate_are_unchanged(session):
    assert {"solita_dk", "vestas_dk"} <= ANALYSIS_ELIGIBLE_SOURCES
    assert {"solita_dk", "vestas_dk"} <= MATCHING_ELIGIBLE_SOURCES
    assert "netcompany_dk" not in ANALYSIS_ELIGIBLE_SOURCES
    assert "netcompany_dk" not in MATCHING_ELIGIBLE_SOURCES

    active = _job(session, "solita_dk", "https://www.solita.fi/positions/kept-1/",
                  availability_status="ACTIVE")
    assert build_context(active, CandidateProfile.load(PROFILE)).source == "solita_dk"

    unknown = _job(session, "vestas_dk", "https://careers.vestas.com/job/Kept/9000000009/")
    with pytest.raises(AnalysisPreconditionError, match="not explicitly available"):
        build_context(unknown, CandidateProfile.load(PROFILE))

    # Energinet is excluded at the source gate even for a historical UNKNOWN job.
    unknown_energinet = _job(session, "energinet_dk", ENERGINET_APPLICATION_INIT_URL)
    with pytest.raises(AnalysisPreconditionError, match="not approved for analysis"):
        build_context(unknown_energinet, CandidateProfile.load(PROFILE))
def test_hr_manager_cross_tenant_cid_rejected_before_fetch():
    """Security: a vacancy URL carrying another tenant's cid (anything other than the pinned cid=316) must be rejected before any network fetch."""
    cross_tenant_url = (
        "https://candidate.hr-manager.net/ApplicationInit.aspx"
        "?cid=999&ProjectId=188785&DepartmentId=21680&MediaId=5"
    )
    # The contract only admits the pinned Energinet cid.
    assert ENERGINET_DRAFT_CONTRACT.allows(cross_tenant_url) is False
    # The approved handler is not even resolvable for a foreign-cid advertisement URL.
    assert resolve_source_handler(cross_tenant_url) is None
