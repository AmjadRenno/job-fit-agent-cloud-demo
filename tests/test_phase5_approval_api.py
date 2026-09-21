from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.db.models import AgentRun, Base, ExecutionEvent, Job, JobSource, SourcePreview
from backend.app.main import app
from backend.app.sources_management.api import get_session as sources_get_session, service as sources_service
from backend.app.sources_management.service import SourceManagementService


@pytest.fixture
def setup_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine)
    company = __import__("backend.app.db.models", fromlist=["Company"]).Company(
        name="Solita", domain="www.solita.fi", country="Denmark"
    )
    source = JobSource(
        source_id="solita_dk",
        display_name="Solita Denmark",
        company=company,
        official_jobs_url="https://www.solita.fi/join-us/",
        canonical_url="https://www.solita.fi/join-us/",
        allowed_domains=["www.solita.fi"],
        lifecycle_status="PREVIEW_READY",
        readiness_status="READY",
        boundary_fingerprint="boundary-v1",
    )
    session.add(source)
    session.flush()
    preview = SourcePreview(
        source=source,
        version=1,
        boundary_fingerprint="boundary-v1",
        status="READY",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        validation_summary={"sample_jobs": []},
    )
    session.add(preview)
    session.commit()

    def override_session():
        yield session

    def override_service():
        return SourceManagementService(session)

    app.dependency_overrides[sources_get_session] = override_session
    app.dependency_overrides[sources_service] = override_service
    os.environ["DASHBOARD_TOKEN"] = "test-token"
    client = TestClient(app)
    try:
        yield client, session, source, preview
    finally:
        app.dependency_overrides.clear()
        session.close()


def auth(human: bool = False) -> dict[str, str]:
    headers = {"X-Dashboard-Token": "test-token"}
    if human:
        headers["X-Actor-Type"] = "HUMAN"
    return headers


def test_approval_requires_dashboard_and_human_authorization(setup_client):
    client, _, source, preview = setup_client
    path = f"/api/sources/{source.id}/approve"
    payload = {"preview_version": preview.version}
    assert client.post(path, json=payload).status_code == 401
    assert client.post(path, headers=auth(), json=payload).status_code == 403


def test_valid_approval_activates_and_audits_once(setup_client):
    client, session, source, preview = setup_client
    path = f"/api/sources/{source.id}/approve"
    response = client.post(path, headers=auth(True), json={"preview_version": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["lifecycle_status"] == "ACTIVE"
    assert body["enabled"] is True
    assert body["approved_preview_version"] == 1
    assert body["approved_at"] is not None
    assert session.query(Job).count() == 0
    events = session.query(ExecutionEvent).filter_by(event_type="source_approval").all()
    assert len(events) == 1
    assert events[0].metadata_json == {"action": "APPROVE", "result": "ACTIVE", "preview_version": 1}
    assert session.query(AgentRun).count() == 1

    repeated = client.post(path, headers=auth(True), json={"preview_version": 1})
    assert repeated.status_code == 200
    assert len(session.query(ExecutionEvent).filter_by(event_type="source_approval").all()) == 1


def test_wrong_foreign_status_expired_and_mismatched_previews_are_rejected(setup_client):
    client, session, source, preview = setup_client
    path = f"/api/sources/{source.id}/approve"
    assert client.post(path, headers=auth(True), json={"preview_version": 99}).status_code == 404

    foreign_source = JobSource(
        source_id="other_dk", display_name="Other", official_jobs_url="https://other.example/careers",
        canonical_url="https://other.example/careers", allowed_domains=["other.example"],
        lifecycle_status="PREVIEW_READY", readiness_status="READY", boundary_fingerprint="boundary-v1",
    )
    session.add(foreign_source)
    session.flush()
    foreign_preview = SourcePreview(source=foreign_source, version=1, boundary_fingerprint="boundary-v1", status="READY")
    session.add(foreign_preview)
    session.delete(preview)
    session.commit()
    assert client.post(path, headers=auth(True), json={"preview_version": 1}).status_code == 404


def test_invalid_preview_states_expiry_and_boundary_cannot_activate(setup_client):
    client, session, source, preview = setup_client
    path = f"/api/sources/{source.id}/approve"
    preview.status = "INVALID"
    session.commit()
    assert client.post(path, headers=auth(True), json={"preview_version": 1}).status_code == 409


def test_execution_not_ready_preview_cannot_activate(setup_client):
    client, session, source, preview = setup_client
    source.readiness_status = "BLOCKED"
    preview.validation_summary = {"execution": {"ready": False}}
    session.commit()
    path = f"/api/sources/{source.id}/approve"
    assert client.post(path, headers=auth(True), json={"preview_version": 1}).status_code == 409

    preview.status = "READY"
    preview.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    session.commit()
    assert client.post(path, headers=auth(True), json={"preview_version": 1}).status_code == 409

    preview.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    source.boundary_fingerprint = "changed"
    session.commit()
    assert client.post(path, headers=auth(True), json={"preview_version": 1}).status_code == 409


def test_draft_onboarding_required_and_active_different_version_conflicts(setup_client):
    client, session, source, preview = setup_client
    path = f"/api/sources/{source.id}/approve"
    source.lifecycle_status = "DRAFT"
    session.commit()
    assert client.post(path, headers=auth(True), json={"preview_version": 1}).status_code == 409

    source.lifecycle_status = "ACTIVE"
    source.enabled = True
    source.approved_preview_version = 1
    session.commit()
    assert client.post(path, headers=auth(True), json={"preview_version": 2}).status_code == 409
