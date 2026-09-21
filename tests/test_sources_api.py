from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import os
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.db.models import Base, Company, Job, JobSource, SourcePreview
from backend.app.main import app
from backend.app.sources.onboarding import OnboardingError, OnboardingFailure
from backend.app.sources_management.api import get_session as sources_get_session, service as sources_service
from backend.app.sources_management.service import SourceManagementService


class FakeOnboarding:
    calls: list[str] = []
    fail = False

    def __init__(self, session):
        self.session = session

    def run(self, url, *, company_name=None, company_domain=None, country="Denmark"):
        self.calls.append(url)
        source = self.session.query(JobSource).filter_by(source_id="solita_dk").one()
        if self.fail:
            source.lifecycle_status = "ONBOARDING"
            source.readiness_status = "BLOCKED"
            self.session.flush()
            self.session.commit()
            raise OnboardingError(OnboardingFailure("NO_JOBS_DISCOVERED", "no jobs"))
        source.lifecycle_status = "PREVIEW_READY"
        source.readiness_status = "READY"
        source.boundary_fingerprint = "fake-boundary"
        preview = SourcePreview(
            source=source,
            version=1,
            boundary_fingerprint="fake-boundary",
            status="READY",
            validation_summary={
                "source": {"original_url": url, "canonical_url": url},
                "sample_jobs": [{"title": "Role", "url": "https://www.solita.fi/positions/role-1/", "description": "private text"}],
                "readiness": {"level": "HIGH"},
            },
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        self.session.add(preview)
        self.session.flush()
        self.session.commit()
        return preview


@pytest.fixture
def setup_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine)
    FakeOnboarding.calls = []
    FakeOnboarding.fail = False

    def override_session():
        yield session

    def override_service():
        return SourceManagementService(session, onboarding_factory=FakeOnboarding)

    app.dependency_overrides[sources_get_session] = override_session
    app.dependency_overrides[sources_service] = override_service
    os.environ["DASHBOARD_TOKEN"] = "test-token"
    client = TestClient(app)
    try:
        yield client, session
    finally:
        app.dependency_overrides.clear()
        session.close()


def auth(human: bool = False) -> dict[str, str]:
    headers = {"X-Dashboard-Token": "test-token"}
    if human:
        headers["X-Actor-Type"] = "HUMAN"
    return headers


def create_company(client: TestClient) -> dict:
    response = client.post("/api/sources/companies", headers=auth(True), json={"name": "Solita", "domain": "www.solita.fi", "country": "Denmark"})
    assert response.status_code == 201
    return response.json()


def create_source(client: TestClient, company_id: str) -> dict:
    response = client.post(
        f"/api/sources/companies/{company_id}/sources",
        headers=auth(True),
        json={
            "source_id": "solita_dk",
            "display_name": "Solita Denmark",
            "official_jobs_url": "https://www.solita.fi/join-us/?country=denmark",
            "allowed_domains": ["www.solita.fi"],
            "robots_txt_url": "https://www.solita.fi/robots.txt",
            "crawl_delay_seconds": 10,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_read_requires_token_and_mutations_require_human(setup_client):
    client, _ = setup_client
    assert client.get("/api/sources/companies").status_code == 401
    assert client.post("/api/sources/companies", headers=auth(), json={"name": "A", "domain": "a.example", "country": "DK"}).status_code == 403


def test_company_and_source_crud_uses_archive_semantics(setup_client):
    client, session = setup_client
    company = create_company(client)
    source = create_source(client, company["id"])
    assert source["lifecycle_status"] == "DRAFT"
    assert source["enabled"] is False
    assert client.get("/api/sources/companies", headers=auth()).status_code == 200
    patch = client.patch(f"/api/sources/companies/{company['id']}", headers=auth(True), json={"name": "Solita A/S"})
    assert patch.status_code == 200
    assert patch.json()["name"] == "Solita A/S"
    archived = client.delete(f"/api/sources/{source['id']}", headers=auth(True))
    assert archived.status_code == 200
    assert archived.json()["lifecycle_status"] == "ARCHIVED"
    assert archived.json()["enabled"] is False
    company_archived = client.delete(f"/api/sources/companies/{company['id']}", headers=auth(True))
    assert company_archived.status_code == 200
    assert company_archived.json()["lifecycle_status"] == "ARCHIVED"
    assert session.query(Job).count() == 0


def test_source_creation_does_not_onboard_or_activate(setup_client):
    client, _ = setup_client
    company = create_company(client)
    source = create_source(client, company["id"])
    assert source["lifecycle_status"] == "DRAFT"
    assert source["enabled"] is False
    assert FakeOnboarding.calls == []


def test_boundary_patch_requires_revalidation_and_metadata_patch_does_not(setup_client):
    client, session = setup_client
    company = create_company(client)
    source = create_source(client, company["id"])
    metadata = client.patch(f"/api/sources/{source['id']}", headers=auth(True), json={"display_name": "Solita Jobs"})
    assert metadata.status_code == 200
    assert metadata.json()["lifecycle_status"] == "DRAFT"
    boundary = client.patch(f"/api/sources/{source['id']}", headers=auth(True), json={"allowed_domains": ["careers.solita.fi"]})
    assert boundary.status_code == 200
    assert boundary.json()["lifecycle_status"] == "DRAFT"

    row = session.get(JobSource, UUID(source["id"]))
    row.lifecycle_status = "ACTIVE"
    row.enabled = True
    historical_job = Job(source_id=row.id, company_id=row.company_id, title="Historical role", source_job_url="https://www.solita.fi/jobs/historical", availability_status="CLOSED")
    session.add(historical_job)
    session.commit()
    active_boundary = client.patch(f"/api/sources/{source['id']}", headers=auth(True), json={"official_jobs_url": "https://www.solita.fi/careers/"})
    assert active_boundary.status_code == 200
    assert active_boundary.json()["lifecycle_status"] == "REVALIDATION_REQUIRED"
    assert active_boundary.json()["enabled"] is False
    assert session.get(Job, historical_job.id) is not None


def test_onboarding_returns_preview_and_safe_sample_without_activation(setup_client):
    client, _ = setup_client
    company = create_company(client)
    source = create_source(client, company["id"])
    response = client.post(f"/api/sources/{source['id']}/onboarding", headers=auth(True), json={})
    assert response.status_code == 201
    body = response.json()
    assert body["lifecycle_status"] == "PREVIEW_READY"
    assert body["readiness_status"] == "READY"
    assert body["validation_summary"]["sample_jobs"][0]["title"] == "Role"
    assert "description" not in body["validation_summary"]["sample_jobs"][0]
    assert body["status"] == "READY"
    assert FakeOnboarding.calls == ["https://www.solita.fi/join-us/?country=denmark"]


def test_failed_onboarding_stays_non_active_and_preview_reads_preserve_history(setup_client):
    client, _ = setup_client
    company = create_company(client)
    source = create_source(client, company["id"])
    FakeOnboarding.fail = True
    failed = client.post(f"/api/sources/{source['id']}/onboarding", headers=auth(True), json={})
    assert failed.status_code == 422
    assert client.get(f"/api/sources/{source['id']}/previews", headers=auth()).json() == []
    FakeOnboarding.fail = False
    succeeded = client.post(f"/api/sources/{source['id']}/onboarding", headers=auth(True), json={})
    assert succeeded.status_code == 201
    preview_id = succeeded.json()["id"]
    listed = client.get(f"/api/sources/{source['id']}/previews", headers=auth())
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    detail = client.get(f"/api/sources/{source['id']}/previews/{preview_id}", headers=auth())
    assert detail.status_code == 200


def test_revalidate_delegates_to_onboarding_and_no_activation_route_exists(setup_client):
    client, _ = setup_client
    company = create_company(client)
    source = create_source(client, company["id"])
    response = client.post(f"/api/sources/{source['id']}/revalidate", headers=auth(True))
    assert response.status_code == 201
    assert response.json()["lifecycle_status"] == "PREVIEW_READY"
    assert client.post(f"/api/sources/{source['id']}/activate", headers=auth(True)).status_code == 404


@pytest.fixture
def request_scoped_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False)
    FakeOnboarding.calls = []
    FakeOnboarding.fail = False

    def request_session():
        with session_factory() as session:
            yield session

    def request_service(session: Session = Depends(request_session)):
        return SourceManagementService(session, onboarding_factory=FakeOnboarding)

    app.dependency_overrides[sources_get_session] = request_session
    app.dependency_overrides[sources_service] = request_service
    os.environ["DASHBOARD_TOKEN"] = "test-token"
    client = TestClient(app)
    try:
        yield client, session_factory
    finally:
        app.dependency_overrides.clear()


def test_transaction_boundary_company_and_source_persists_across_requests(request_scoped_client):
    client, session_factory = request_scoped_client

    # Request 1: create company
    resp1 = client.post(
        "/api/sources/companies",
        headers=auth(True),
        json={"name": "Energinet", "domain": "energinet.dk", "country": "Denmark"},
    )
    assert resp1.status_code == 201
    company_id = resp1.json()["id"]

    # Request 2: create source referencing company_id from request 1
    resp2 = client.post(
        f"/api/sources/companies/{company_id}/sources",
        headers=auth(True),
        json={
            "source_id": "energinet_preview",
            "display_name": "Energinet source",
            "official_jobs_url": "https://energinet.dk/karriere/",
            "canonical_url": "https://energinet.dk/karriere/",
            "allowed_domains": ["energinet.dk"],
            "robots_txt_url": "https://energinet.dk/robots.txt",
            "crawl_delay_seconds": 5,
        },
    )
    assert resp2.status_code == 201
    source_id = resp2.json()["id"]
    assert resp2.json()["lifecycle_status"] == "DRAFT"

    # Request 3: fetch source in a new request
    resp3 = client.get(f"/api/sources/{source_id}", headers=auth())
    assert resp3.status_code == 200
    assert resp3.json()["source_id"] == "energinet_preview"

    # Verify rows are committed and visible in an independent session
    with session_factory() as session:
        db_company = session.get(Company, UUID(company_id))
        assert db_company is not None
        assert db_company.name == "Energinet"
        db_source = session.get(JobSource, UUID(source_id))
        assert db_source is not None
        assert db_source.company_id == UUID(company_id)


def test_duplicate_company_resolution_across_requests(request_scoped_client):
    client, session_factory = request_scoped_client

    # Request 1: create company
    resp1 = client.post(
        "/api/sources/companies",
        headers=auth(True),
        json={"name": "Solita", "domain": "www.solita.fi", "country": "Denmark"},
    )
    assert resp1.status_code == 201
    company_id = resp1.json()["id"]

    # Request 2: duplicate domain -> 409 conflict
    resp2 = client.post(
        "/api/sources/companies",
        headers=auth(True),
        json={"name": "Solita Copy", "domain": "www.solita.fi", "country": "Denmark"},
    )
    assert resp2.status_code == 409
    assert "company already exists" in resp2.json()["detail"]

    # Request 3: list companies and resolve existing
    resp3 = client.get("/api/sources/companies", headers=auth())
    assert resp3.status_code == 200
    companies = resp3.json()
    assert len(companies) == 1
    assert companies[0]["id"] == company_id
    assert companies[0]["domain"] == "solita.fi"


def test_rollback_on_mutation_failure_across_requests(request_scoped_client):
    client, session_factory = request_scoped_client

    # Request 1: create valid company
    resp1 = client.post(
        "/api/sources/companies",
        headers=auth(True),
        json={"name": "Vestas", "domain": "careers.vestas.com", "country": "Denmark"},
    )
    assert resp1.status_code == 201
    company_id = resp1.json()["id"]

    # Request 2: create source with duplicate source_id after creating first source
    resp2 = client.post(
        f"/api/sources/companies/{company_id}/sources",
        headers=auth(True),
        json={
            "source_id": "vestas_dk",
            "display_name": "Vestas Denmark",
            "official_jobs_url": "https://careers.vestas.com/search/",
            "allowed_domains": ["careers.vestas.com"],
            "crawl_delay_seconds": 15,
        },
    )
    assert resp2.status_code == 201

    # Request 3: duplicate source_id -> 409 conflict and rolled back
    resp3 = client.post(
        f"/api/sources/companies/{company_id}/sources",
        headers=auth(True),
        json={
            "source_id": "vestas_dk",
            "display_name": "Vestas Denmark Duplicate",
            "official_jobs_url": "https://careers.vestas.com/search2/",
            "allowed_domains": ["careers.vestas.com"],
            "crawl_delay_seconds": 15,
        },
    )
    assert resp3.status_code == 409

    # Verify only 1 source exists in DB
    with session_factory() as session:
        sources = session.query(JobSource).filter_by(company_id=UUID(company_id)).all()
        assert len(sources) == 1
        assert sources[0].display_name == "Vestas Denmark"


def test_source_approval_lifecycle_persists_across_requests(request_scoped_client):
    client, session_factory = request_scoped_client

    # Request 1: create company
    c_resp = client.post(
        "/api/sources/companies",
        headers=auth(True),
        json={"name": "Solita", "domain": "www.solita.fi", "country": "Denmark"},
    )
    company_id = c_resp.json()["id"]

    # Request 2: create source
    s_resp = client.post(
        f"/api/sources/companies/{company_id}/sources",
        headers=auth(True),
        json={
            "source_id": "solita_dk",
            "display_name": "Solita Denmark",
            "official_jobs_url": "https://www.solita.fi/join-us/?country=denmark",
            "allowed_domains": ["www.solita.fi"],
            "crawl_delay_seconds": 10,
        },
    )
    source_id = s_resp.json()["id"]

    # Request 3: onboard source -> preview created
    onboard_resp = client.post(f"/api/sources/{source_id}/onboarding", headers=auth(True), json={})
    assert onboard_resp.status_code == 201
    preview_version = onboard_resp.json()["version"]

    # Request 4: approve source
    app_resp = client.post(
        f"/api/sources/{source_id}/approve",
        headers=auth(True),
        json={"preview_version": preview_version},
    )
    assert app_resp.status_code == 200
    assert app_resp.json()["lifecycle_status"] == "ACTIVE"
    assert app_resp.json()["enabled"] is True

    # Request 5: fetch source in separate request to verify persisted ACTIVE state
    get_resp = client.get(f"/api/sources/{source_id}", headers=auth())
    assert get_resp.status_code == 200
    assert get_resp.json()["lifecycle_status"] == "ACTIVE"
    assert get_resp.json()["enabled"] is True


def test_revalidate_endpoint_calls_source_revalidate_not_workflow(request_scoped_client):
    client, session_factory = request_scoped_client

    c_resp = client.post(
        "/api/sources/companies",
        headers=auth(True),
        json={"name": "Solita", "domain": "www.solita.fi", "country": "Denmark"},
    )
    company_id = c_resp.json()["id"]
    s_resp = client.post(
        f"/api/sources/companies/{company_id}/sources",
        headers=auth(True),
        json={
            "source_id": "solita_dk",
            "display_name": "Solita Denmark",
            "official_jobs_url": "https://www.solita.fi/join-us/?country=denmark",
            "allowed_domains": ["www.solita.fi"],
            "crawl_delay_seconds": 10,
        },
    )
    source_id = s_resp.json()["id"]

    # Call POST /api/sources/{source_id}/revalidate
    reval_resp = client.post(f"/api/sources/{source_id}/revalidate", headers=auth(True))
    assert reval_resp.status_code == 201
    assert reval_resp.json()["lifecycle_status"] == "PREVIEW_READY"
    assert FakeOnboarding.calls == ["https://www.solita.fi/join-us/?country=denmark"]


def test_revalidate_unapproved_source_with_zero_jobs_produces_valid_preview(request_scoped_client):
    from backend.app.sources.onboarding import OnboardingTrialService
    client, session_factory = request_scoped_client

    # Create unapproved company & source with an unconfigured/generic domain
    c_resp = client.post(
        "/api/sources/companies",
        headers=auth(True),
        json={"name": "UnknownCo", "domain": "unknownco.dk", "country": "Denmark"},
    )
    company_id = c_resp.json()["id"]

    s_resp = client.post(
        f"/api/sources/companies/{company_id}/sources",
        headers=auth(True),
        json={
            "source_id": "unknownco_preview",
            "display_name": "UnknownCo source",
            "official_jobs_url": "https://www.unknownco.dk/jobs/",
            "allowed_domains": ["www.unknownco.dk", "unknownco.dk"],
            "crawl_delay_seconds": 5,
        },
    )
    source_id = s_resp.json()["id"]

    # Revalidate via Source Management with zero jobs (simulating ATS iframe/empty landing page)
    class FakeEmptyTransport:
        def get(self, url):
            class Response:
                status_code = 200
                text = "<html><body><h1>Ledige stillinger</h1><iframe src='https://candidate.hr-manager.net'></iframe></body></html>"
            return Response()

    def real_onboarding_factory(session):
        return OnboardingTrialService(session, transport=FakeEmptyTransport())

    with session_factory() as session:
        service = SourceManagementService(session, onboarding_factory=real_onboarding_factory)
        preview_read = service.revalidate(UUID(source_id))
        assert preview_read.status == "READY"
        assert preview_read.lifecycle_status == "PREVIEW_READY"
        assert preview_read.readiness_status == "BLOCKED"
        assert preview_read.validation_summary["discovery"]["jobs_discovered"] == 0
        assert any("NO_JOBS_DISCOVERED" in w for w in preview_read.validation_summary["validation"]["warnings"])
        assert preview_read.validation_summary["execution"]["ready"] is False
        assert preview_read.validation_summary["execution"]["status"] == "NOT_READY"
        assert preview_read.validation_summary["readiness"]["status"] == "EXECUTION_NOT_READY"

    # Verify /api/workflow/discover still rejects this unapproved source with 400
    wf_resp = client.post(
        "/api/workflow/discover",
        headers=auth(True),
        json={"careers_url": "https://www.unknownco.dk/jobs/"},
    )
    assert wf_resp.status_code == 400
    assert "not from an approved job source" in wf_resp.json()["detail"]
