from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session

from backend.app.applications.domain import ApplicationTransitionError, VALID_TRANSITIONS, validate_transition
from backend.app.applications.service import ApplicationAuthorizationError, ApplicationService
from backend.app.db.models import Application, ApplicationEvent, Base, CoverLetter, Job, JobSource
from backend.app.applications.api import get_session
from backend.app.main import app


def setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine)
    source = JobSource(source_id="solita_dk", display_name="Solita", official_jobs_url="https://www.solita.fi", allowed_domains=["www.solita.fi"])
    session.add(source); session.flush()
    job = Job(source_id=source.id, title="Role", source_job_url="https://www.solita.fi/role", availability_status="CLOSED")
    session.add(job); session.commit()
    return session, job


def test_every_documented_valid_transition_is_allowed():
    for current, targets in VALID_TRANSITIONS.items():
        for target in targets:
            validate_transition(current, target)


def test_invalid_transitions_and_system_authorization_are_rejected():
    with pytest.raises(ApplicationTransitionError):
        validate_transition("CLOSED", "APPLIED")
    session, job = setup()
    service = ApplicationService(session)
    with pytest.raises(ApplicationAuthorizationError):
        service.create_for_human(job.id, human_authorized=False)
    session.close()


def test_creation_is_human_only_and_duplicate_creation_is_idempotent():
    session, job = setup()
    service = ApplicationService(session)
    first = service.create_for_human(job.id, human_authorized=True, notes="Review")
    second = service.create_for_human(job.id, human_authorized=True)
    assert first.id == second.id
    assert first.status == "INTERESTING"
    assert session.query(ApplicationEvent).count() == 1
    assert session.query(ApplicationEvent).one().triggered_by == "HUMAN"
    session.close()


def test_transition_is_atomic_idempotent_and_applied_at_is_preserved():
    session, job = setup()
    service = ApplicationService(session)
    application = service.create_for_human(job.id, human_authorized=True)
    service.transition(application.id, "TO_APPLY", human_authorized=True)
    service.transition(application.id, "APPLIED", human_authorized=True)
    applied_at = application.applied_at
    service.transition(application.id, "APPLIED", human_authorized=True)
    assert application.applied_at == applied_at
    assert session.query(ApplicationEvent).count() == 3
    application.status = "CLOSED"
    session.commit()
    assert application.applied_at == applied_at
    assert job.availability_status == "CLOSED"
    session.close()


def test_rollback_keeps_state_and_event_unchanged():
    session, job = setup()
    service = ApplicationService(session)
    application = service.create_for_human(job.id, human_authorized=True)
    original_commit = session.commit
    def failing_commit():
        raise RuntimeError("db failure")
    session.commit = failing_commit
    with pytest.raises(RuntimeError):
        service.transition(application.id, "TO_APPLY", human_authorized=True)
    session.commit = original_commit
    session.rollback()
    assert session.get(Application, application.id).status == "INTERESTING"
    assert session.query(ApplicationEvent).count() == 1
    session.close()


def test_every_transition_appends_history_without_deleting_events():
    session, job = setup()
    service = ApplicationService(session)
    application = service.create_for_human(job.id, human_authorized=True)
    service.transition(application.id, "TO_APPLY", human_authorized=True)
    service.transition(application.id, "APPLIED", human_authorized=True)
    events = service.history(application.id)
    assert [(event.from_status, event.to_status) for event in events] == [(None, "INTERESTING"), ("INTERESTING", "TO_APPLY"), ("TO_APPLY", "APPLIED")]
    assert all(event.triggered_by == "HUMAN" for event in events)
    session.close()


def test_api_requires_dashboard_and_human_authorization():
    session, job = setup()
    def override():
        yield session
    app.dependency_overrides[get_session] = override
    import os
    os.environ["DASHBOARD_TOKEN"] = "test-token"
    client = TestClient(app)
    payload = {"target_state": "INTERESTING", "notes": "Human review"}
    assert client.post(f"/api/applications/jobs/{job.id}", json=payload).status_code == 401
    assert client.post(f"/api/applications/jobs/{job.id}", headers={"X-Dashboard-Token": "test-token"}, json=payload).status_code == 403
    response = client.post(f"/api/applications/jobs/{job.id}", headers={"X-Dashboard-Token": "test-token", "X-Actor-Type": "HUMAN"}, json=payload)
    assert response.status_code == 201
    application_id = response.json()["id"]
    transition = client.post(f"/api/applications/{application_id}/transition", headers={"X-Dashboard-Token": "test-token", "X-Actor-Type": "HUMAN"}, json={"target_state": "TO_APPLY"})
    assert transition.status_code == 200
    assert len(client.get(f"/api/applications/{application_id}/history", headers={"X-Dashboard-Token": "test-token"}).json()) == 2
    app.dependency_overrides.clear()
    session.close()


def test_cover_letter_association_is_validated_and_idempotent():
    session, job = setup()
    service = ApplicationService(session)
    source = session.query(JobSource).one()
    other_job = Job(source_id=source.id, title="Other", source_job_url="https://www.solita.fi/other", availability_status="ACTIVE")
    session.add(other_job); session.commit()
    application = service.create_for_human(job.id, human_authorized=True)
    letter = CoverLetter(job_id=job.id, content="Draft", model="test", prompt_version="v1")
    other_letter = CoverLetter(job_id=other_job.id, content="Other draft", model="test", prompt_version="v1")
    session.add_all([letter, other_letter]); session.commit()
    with pytest.raises(ApplicationAuthorizationError):
        service.attach_cover_letter(application.id, letter.id, human_authorized=False)
    with pytest.raises(LookupError):
        service.attach_cover_letter(application.id, uuid4(), human_authorized=True)
    with pytest.raises(ValueError):
        service.attach_cover_letter(application.id, other_letter.id, human_authorized=True)
    service.attach_cover_letter(application.id, letter.id, human_authorized=True)
    assert session.get(CoverLetter, letter.id).application_id == application.id
    service.attach_cover_letter(application.id, letter.id, human_authorized=True)
    assert session.get(CoverLetter, letter.id).application_id == application.id
    other_application = service.create_for_human(other_job.id, human_authorized=True)
    with pytest.raises(ValueError):
        service.attach_cover_letter(other_application.id, letter.id, human_authorized=True)
    session.close()


def test_api_cover_letter_association_is_human_only_and_persists():
    session, job = setup()
    def override():
        yield session
    app.dependency_overrides[get_session] = override
    import os
    os.environ["DASHBOARD_TOKEN"] = "test-token"
    client = TestClient(app)
    letter = CoverLetter(job_id=job.id, content="Draft", model="test", prompt_version="v1")
    session.add(letter); session.commit()
    created = client.post(f"/api/applications/jobs/{job.id}", headers={"X-Dashboard-Token": "test-token", "X-Actor-Type": "HUMAN"}, json={"target_state": "INTERESTING"})
    assert created.status_code == 201
    application_id = created.json()["id"]
    assert client.post(f"/api/applications/{application_id}/cover-letter", headers={"X-Dashboard-Token": "test-token"}, json={"cover_letter_id": str(letter.id)}).status_code == 403
    response = client.post(f"/api/applications/{application_id}/cover-letter", headers={"X-Dashboard-Token": "test-token", "X-Actor-Type": "HUMAN"}, json={"cover_letter_id": str(letter.id)})
    assert response.status_code == 200
    assert response.json()["id"] == application_id
    assert session.get(CoverLetter, letter.id).application_id == __import__("uuid").UUID(application_id)
    assert client.post(f"/api/applications/{application_id}/cover-letter", headers={"X-Dashboard-Token": "test-token", "X-Actor-Type": "HUMAN"}, json={"cover_letter_id": str(uuid4())}).status_code == 404
    app.dependency_overrides.clear()
    session.close()
