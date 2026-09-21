from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session

from backend.app.db.models import AgentRun, Application, Base, Job, JobAnalysis, JobSource, MatchResult
from backend.app.dashboard.api import get_session
from backend.app.main import app


def setup_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    source = JobSource(source_id="solita_dk", display_name="Solita Denmark", official_jobs_url="https://www.solita.fi", allowed_domains=["www.solita.fi"], health_status="HEALTHY", lifecycle_status="ACTIVE", enabled=True)
    session.add(source); session.flush()
    job = Job(source_id=source.id, title="Developer", source_job_url="https://www.solita.fi/job", description="Untrusted <script>alert(1)</script> text", availability_status="ACTIVE", analysis_status="COMPLETED")
    session.add(job); session.flush()
    run = AgentRun(started_at=datetime.now(timezone.utc), status="SUCCESS")
    session.add(run); session.flush()
    analysis = JobAnalysis(job_id=job.id, run_id=run.id, analysis_result={"fit_category":"GOOD"}, model="test", prompt_version="v1")
    session.add(analysis); session.flush()
    session.add(MatchResult(job_id=job.id, analysis_id=analysis.id, run_id=run.id, overall_score=70, confidence="MEDIUM", recommendation="CONSIDER", critical_gaps=["Cloud experience"], reasoning="Review the remaining gap.", evidence={"requirements": [], "decision": {"recommendation": "CONSIDER", "reasons": ["Evidence match is 70%."], "critical_gaps": ["Cloud experience"], "next_step": "Review the gap."}}, model="test", prompt_version="v1"))
    session.commit()
    def override():
        yield session
    app.dependency_overrides[get_session] = override
    os.environ["DASHBOARD_TOKEN"] = "test-token"
    return TestClient(app), session, job


def test_dashboard_requires_backend_authorization():
    client, session, _ = setup_client()
    assert client.get("/api/dashboard/jobs").status_code == 401
    response = client.get("/api/dashboard/jobs", headers={"X-Dashboard-Token": "test-token"})
    assert response.status_code == 200
    app.dependency_overrides.clear(); session.close()


def test_dashboard_reads_persisted_values_and_filters():
    client, session, _ = setup_client()
    response = client.get("/api/dashboard/jobs?page=1&page_size=1&status=ACTIVE", headers={"X-Dashboard-Token": "test-token"})
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["availability_status"] == "ACTIVE"
    assert "<script>" in body["items"][0]["description"]
    assert client.get("/api/dashboard/matching", headers={"X-Dashboard-Token": "test-token"}).json()["total"] == 1
    app.dependency_overrides.clear(); session.close()


def test_seen_is_set_only_by_explicit_human_open():
    client, session, job = setup_client()
    token = {"X-Dashboard-Token": "test-token", "X-Actor-Type": "HUMAN"}
    assert job.seen_at is None
    assert client.get("/api/dashboard/jobs", headers=token).json()["items"][0]["seen_at"] is None
    response = client.post(f"/api/dashboard/jobs/{job.id}/seen", headers=token)
    assert response.status_code == 200
    first_seen = response.json()["seen_at"]
    assert first_seen is not None
    assert client.post(f"/api/dashboard/jobs/{job.id}/seen", headers=token).json()["seen_at"] == first_seen
    app.dependency_overrides.clear(); session.close()


def test_dashboard_history_runs_and_health_are_read_only():
    client, session, job = setup_client()
    token = {"X-Dashboard-Token": "test-token"}
    history = client.get(f"/api/dashboard/jobs/{job.id}/history", headers=token)
    assert len(history.json()["analyses"]) == 1
    assert len(history.json()["matches"]) == 1
    match = history.json()["matches"][0]
    assert match["recommendation"] == "CONSIDER"
    assert match["critical_gaps"] == ["Cloud experience"]
    assert match["evidence"]["decision"]["next_step"] == "Review the gap."
    assert len(client.get("/api/dashboard/runs", headers=token).json()) == 1
    assert client.get("/api/dashboard/source-health", headers=token).json()[0]["jobs_persisted"] == 1
    assert client.post(f"/api/dashboard/jobs/{job.id}", headers=token).status_code == 405
    app.dependency_overrides.clear(); session.close()


def test_dashboard_single_application_read():
    client, session, job = setup_client()
    application = Application(job_id=job.id, status="INTERESTING", notes="Human review")
    session.add(application); session.commit()
    token = {"X-Dashboard-Token": "test-token"}
    response = client.get(f"/api/dashboard/applications/{application.id}", headers=token)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(application.id)
    assert body["job_id"] == str(job.id)
    assert body["status"] == "INTERESTING"
    assert client.get(f"/api/dashboard/applications/{uuid4()}", headers=token).status_code == 404
    listed = client.get("/api/dashboard/applications", headers=token).json()
    assert [item["id"] for item in listed] == [str(application.id)]
    app.dependency_overrides.clear(); session.close()


def test_dashboard_snapshot_aggregates_operational_view():
    client, session, job = setup_client()
    body = client.get("/api/dashboard/snapshot", headers={"X-Dashboard-Token": "test-token"}).json()
    assert body["new_jobs"]["total"] == 1
    assert body["matching_jobs"]["total"] == 1
    assert len(body["runs"]) == 1
    assert body["source_health"][0]["source_id"] == "solita_dk"
    assert client.get("/api/dashboard/snapshot").status_code == 401
    app.dependency_overrides.clear(); session.close()
