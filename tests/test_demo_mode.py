from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from backend.app.analysis.llm import OpenAIAnalyzer
from backend.app.db.models import Base, Job, JobAnalysis, MatchResult
from backend.app.main import app
from backend.app.mode import DEMO_MODE_READ_ONLY
from scripts.run_daily_search import DailyRun
from scripts.seed_demo import seed_demo


def test_demo_mode_allows_health_and_blocks_commands_before_handlers(monkeypatch):
    monkeypatch.setenv("APP_MODE", "demo")
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    response = client.post("/api/sources/companies", json={})
    assert response.status_code == 403
    assert response.json() == {"detail": DEMO_MODE_READ_ONLY}


def test_demo_mode_blocks_daily_run_and_openai_before_client_construction(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_MODE", "demo")
    with pytest.raises(RuntimeError, match=DEMO_MODE_READ_ONLY):
        OpenAIAnalyzer()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        with pytest.raises(RuntimeError, match=DEMO_MODE_READ_ONLY):
            DailyRun(session, tmp_path).execute()


def test_synthetic_seed_is_idempotent_and_resolves_synthetic_evidence(monkeypatch):
    monkeypatch.setenv("APP_MODE", "demo")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    profile = Path(__file__).parents[1] / "data/candidate/profile.md"
    with Session(engine) as session:
        first = seed_demo(session, profile)
        second = seed_demo(session, profile)
        assert first == {"jobs_created": 6, "analyses_created": 6, "matches_created": 6, "jobs_total": 6}
        assert second == {"jobs_created": 0, "analyses_created": 0, "matches_created": 0, "jobs_total": 6}
        assert session.scalar(select(func.count()).select_from(Job)) == 6
        assert session.scalar(select(func.count()).select_from(JobAnalysis)) == 6
        assert all(analysis.job_id is not None for analysis in session.scalars(select(JobAnalysis)))
        rows = session.scalars(select(MatchResult)).all()
        assert len(rows) == 6
        assert all(item.evidence and all(evidence_id.startswith("profile:") for requirement in item.evidence["requirements"] for evidence_id in requirement["candidate_evidence_ids"]) for item in rows)
