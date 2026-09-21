from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.dashboard.service import DashboardService
from backend.app.db.models import Application, Base, CandidateProfile, Company, Job, JobAnalysis, JobSource, MatchResult
from backend.app.sources_management.service import SourceManagementService


def make_session() -> tuple[Session, Company, JobSource]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine)
    company = Company(name="Example", domain="example.test", country="Denmark")
    source = JobSource(source_id="example", company=company, display_name="Example jobs", official_jobs_url="https://example.test/jobs", canonical_url="https://example.test/jobs", allowed_domains=["example.test"], lifecycle_status="ACTIVE", readiness_status="READY", enabled=True, health_status="UNKNOWN")
    session.add_all([company, source, CandidateProfile(version="preferences", profile_hash="preferences", profile_data={"minimum_fit": 60}, is_active=True)])
    session.commit()
    return session, company, source


def analyzed_job(session: Session, source: JobSource, *, title: str, score: int, first_seen: datetime, posted: date | None = None, seen: datetime | None = None) -> Job:
    job = Job(source_id=source.id, company_id=source.company_id, title=title, source_job_url=f"https://example.test/{title}", description="Python services", availability_status="ACTIVE", analysis_status="COMPLETED", first_seen_at=first_seen, last_seen_at=first_seen, date_posted=posted, seen_at=seen)
    session.add(job); session.flush()
    analysis = JobAnalysis(job_id=job.id, analysis_result={}, must_have=["Python"], model="test", prompt_version="v1", created_at=first_seen)
    session.add(analysis); session.flush()
    session.add(MatchResult(job_id=job.id, analysis_id=analysis.id, overall_score=score, confidence="HIGH", recommendation="CONSIDER", matched_requirements=["Python"], partial_matches=[], gaps=[], critical_gaps=[], evidence={"requirements": []}, model="test", prompt_version="v1", created_at=first_seen))
    session.commit()
    return job


def test_recommended_threshold_applied_unseen_and_sorting_are_server_semantics():
    session, _, source = make_session()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    good = analyzed_job(session, source, title="good", score=70, first_seen=base, posted=date(2026, 1, 3))
    low = analyzed_job(session, source, title="low", score=53, first_seen=base + timedelta(days=2), posted=None)
    applied = analyzed_job(session, source, title="applied", score=90, first_seen=base + timedelta(days=1), posted=date(2026, 1, 1))
    session.add(Application(job_id=applied.id, status="APPLIED")); session.commit()

    dashboard = DashboardService(session)
    recommended = dashboard.jobs(page=1, page_size=20, view="recommended", sort="best_fit")
    assert [item.id for item in recommended.items] == [good.id]
    assert dashboard.jobs(page=1, page_size=20, view="applied").items[0].id == applied.id
    # Seen is a user-open state, not discovery or analysis state.
    assert {item.id for item in dashboard.jobs(page=1, page_size=20, view="unseen").items} == {good.id, low.id, applied.id}
    by_posted = dashboard.jobs(page=1, page_size=20, view="all", sort="newest_posted").items
    assert [item.id for item in by_posted] == [good.id, applied.id, low.id]
    session.close()


def test_latest_analysis_match_and_cover_eligibility_are_composed_without_guessing():
    session, _, source = make_session()
    job = analyzed_job(session, source, title="current", score=70, first_seen=datetime(2026, 1, 1, tzinfo=timezone.utc))
    old_analysis = session.query(JobAnalysis).filter_by(job_id=job.id).one()
    newer = JobAnalysis(job_id=job.id, analysis_result={}, model="test", prompt_version="v2", created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    session.add(newer); session.commit()
    # The older match is intentionally not presented as a current result.
    read = DashboardService(session).job(job.id)
    assert read.latest_analysis is not None and read.latest_analysis.id == newer.id
    assert read.latest_match is None
    assert read.cover_letter.eligible is False
    assert read.cover_letter.reason == "NO_VALID_MATCH"
    assert old_analysis.id != newer.id
    session.close()


def test_company_summary_has_one_row_for_multiple_sources_and_unknown_is_not_healthy():
    session, company, source = make_session()
    second = JobSource(source_id="example-secondary", company_id=company.id, display_name="Example secondary", official_jobs_url="https://example.test/careers", canonical_url="https://example.test/careers", allowed_domains=["example.test"], lifecycle_status="DISABLED", readiness_status="UNKNOWN", enabled=False, health_status="HEALTHY")
    session.add(second)
    analyzed_job(session, source, title="one", score=70, first_seen=datetime(2026, 1, 1, tzinfo=timezone.utc))
    companies = SourceManagementService(session).list_companies()
    assert len(companies) == 1
    summary = companies[0]
    assert summary.source_count == 2
    assert summary.job_count == 1
    assert summary.unseen_job_count == 1
    assert summary.recommended_job_count == 1
    assert summary.health_status == "UNKNOWN"
    health = DashboardService(session).source_health_summary()
    assert health.health_status == "UNKNOWN"
    session.close()
