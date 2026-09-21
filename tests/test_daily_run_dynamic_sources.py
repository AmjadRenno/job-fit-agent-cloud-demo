from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.db.models import AgentRun, Base, ExecutionEvent, JobSource
from backend.app.db.repository import ensure_source
from scripts import run_daily_search
from scripts.run_daily_search import DailyRun


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def configure_source(session: Session, source_id: str, lifecycle: str, enabled: bool) -> JobSource:
    source = ensure_source(
        session,
        source_id=source_id,
        display_name=source_id,
        official_jobs_url=f"https://{source_id}.example/careers",
        allowed_domains=[f"{source_id}.example"],
    )
    session.flush()
    source.lifecycle_status = lifecycle
    source.enabled = enabled
    session.commit()
    return source


def empty_runner(path: Path) -> dict[str, object]:
    path.mkdir(parents=True, exist_ok=True)
    (path / "jobs.json").write_text(json.dumps({"source": "registered_dk", "jobs": []}), encoding="utf-8")
    return {"source": "registered_dk", "jobs": []}


@pytest.mark.parametrize("lifecycle", ["DRAFT", "ONBOARDING", "PREVIEW_READY", "REVALIDATION_REQUIRED", "DISABLED", "ARCHIVED"])
def test_non_active_sources_are_excluded(session: Session, tmp_path: Path, lifecycle: str):
    configure_source(session, "inactive_dk", lifecycle, True)
    summary = DailyRun(session, tmp_path, source_runners=None).execute()
    assert summary["sources_attempted"] == []
    assert session.query(AgentRun).one().sources_total == 0


def test_active_disabled_source_is_excluded(session: Session, tmp_path: Path):
    configure_source(session, "disabled_dk", "ACTIVE", False)
    summary = DailyRun(session, tmp_path).execute()
    assert summary["sources_attempted"] == []


def test_active_enabled_registered_source_executes(session: Session, tmp_path: Path, monkeypatch):
    source = configure_source(session, "registered_dk", "ACTIVE", True)
    monkeypatch.setattr(run_daily_search, "resolve_source_runner", lambda source_id: empty_runner if source_id == source.source_id else None)
    summary = DailyRun(session, tmp_path).execute()
    assert summary["status"] == "SUCCESS"
    assert summary["sources_attempted"] == [{"source": "registered_dk", "status": "SUCCESS", "jobs": 0}]
    assert source.lifecycle_status == "ACTIVE"
    assert source.enabled is True
    assert not [event for event in session.query(ExecutionEvent) if event.event_type == "source_failed"]


def test_active_enabled_unsupported_source_is_isolated(session: Session, tmp_path: Path, monkeypatch):
    configure_source(session, "unsupported_dk", "ACTIVE", True)
    configure_source(session, "registered_dk", "ACTIVE", True)
    monkeypatch.setattr(run_daily_search, "resolve_source_runner", lambda source_id: empty_runner if source_id == "registered_dk" else None)
    summary = DailyRun(session, tmp_path).execute()
    assert summary["status"] == "PARTIAL_SUCCESS"
    assert [item["source"] for item in summary["sources_attempted"]] == ["registered_dk", "unsupported_dk"]
    assert any(item["source"] == "unsupported_dk" and item["status"] == "FAILED" for item in summary["sources_attempted"])
    failed = [event for event in session.query(ExecutionEvent) if event.event_type == "source_failed"]
    assert len(failed) == 1
    assert failed[0].source_id == "unsupported_dk"
    assert failed[0].error_code == "UnsupportedSourceRunnerError"
    assert session.query(JobSource).filter_by(source_id="unsupported_dk").one().enabled is True
