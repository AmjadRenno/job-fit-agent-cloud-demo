"""Phase 12 Definition-of-Done blocking fixes — focused regression tests.

Covers exactly the four audit gaps:
1. #6  persistent analysis-status guard (new analyzed / already-analyzed
       skipped / no duplicate analysis / content change re-analyzes)
2. #12 applied-job exclusion (applied excluded from matching + dashboard
       recommendations; interesting/unapplied stays eligible)
3. #4  explicit deterministic location eligibility gate (DK eligible,
       ineligible country rejected before any LLM work)
4. #11 application-tracking surface (JobRead.application_status via the
       dashboard API, None when untracked)
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.analysis.context import AnalysisPreconditionError, build_context, CandidateProfile
from backend.app.analysis.domain import Confidence, FitCategory, JobAnalysis
from backend.app.applications.service import ApplicationService, has_applied_application
from backend.app.dashboard.service import DashboardService
from backend.app.db.models import Base, CandidateProfile as CandidateProfileRow, Job, JobAnalysis as JobAnalysisRow, JobSource, MatchResult as MatchResultRow
from backend.app.db.repository import ensure_source
from backend.app.main import app
from backend.app.workflow.processing import JobProcessingService

PROFILE = Path("data/candidate/profile.md")


class CountingAnalyzer:
    model = "fake-dod-analyzer"

    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, context):
        self.calls += 1
        return JobAnalysis(
            overall_fit=70,
            fit_category=FitCategory.GOOD,
            matching_requirements=["C#"],
            missing_requirements=[],
            transferable_skills=[],
            experience_alignment="Candidate evidence aligns with the role.",
            education_alignment="Datamatiker education is relevant.",
            location_alignment="Denmark location aligns.",
            work_arrangement_alignment="Arrangement unknown.",
            strengths=["C#"],
            concerns=[],
            evidence=[],
            confidence=Confidence.MEDIUM,
            analysis_version="phase3-analysis-v1",
        )


def make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine)
    ensure_source(
        session,
        source_id="solita_dk",
        display_name="Solita Denmark",
        official_jobs_url="https://www.solita.fi/join-us/?country=denmark",
        allowed_domains=["www.solita.fi"],
    )
    source = session.query(JobSource).filter_by(source_id="solita_dk").one()
    source.lifecycle_status = "ACTIVE"
    source.enabled = True
    session.commit()
    return session


def job_record(url: str, description: str = "Build APIs with C# and ASP.NET Core.") -> dict:
    return {
        "source": "solita_dk",
        "title": "Backend Developer",
        "url": url,
        "description": description,
        "country": ["Denmark"],
        "location": "Denmark",
        "employment_type": "FULL_TIME",
        "status": "active",
        "availability_signal": "Apply to this position",
        "external_job_id": url.rsplit("-", 1)[-1].strip("/"),
    }


@pytest.fixture
def service():
    session = make_session()
    analyzer = CountingAnalyzer()
    yield session, JobProcessingService(session, analyzer=analyzer, profile_path=PROFILE), analyzer
    session.close()
# ---------------------------------------------------------------------------
# Fix #6 — persistent analysis-status guard
# ---------------------------------------------------------------------------


def test_new_job_is_analyzed_and_marked_completed(service):
    session, processing, analyzer = service
    result = processing.process_record(job_record("https://www.solita.fi/positions/new-1/"))
    assert analyzer.calls == 1
    assert result.analysis_performed is True
    assert result.job.analysis_status == "COMPLETED"
    assert session.query(JobAnalysisRow).count() == 1


def test_already_analyzed_job_is_skipped_without_duplicate(service):
    session, processing, analyzer = service
    processing.process_record(job_record("https://www.solita.fi/positions/skip-1/"))
    second = processing.process_record(job_record("https://www.solita.fi/positions/skip-1/"))
    assert analyzer.calls == 1
    assert second.analysis_performed is False
    assert second.job.analysis_status == "COMPLETED"
    assert session.query(JobAnalysisRow).count() == 1


def test_content_change_legitimately_reanalyzes(service):
    session, processing, analyzer = service
    processing.process_record(job_record("https://www.solita.fi/positions/change-1/"))
    processing.process_record(job_record(
        "https://www.solita.fi/positions/change-1/",
        description="Updated description with different requirements for Rust.",
    ))
    assert analyzer.calls == 2
    assert session.query(JobAnalysisRow).count() == 2


# ---------------------------------------------------------------------------
# Fix #12 — applied-job exclusion
# ---------------------------------------------------------------------------


def test_applied_job_is_not_rematched(service):
    session, processing, analyzer = service
    result = processing.process_record(job_record("https://www.solita.fi/positions/applied-1/"))
    application = ApplicationService(session).create_for_human(result.job.id, human_authorized=True)
    ApplicationService(session).transition(application.id, "TO_APPLY", human_authorized=True)
    ApplicationService(session).transition(application.id, "APPLIED", human_authorized=True)
    assert has_applied_application(session, result.job.id) is True

    rerun = processing.process_record(job_record("https://www.solita.fi/positions/applied-1/"))
    assert rerun.match_performed is False
    assert session.query(MatchResultRow).count() == 1


def test_applied_job_without_prior_match_is_never_matched(service):
    session, processing, analyzer = service
    result = processing.process_record(job_record("https://www.solita.fi/positions/applied-2/"))
    # Remove the match so the next run would create one if the guard failed.
    session.query(MatchResultRow).delete()
    session.flush()
    application = ApplicationService(session).create_for_human(result.job.id, human_authorized=True)
    ApplicationService(session).transition(application.id, "TO_APPLY", human_authorized=True)
    ApplicationService(session).transition(application.id, "APPLIED", human_authorized=True)

    rerun = processing.process_record(job_record("https://www.solita.fi/positions/applied-2/"))
    assert rerun.match_performed is False
    assert session.query(MatchResultRow).count() == 0


def test_interesting_unapplied_job_remains_eligible(service):
    session, processing, analyzer = service
    result = processing.process_record(job_record("https://www.solita.fi/positions/interesting-1/"))
    ApplicationService(session).create_for_human(result.job.id, human_authorized=True)
    assert has_applied_application(session, result.job.id) is False

    rerun = processing.process_record(job_record("https://www.solita.fi/positions/interesting-1/"))
    assert rerun.match is not None
    assert session.query(MatchResultRow).count() == 1


def test_dashboard_recommendations_exclude_applied_and_keep_unrelated(service):
    session, processing, _ = service
    applied = processing.process_record(job_record("https://www.solita.fi/positions/applied-3/"))
    unrelated = processing.process_record(job_record("https://www.solita.fi/positions/other-1/"))
    application = ApplicationService(session).create_for_human(applied.job.id, human_authorized=True)
    ApplicationService(session).transition(application.id, "TO_APPLY", human_authorized=True)
    ApplicationService(session).transition(application.id, "APPLIED", human_authorized=True)
    # This regression is about applied state; use an explicit display threshold
    # so a low score does not obscure that independent assertion.
    session.add(CandidateProfileRow(version="dashboard-threshold", profile_hash="dashboard-threshold", profile_data={"minimum_fit": 0}, is_active=True))
    session.commit()

    dashboard = DashboardService(session)
    listed = dashboard.matching_jobs(page=1, page_size=25)
    listed_ids = {item.id for item in listed.items}
    assert applied.job.id not in listed_ids
    assert unrelated.job.id in listed_ids
    assert listed.total == 1
# ---------------------------------------------------------------------------
# Fix #4 — explicit deterministic location eligibility gate
# ---------------------------------------------------------------------------


def _gate_job(session, country_code):
    from backend.app.db.models import JobSource
    source = session.query(JobSource).filter_by(source_id="solita_dk").one()
    job = Job(
        source_id=source.id,
        title="Role",
        source_job_url=f"https://www.solita.fi/positions/gate-{country_code or 'none'}/",
        description="Build APIs with C#.",
        country_code=country_code,
        availability_status="ACTIVE",
    )
    session.add(job)
    session.flush()
    return job


def test_eligible_country_reaches_analysis_gate():
    session = make_session()
    profile = CandidateProfile.load(PROFILE)
    context = build_context(_gate_job(session, "DK"), profile)
    assert context.country_code == "DK"
    session.close()


def test_ineligible_country_is_rejected_deterministically():
    session = make_session()
    profile = CandidateProfile.load(PROFILE)
    with pytest.raises(AnalysisPreconditionError, match="eligible locations"):
        build_context(_gate_job(session, "SE"), profile)
    with pytest.raises(AnalysisPreconditionError, match="eligible locations"):
        build_context(_gate_job(session, "DE"), profile)
    session.close()


def test_unknown_country_is_not_rejected_as_ineligible():
    # A missing country code is an unknown, not a configured ineligible
    # location; eligibility for unknown-country jobs stays governed by the
    # source adapters' own DK filtering, never fabricated by this gate.
    session = make_session()
    profile = CandidateProfile.load(PROFILE)
    context = build_context(_gate_job(session, None), profile)
    assert context.country_code is None
    session.close()


def test_location_gate_runs_before_any_llm_work():
    session = make_session()
    analyzer = CountingAnalyzer()
    from backend.app.analysis.service import JobAnalysisService
    job = _gate_job(session, "SE")
    session.add(job)
    session.flush()
    with pytest.raises(AnalysisPreconditionError):
        JobAnalysisService(session, analyzer).analyze_job(job.id, PROFILE)
    assert analyzer.calls == 0
    session.close()


# ---------------------------------------------------------------------------
# Fix #11 — application-tracking surface
# ---------------------------------------------------------------------------


def test_job_read_exposes_application_status_and_untracked_is_none():
    session = make_session()
    processing = JobProcessingService(session, analyzer=CountingAnalyzer(), profile_path=PROFILE)
    tracked = processing.process_record(job_record("https://www.solita.fi/positions/track-1/"))
    untracked = processing.process_record(job_record("https://www.solita.fi/positions/track-2/"))
    ApplicationService(session).create_for_human(tracked.job.id, human_authorized=True)

    dashboard = DashboardService(session)
    page = dashboard.jobs(page=1, page_size=25)
    statuses = {item.id: item.application_status for item in page.items}
    assert statuses[tracked.job.id] == "INTERESTING"
    assert statuses[untracked.job.id] is None
    session.close()


def test_dashboard_api_returns_application_status_field():
    session = make_session()
    processing = JobProcessingService(session, analyzer=CountingAnalyzer(), profile_path=PROFILE)
    result = processing.process_record(job_record("https://www.solita.fi/positions/api-1/"))
    ApplicationService(session).create_for_human(result.job.id, human_authorized=True)

    from backend.app.dashboard.api import get_session

    def override():
        yield session

    app.dependency_overrides[get_session] = override
    os.environ["DASHBOARD_TOKEN"] = "test-token"
    client = TestClient(app)
    body = client.get("/api/dashboard/jobs", headers={"X-Dashboard-Token": "test-token"}).json()
    assert all("application_status" in item for item in body["items"])
    tracked = next(item for item in body["items"] if item["id"] == str(result.job.id))
    assert tracked["application_status"] == "INTERESTING"
    app.dependency_overrides.clear()
    session.close()


# ---------------------------------------------------------------------------
# Migration — additive and reversible
# ---------------------------------------------------------------------------


def test_analysis_status_column_is_additive_and_migration_reversible():
    import importlib.util

    migration_path = Path(__file__).parents[1] / "backend" / "app" / "db" / "migrations" / "versions" / "0003_job_analysis_status.py"
    spec = importlib.util.spec_from_file_location("migration_0003", migration_path)
    migration_0003 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration_0003)

    assert "analysis_status" in Job.__table__.columns
    assert migration_0003.revision == "0003_job_analysis_status"
    assert migration_0003.down_revision == "0002_analysis_result"
    upgrade_source = inspect.getsource(migration_0003.upgrade)
    downgrade_source = inspect.getsource(migration_0003.downgrade)
    assert "add_column" in upgrade_source
    assert "drop_column" in downgrade_source
