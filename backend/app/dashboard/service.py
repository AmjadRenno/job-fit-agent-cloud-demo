from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from backend.app.applications.service import APPLIED_APPLICATION_STATUSES
from backend.app.cover_letters.service import cover_letter_eligibility
from backend.app.db.models import AgentRun, Application, ExecutionEvent, Job, JobAnalysis, JobSource, MatchResult
from backend.app.preferences import minimum_fit_preference

from .schemas import AnalysisRead, AnalysisSummaryRead, ApplicationRead, CoverLetterEligibilityRead, EventRead, HistoryRead, JobPage, JobRead, MatchRead, MatchSummaryRead, RunRead, SourceHealthRead, SourceHealthSummaryRead


PROFILE_PATH = Path("data/candidate/profile.md")
VALID_VIEWS = frozenset({"recommended", "unseen", "applied", "all"})
VALID_SORTS = frozenset({"best_fit", "newest_discovered", "newest_posted"})


class DashboardService:
    """Compose the UI read model from persisted job and history records.

    Recommended means: a completed latest analysis with its matching latest
    match, score at/above the saved display threshold, not applied, and an
    ACTIVE job from an active enabled source. Unseen means ``seen_at is None``
    independently of analysis. Applied follows Application's existing domain
    statuses. All retains every persisted job allowed by current history rules.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def jobs(self, *, page: int, page_size: int, status: str | None = None, view: str = "all", company_id: UUID | None = None, search: str | None = None, analysis_status: str | None = None, application_status: str | None = None, starred: bool | None = None, sort: str = "newest_discovered") -> JobPage:
        if view not in VALID_VIEWS:
            raise ValueError("invalid job view")
        if sort not in VALID_SORTS:
            raise ValueError("invalid job sort")
        query = select(Job).options(selectinload(Job.source).selectinload(JobSource.company), selectinload(Job.company))
        needs_source_join = company_id is not None or bool(search and search.strip())
        if needs_source_join:
            query = query.join(JobSource, Job.source_id == JobSource.id)
        if status:
            query = query.where(Job.availability_status == status)
        if company_id:
            query = query.where(or_(Job.company_id == company_id, JobSource.company_id == company_id))
        if analysis_status:
            query = query.where(Job.analysis_status == analysis_status)
        if starred is not None:
            query = query.where(Job.starred.is_(starred))
        if search and search.strip():
            term = f"%{search.strip()}%"
            query = query.where(Job.title.ilike(term) | Job.description.ilike(term) | Job.location_raw.ilike(term) | JobSource.display_name.ilike(term))
        composed = self._compose_jobs(list(self.session.scalars(query).all()))
        if application_status:
            composed = [item for item in composed if item.application_status == application_status]
        composed = [item for item in composed if self._in_view(item, view)]
        composed.sort(key=lambda item: self._sort_key(item, sort))
        total = len(composed)
        start = (page - 1) * page_size
        return JobPage(page=page, page_size=page_size, total=total, items=composed[start:start + page_size])

    def matching_jobs(self, *, page: int, page_size: int) -> JobPage:
        """Compatibility alias; new consumers use ``/jobs?view=recommended``."""
        return self.jobs(page=page, page_size=page_size, view="recommended", sort="best_fit")

    def job(self, job_id: UUID) -> JobRead:
        item = self.session.scalar(select(Job).options(selectinload(Job.source).selectinload(JobSource.company), selectinload(Job.company)).where(Job.id == job_id))
        if item is None:
            raise LookupError("job not found")
        return self._compose_jobs([item])[0]

    def analysis(self, analysis_id: UUID) -> AnalysisRead:
        item = self.session.get(JobAnalysis, analysis_id)
        if item is None:
            raise LookupError("analysis not found")
        return AnalysisRead.model_validate(item, from_attributes=True)

    def match(self, match_id: UUID) -> MatchRead:
        item = self.session.get(MatchResult, match_id)
        if item is None:
            raise LookupError("match not found")
        return MatchRead.model_validate(item, from_attributes=True)

    def history(self, job_id: UUID) -> HistoryRead:
        analyses = self.session.scalars(select(JobAnalysis).where(JobAnalysis.job_id == job_id).order_by(JobAnalysis.created_at.desc(), JobAnalysis.id.desc())).all()
        matches = self.session.scalars(select(MatchResult).where(MatchResult.job_id == job_id).order_by(MatchResult.created_at.desc(), MatchResult.id.desc())).all()
        return HistoryRead(analyses=[AnalysisRead.model_validate(item, from_attributes=True) for item in analyses], matches=[MatchRead.model_validate(item, from_attributes=True) for item in matches])

    def runs(self, *, limit: int) -> list[RunRead]:
        return [RunRead.model_validate(item, from_attributes=True) for item in self.session.scalars(select(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit)).all()]

    def run_events(self, run_id: UUID) -> list[EventRead]:
        return [EventRead.model_validate(item, from_attributes=True) for item in self.session.scalars(select(ExecutionEvent).where(ExecutionEvent.run_id == run_id).order_by(ExecutionEvent.created_at)).all()]

    def applications(self, *, page: int, page_size: int) -> list[ApplicationRead]:
        rows = self.session.scalars(select(Application).order_by(Application.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
        return [ApplicationRead.model_validate(item, from_attributes=True) for item in rows]

    def application(self, application_id: UUID) -> ApplicationRead:
        item = self.session.get(Application, application_id)
        if item is None:
            raise LookupError("application not found")
        return ApplicationRead.model_validate(item, from_attributes=True)

    def source_health(self) -> list[SourceHealthRead]:
        result = []
        for source in self.session.scalars(select(JobSource).order_by(JobSource.source_id)).all():
            jobs = self.session.scalar(select(func.count(Job.id)).where(Job.source_id == source.id)) or 0
            failures = self.session.scalar(select(func.count(ExecutionEvent.id)).where(ExecutionEvent.source_id == source.source_id, ExecutionEvent.status == "FAILURE")) or 0
            result.append(SourceHealthRead(source_id=source.source_id, display_name=source.display_name, health_status=source.health_status, enabled=source.enabled, last_successful_run=source.last_successful_run, jobs_discovered=jobs, jobs_persisted=jobs, failures=failures))
        return result

    def source_health_summary(self) -> SourceHealthSummaryRead:
        """Aggregate enabled ACTIVE sources only; UNKNOWN is never reported green."""
        sources = self.session.scalars(select(JobSource).where(JobSource.enabled.is_(True), JobSource.lifecycle_status == "ACTIVE")).all()
        statuses = [source.health_status if source.health_status in {"HEALTHY", "UNHEALTHY", "BLOCKED"} else "UNKNOWN" for source in sources]
        counts = {value: statuses.count(value) for value in ("HEALTHY", "UNHEALTHY", "BLOCKED", "UNKNOWN")}
        aggregate = "UNKNOWN" if not statuses else "UNHEALTHY" if counts["UNHEALTHY"] else "BLOCKED" if counts["BLOCKED"] else "UNKNOWN" if counts["UNKNOWN"] else "HEALTHY"
        return SourceHealthSummaryRead(health_status=aggregate, sources_considered=len(statuses), healthy=counts["HEALTHY"], unhealthy=counts["UNHEALTHY"], blocked=counts["BLOCKED"], unknown=counts["UNKNOWN"])

    def _compose_jobs(self, jobs: list[Job]) -> list[JobRead]:
        if not jobs:
            return []
        ids = [item.id for item in jobs]
        analyses = self._latest_by_job(JobAnalysis, ids)
        matches = self._latest_by_job(MatchResult, ids)
        applications = {item.job_id: item.status for item in self.session.scalars(select(Application).where(Application.job_id.in_(ids))).all()}
        result = []
        for item in jobs:
            analysis = analyses.get(item.id)
            candidate = matches.get(item.id)
            # Never display an older match as current after a newer analysis.
            latest = candidate if analysis is not None and candidate is not None and candidate.analysis_id == analysis.id else None
            eligible, reason = cover_letter_eligibility(self.session, job=item, match=latest, profile_path=PROFILE_PATH)
            result.append(self._job(item, analysis, latest, applications.get(item.id), eligible, reason))
        return result

    def _latest_by_job(self, model, job_ids: list[UUID]):
        rows = self.session.scalars(select(model).where(model.job_id.in_(job_ids)).order_by(model.created_at.desc(), model.id.desc())).all()
        result = {}
        for row in rows:
            result.setdefault(row.job_id, row)
        return result

    def _in_view(self, item: JobRead, view: str) -> bool:
        if view == "all":
            return True
        if view == "unseen":
            return item.seen_at is None
        if view == "applied":
            return item.is_applied
        return item.analysis_status == "COMPLETED" and item.latest_analysis is not None and item.latest_match is not None and item.match_score is not None and item.match_score >= minimum_fit_preference(self.session) and not item.is_applied and item.availability_status == "ACTIVE" and item.source_lifecycle_status == "ACTIVE" and item.source_enabled

    @staticmethod
    def _sort_key(item: JobRead, sort: str):
        if sort == "best_fit":
            return (-(item.match_score if item.match_score is not None else -1), -item.first_seen_at.timestamp(), str(item.id))
        if sort == "newest_posted":
            # Known dates first; null dates are ordered by discovery, never inferred.
            return (0 if item.date_posted is not None else 1, -(item.date_posted.toordinal() if item.date_posted else 0), -item.first_seen_at.timestamp(), str(item.id))
        return (-item.first_seen_at.timestamp(), str(item.id))

    @staticmethod
    def _job(item: Job, analysis: JobAnalysis | None = None, latest: MatchResult | None = None, application_status: str | None = None, cover_eligible: bool = False, cover_reason: str = "NOT_ANALYZED") -> JobRead:
        source = item.source
        company = item.company or source.company
        analysis_read = None if analysis is None else AnalysisSummaryRead(id=analysis.id, created_at=analysis.created_at, seniority_level=analysis.seniority_level, language_requirement=analysis.language_requirement, must_have=list(analysis.must_have or []), nice_to_have=list(analysis.nice_to_have or []))
        match_read = None if latest is None else MatchSummaryRead(id=latest.id, analysis_id=latest.analysis_id, overall_score=latest.overall_score, confidence=latest.confidence, recommendation=latest.recommendation, matched_requirements=list(latest.matched_requirements or []), partial_matches=list(latest.partial_matches or []), gaps=list(latest.gaps or []), critical_gaps=list(latest.critical_gaps or []), created_at=latest.created_at)
        return JobRead(id=item.id, source=source.source_id, source_id=source.id, source_lifecycle_status=source.lifecycle_status, source_enabled=source.enabled, title=item.title, source_job_url=item.source_job_url, canonical_url=item.canonical_url, description=item.description, location_raw=item.location_raw, city=item.city, country_code=item.country_code, employment_type=item.employment_type, availability_status=item.availability_status, first_seen_at=item.first_seen_at, last_seen_at=item.last_seen_at, date_posted=item.date_posted, seen_at=item.seen_at, application_status=application_status, is_applied=application_status in APPLIED_APPLICATION_STATUSES, company_id=company.id if company else item.company_id, company_name=company.name if company else source.display_name, language=item.language, seniority_level=item.seniority_level, is_remote=item.is_remote, is_hybrid=item.is_hybrid, starred=item.starred, analysis_status=item.analysis_status, latest_analysis=analysis_read, latest_match=match_read, cover_letter=CoverLetterEligibilityRead(eligible=cover_eligible, reason=cover_reason), match_score=latest.overall_score if latest else None, recommendation=latest.recommendation if latest else None, matched_requirements=list(latest.matched_requirements or []) if latest else [], gaps=list(latest.gaps or []) if latest else [])
