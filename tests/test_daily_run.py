from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.analysis.domain import Confidence, FitCategory, JobAnalysis
from backend.app.db.models import AgentRun, Base, ExecutionEvent, Job, JobAnalysis as JobAnalysisRow, JobSource, MatchResult
from backend.app.db.repository import ensure_source
from scripts.run_daily_search import DailyRun, OverlappingRunError


class FakeAnalyzer:
    model = "fake-model"

    def analyze(self, context):
        return JobAnalysis(
            overall_fit=70, fit_category=FitCategory.GOOD, matching_requirements=["C#"],
            missing_requirements=[], transferable_skills=[], experience_alignment="Aligned.",
            education_alignment="Relevant.", location_alignment="Denmark.", work_arrangement_alignment="Unknown.",
            strengths=["C#"], concerns=[], evidence=[], confidence=Confidence.MEDIUM,
            analysis_version="phase3-analysis-v1",
        )


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def write_payload(path: Path, jobs: list[dict]) -> dict:
    path.mkdir(parents=True, exist_ok=True)
    payload = {"source": "solita_dk", "run_date": "2026-08-25", "jobs": jobs}
    (path / "jobs.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def success_runner(path: Path) -> dict:
    return write_payload(path, [{"source": "solita_dk", "title": "Role", "url": "https://www.solita.fi/role", "description": "A sufficiently long job description for testing the daily run.", "country": ["Denmark"], "location": "Denmark", "employment_type": "UNKNOWN", "status": "active", "availability_signal": "explicit"}])


def empty_runner(path: Path) -> dict:
    return write_payload(path, [])


def configured(session):
    ensure_source(session, source_id="solita_dk", display_name="Solita", official_jobs_url="https://www.solita.fi", allowed_domains=["www.solita.fi"])
    session.commit()


def configured_root(root: Path) -> None:
    profile = root / "data" / "candidate" / "profile.md"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(Path("data/candidate/profile.md").read_text(encoding="utf-8"), encoding="utf-8")


def test_success_lifecycle_trace_and_summary(tmp_path):
    session = make_session(); configured(session)
    configured_root(tmp_path)
    summary = DailyRun(session, tmp_path, analysis_factory=FakeAnalyzer, source_runners={"solita_dk": success_runner}).execute()
    assert summary["status"] == "SUCCESS"
    assert summary["analyses_performed"] == 1
    assert summary["matches_performed"] == 1
    assert session.query(AgentRun).one().ended_at is not None
    assert session.query(AgentRun).one().sources_success == 1
    events = [event.event_type for event in session.query(ExecutionEvent).all()]
    assert {"run_started", "source_started", "job_persisted", "analysis_completed", "matching_completed", "run_completed"} <= set(events)
    source = session.query(JobSource).filter_by(source_id="solita_dk").one()
    assert source.health_status == "HEALTHY"
    assert source.last_successful_run is not None


def test_daily_runner_loads_repository_environment_file(monkeypatch, tmp_path):
    from scripts import run_daily_search

    monkeypatch.delenv("DATABASE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=postgresql://daily-test\n", encoding="utf-8")
    run_daily_search.load_environment(tmp_path)
    assert os.environ["DATABASE_URL"] == "postgresql://daily-test"


def test_idempotent_rerun_skips_analysis_and_matching(tmp_path):
    session = make_session(); configured(session)
    configured_root(tmp_path)
    runners = {"solita_dk": success_runner}
    first = DailyRun(session, tmp_path, analysis_factory=FakeAnalyzer, source_runners=runners).execute()
    second = DailyRun(session, tmp_path, analysis_factory=FakeAnalyzer, source_runners=runners).execute()
    assert first["analyses_performed"] == 1
    assert second["analyses_performed"] == 0
    assert second["matches_performed"] == 0
    assert session.query(Job).count() == 1
    assert session.query(JobAnalysisRow).count() == 1
    assert session.query(MatchResult).count() == 1


def test_idempotency_uses_application_prompt_version_not_provider_field(tmp_path):
    session = make_session(); configured(session)
    configured_root(tmp_path)
    runners = {"solita_dk": success_runner}
    DailyRun(session, tmp_path, analysis_factory=FakeAnalyzer, source_runners=runners).execute()
    analysis = session.query(JobAnalysisRow).one()
    analysis.analysis_result = {**analysis.analysis_result, "analysis_version": "1.0"}
    session.commit()

    second = DailyRun(session, tmp_path, analysis_factory=FakeAnalyzer, source_runners=runners).execute()

    assert second["analyses_performed"] == 0
    assert second["matches_performed"] == 0
    assert session.query(JobAnalysisRow).count() == 1


def test_material_content_change_creates_new_analysis_and_match(tmp_path):
    session = make_session(); configured(session); configured_root(tmp_path)
    descriptions = iter(["A sufficiently long original job description for testing the daily run.", "A materially changed job description requiring a new processing history."])
    def changing_runner(path):
        return write_payload(path, [{"source": "solita_dk", "title": "Role", "url": "https://www.solita.fi/role", "description": next(descriptions), "country": ["Denmark"], "location": "Denmark", "employment_type": "UNKNOWN", "status": "active", "availability_signal": "explicit"}])
    runner = {"solita_dk": changing_runner}
    DailyRun(session, tmp_path, analysis_factory=FakeAnalyzer, source_runners=runner).execute()
    DailyRun(session, tmp_path, analysis_factory=FakeAnalyzer, source_runners=runner).execute()
    assert session.query(JobAnalysisRow).count() == 2
    assert session.query(MatchResult).count() == 2


def test_partial_and_total_failure_statuses(tmp_path):
    session = make_session(); configured(session)
    configured_root(tmp_path)
    partial = DailyRun(session, tmp_path, source_runners={"solita_dk": empty_runner, "energinet_dk": lambda path: (_ for _ in ()).throw(ValueError("blocked"))}).execute()
    assert partial["status"] == "PARTIAL_SUCCESS"
    session.query(AgentRun).update({AgentRun.status: "SUCCESS"}); session.commit()
    failed = DailyRun(session, tmp_path, source_runners={"solita_dk": lambda path: (_ for _ in ()).throw(ValueError("bad")), "energinet_dk": lambda path: (_ for _ in ()).throw(ValueError("blocked"))}).execute()
    assert failed["status"] == "FAILED"
    assert session.query(JobSource).filter_by(source_id="solita_dk").one().health_status == "UNHEALTHY"


def test_blocked_source_produces_partial_success(tmp_path):
    session = make_session(); configured(session); configured_root(tmp_path)
    blocked = lambda path: {"source": "energinet_dk", "jobs": [], "blocker": {"code": "EXTERNAL_ATS_NOT_ALLOWLISTED"}}
    summary = DailyRun(session, tmp_path, source_runners={"solita_dk": empty_runner, "energinet_dk": blocked}).execute()
    assert summary["status"] == "PARTIAL_SUCCESS"
    assert session.query(AgentRun).one().sources_partial == 1


def test_retry_only_for_retryable_source_error(tmp_path):
    session = make_session(); configured(session); calls = {"count": 0}
    configured_root(tmp_path)
    def flaky(path):
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("temporary")
        return empty_runner(path)
    summary = DailyRun(session, tmp_path, source_runners={"solita_dk": flaky}).execute()
    assert summary["status"] == "SUCCESS"
    assert summary["retries"] == [{"source": "solita_dk", "error": "TimeoutError"}]


def test_overlapping_run_is_refused(tmp_path):
    session = make_session(); configured(session)
    configured_root(tmp_path)
    session.add(AgentRun(id=__import__("uuid").uuid4(), started_at=datetime.now(timezone.utc), status="RUNNING")); session.commit()
    with pytest.raises(OverlappingRunError):
        DailyRun(session, tmp_path, source_runners={"solita_dk": empty_runner}).execute()


def test_single_job_failure_is_isolated_and_recorded(tmp_path):
    session = make_session(); configured(session); configured_root(tmp_path)
    def mixed_runner(path):
        return write_payload(path, [
            {"source": "solita_dk", "title": "Good Role", "url": "https://www.solita.fi/good", "description": "A sufficiently long job description for testing the daily run.", "country": ["Denmark"], "location": "Denmark", "employment_type": "UNKNOWN", "status": "active", "availability_signal": "explicit"},
            {"source": "solita_dk", "title": "Broken Role", "description": "A job record without a url must fail persistence.", "country": ["Denmark"], "location": "Denmark", "employment_type": "UNKNOWN", "status": "active", "availability_signal": "explicit"},
        ])
    summary = DailyRun(session, tmp_path, analysis_factory=FakeAnalyzer, source_runners={"solita_dk": mixed_runner}).execute()
    assert summary["status"] == "PARTIAL_SUCCESS"
    assert summary["jobs_failed"] == 1
    assert summary["analyses_performed"] == 1
    assert session.query(Job).count() == 1
    failed = [event for event in session.query(ExecutionEvent).all() if event.event_type == "job_failed"]
    assert len(failed) == 1
    assert failed[0].source_id == "solita_dk"
    assert failed[0].status == "FAILURE"
    assert failed[0].error_code is not None
    assert summary["failures"] == [{"source": "solita_dk", "job_url": "unknown", "code": failed[0].error_code}]
