from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from backend.app.db.models import AgentRun, Company, ExecutionEvent, Job, JobSource, SourcePreview
from backend.app.sources.lifecycle import (
    InvalidSourceTransition,
    archive_company,
    mark_boundary_changed,
    preview_can_activate,
    transition_source,
)
from backend.app.sources.onboarding import OnboardingError, OnboardingTrialService

from .schemas import (
    CompanyCreate,
    CompanyPatch,
    CompanyRead,
    PreviewRead,
    SourceCreate,
    SourcePatch,
    SourceRead,
    SourceSummary,
)
from .domain import normalize_company_domain


BOUNDARY_FIELDS = frozenset({
    "official_jobs_url",
    "canonical_url",
    "allowed_domains",
    "robots_txt_url",
})


class SourceManagementError(ValueError):
    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


class SourceManagementService:
    def __init__(
        self,
        session: Session,
        *,
        onboarding_factory: Any = OnboardingTrialService,
    ) -> None:
        self.session = session
        self.onboarding_factory = onboarding_factory

    def create_company(self, command: CompanyCreate) -> CompanyRead:
        company = Company(name=command.name.strip(), domain=normalize_company_domain(command.domain), country=command.country.strip())
        self.session.add(company)
        self._flush_integrity("company already exists")
        self._commit()
        return self.company_read(company)

    def list_companies(self) -> list[CompanyRead]:
        companies = self.session.scalars(
            select(Company).options(selectinload(Company.job_sources)).order_by(Company.name)
        ).all()
        return [self.company_read(company) for company in companies]

    def get_company(self, company_id: UUID) -> CompanyRead:
        company = self.session.scalar(
            select(Company).options(selectinload(Company.job_sources)).where(Company.id == company_id)
        )
        if company is None:
            raise LookupError("company not found")
        return self.company_read(company)

    def update_company(self, company_id: UUID, command: CompanyPatch) -> CompanyRead:
        company = self._company(company_id)
        values = command.model_dump(exclude_unset=True)
        for field, value in values.items():
            setattr(company, field, normalize_company_domain(value) if field == "domain" else value.strip())
        self._flush_integrity("company domain already exists")
        self._commit()
        return self.get_company(company_id)

    def archive_company(self, company_id: UUID) -> CompanyRead:
        company = self._company(company_id, with_sources=True)
        archive_company(company, list(company.job_sources))
        self.session.flush()
        self._commit()
        return self.get_company(company_id)

    def create_source(self, company_id: UUID, command: SourceCreate) -> SourceRead:
        company = self._company(company_id)
        if company.lifecycle_status == "ARCHIVED":
            raise SourceManagementError("archived company cannot receive new sources")
        official_url = self._validated_url(str(command.official_jobs_url))
        canonical_url = self._validated_url(str(command.canonical_url)) if command.canonical_url else official_url
        source = JobSource(
            source_id=command.source_id.strip(),
            company_id=company.id,
            display_name=command.display_name.strip(),
            official_jobs_url=official_url,
            canonical_url=canonical_url,
            allowed_domains=[domain.strip().lower() for domain in command.allowed_domains],
            robots_txt_url=str(command.robots_txt_url) if command.robots_txt_url else None,
            crawl_delay_seconds=command.crawl_delay_seconds,
            enabled=False,
            lifecycle_status="DRAFT",
            readiness_status="UNKNOWN",
        )
        self.session.add(source)
        self._flush_integrity("source identity or canonical URL already exists")
        self._commit()
        return self.source_read(source)

    def get_source(self, source_id: UUID) -> SourceRead:
        return self.source_read(self._source(source_id))

    def update_source(self, source_id: UUID, command: SourcePatch) -> SourceRead:
        source = self._source(source_id)
        values = command.model_dump(exclude_unset=True)
        boundary_changed = bool(BOUNDARY_FIELDS.intersection(values))
        if boundary_changed and source.lifecycle_status == "ACTIVE":
            mark_boundary_changed(source)
        if "official_jobs_url" in values:
            values["official_jobs_url"] = self._validated_url(str(values["official_jobs_url"]))
        if "canonical_url" in values and values["canonical_url"] is not None:
            values["canonical_url"] = self._validated_url(str(values["canonical_url"]))
        if "allowed_domains" in values:
            values["allowed_domains"] = [domain.strip().lower() for domain in values["allowed_domains"]]
        if "robots_txt_url" in values and values["robots_txt_url"] is not None:
            values["robots_txt_url"] = str(values["robots_txt_url"])
        for field, value in values.items():
            setattr(source, field, value.strip() if isinstance(value, str) else value)
        if boundary_changed and source.lifecycle_status == "PREVIEW_READY":
            source.readiness_status = "UNKNOWN"
        self._flush_integrity("source identity or canonical URL already exists")
        self._commit()
        return self.source_read(source)

    def archive_source(self, source_id: UUID) -> SourceRead:
        source = self._source(source_id)
        if source.lifecycle_status != "ARCHIVED":
            source.lifecycle_status = "ARCHIVED"
            source.enabled = False
            source.archived_at = datetime.now(timezone.utc)
        self.session.flush()
        self._commit()
        return self.source_read(source)

    def onboard(self, source_id: UUID, url: str | None = None) -> PreviewRead:
        source = self._source(source_id)
        if source.lifecycle_status == "ACTIVE":
            raise SourceManagementError("active source requires revalidation before onboarding")
        target_url = url or source.canonical_url or source.official_jobs_url
        try:
            onboarding = self.onboarding_factory(self.session)
            arguments = {
                "company_name": source.company.name if source.company else None,
                "company_domain": source.company.domain if source.company else None,
                "country": source.company.country if source.company else "Denmark",
            }
            if "source_id" in inspect.signature(onboarding.run).parameters:
                arguments["source_id"] = source.source_id
            preview = onboarding.run(target_url, **arguments)
        except OnboardingError:
            raise
        return self.preview_read(preview)

    def revalidate(self, source_id: UUID) -> PreviewRead:
        source = self._source(source_id)
        if source.lifecycle_status == "ACTIVE":
            mark_boundary_changed(source)
            self.session.flush()
            self._commit()
        return self.onboard(source_id)

    def approve(self, source_id: UUID, preview_version: int) -> SourceRead:
        source = self._source(source_id)
        if source.lifecycle_status == "ACTIVE":
            if source.approved_preview_version == preview_version:
                return self.source_read(source)
            raise SourceManagementError("source is already ACTIVE with a different approved preview")

        preview = self.session.scalar(
            select(SourcePreview).where(
                SourcePreview.source_id == source.id,
                SourcePreview.version == preview_version,
            )
        )
        if preview is None:
            raise LookupError("preview not found")

        if not preview_can_activate(source, preview):
            raise SourceManagementError("preview is not current, ready, and valid for approval")

        try:
            transition_source(
                source,
                "ACTIVE",
                preview_valid=True,
                human_approved=True,
                preview_version=preview_version,
            )
        except InvalidSourceTransition as error:
            raise SourceManagementError(str(error)) from error

        preview.approved_at = source.approved_at
        audit_run = AgentRun(
            started_at=source.approved_at,
            ended_at=source.approved_at,
            status="SUCCESS",
            sources_total=1,
            sources_success=1,
        )
        self.session.add(audit_run)
        self.session.flush()
        self.session.add(
            ExecutionEvent(
                run_id=audit_run.id,
                source_id=source.source_id,
                event_type="source_approval",
                status="SUCCESS",
                metadata_json={
                    "action": "APPROVE",
                    "result": "ACTIVE",
                    "preview_version": preview_version,
                },
            )
        )
        self.session.flush()
        self._commit()
        return self.source_read(source)

    def list_previews(self, source_id: UUID) -> list[PreviewRead]:
        self._source(source_id)
        rows = self.session.scalars(
            select(SourcePreview).where(SourcePreview.source_id == source_id).order_by(SourcePreview.version.desc())
        ).all()
        return [self.preview_read(row) for row in rows]

    def get_preview(self, source_id: UUID, preview_id: UUID) -> PreviewRead:
        self._source(source_id)
        preview = self.session.scalar(
            select(SourcePreview).where(SourcePreview.id == preview_id, SourcePreview.source_id == source_id)
        )
        if preview is None:
            raise LookupError("preview not found")
        return self.preview_read(preview)

    def _company(self, company_id: UUID, *, with_sources: bool = False) -> Company:
        query = select(Company).where(Company.id == company_id)
        if with_sources:
            query = query.options(selectinload(Company.job_sources))
        company = self.session.scalar(query)
        if company is None:
            raise LookupError("company not found")
        return company

    def _source(self, source_id: UUID) -> JobSource:
        source = self.session.scalar(select(JobSource).options(selectinload(JobSource.company)).where(JobSource.id == source_id))
        if source is None:
            raise LookupError("source not found")
        return source

    def _flush_integrity(self, message: str) -> None:
        try:
            self.session.flush()
        except IntegrityError as error:
            self.session.rollback()
            raise SourceManagementError(message) from error

    def _commit(self) -> None:
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    @staticmethod
    def _validated_url(value: str) -> str:
        from backend.app.sources.onboarding import OnboardingTrialService
        return OnboardingTrialService._validate_url(value)

    def company_read(self, company: Company) -> CompanyRead:
        """One company row owns all of its sources and aggregate read state."""
        sources = list(company.job_sources)
        source_ids = [source.id for source in sources]
        job_count = self.session.scalar(select(func.count(Job.id)).where(Job.source_id.in_(source_ids))) if source_ids else 0
        unseen_job_count = self.session.scalar(select(func.count(Job.id)).where(Job.source_id.in_(source_ids), Job.seen_at.is_(None))) if source_ids else 0
        from backend.app.dashboard.service import DashboardService
        recommended_job_count = DashboardService(self.session).jobs(
            page=1, page_size=100, view="recommended", company_id=company.id, sort="best_fit"
        ).total
        active_sources = [source for source in sources if source.lifecycle_status == "ACTIVE" and source.enabled]
        primary = (active_sources or sources or [None])[0]
        statuses = [source.health_status if source.health_status in {"HEALTHY", "UNHEALTHY", "BLOCKED"} else "UNKNOWN" for source in active_sources]
        health = "UNKNOWN" if not statuses else "UNHEALTHY" if "UNHEALTHY" in statuses else "BLOCKED" if "BLOCKED" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "HEALTHY"
        last_success = max((source.last_successful_run for source in sources if source.last_successful_run is not None), default=None)
        return CompanyRead(
            id=company.id,
            name=company.name,
            domain=company.domain,
            country=company.country,
            lifecycle_status=company.lifecycle_status,
            archived_at=company.archived_at,
            created_at=company.created_at,
            updated_at=company.updated_at,
            sources=[SourceManagementService.source_summary(source) for source in sources],
            source_count=len(sources),
            primary_careers_url=(primary.canonical_url or primary.official_jobs_url) if primary else None,
            last_successful_run=last_success,
            job_count=job_count or 0,
            unseen_job_count=unseen_job_count or 0,
            recommended_job_count=recommended_job_count,
            health_status=health,
        )

    @staticmethod
    def source_summary(source: JobSource) -> SourceSummary:
        return SourceSummary(
            id=source.id,
            source_id=source.source_id,
            display_name=source.display_name,
            lifecycle_status=source.lifecycle_status,
            readiness_status=source.readiness_status,
            health_status=source.health_status,
            canonical_url=source.canonical_url,
            enabled=source.enabled,
        )

    @staticmethod
    def source_read(source: JobSource) -> SourceRead:
        return SourceRead(
            **SourceManagementService.source_summary(source).model_dump(),
            company_id=source.company_id,
            official_jobs_url=source.official_jobs_url,
            allowed_domains=source.allowed_domains,
            robots_txt_url=source.robots_txt_url,
            crawl_delay_seconds=source.crawl_delay_seconds,
            boundary_fingerprint=source.boundary_fingerprint,
            last_validated_at=source.last_validated_at,
            approved_preview_version=source.approved_preview_version,
            approved_at=source.approved_at,
            archived_at=source.archived_at,
            last_successful_run=source.last_successful_run,
            created_at=source.created_at,
            updated_at=source.updated_at,
        )

    @staticmethod
    def preview_read(preview: SourcePreview) -> PreviewRead:
        summary = dict(preview.validation_summary or {})
        sample = []
        for item in summary.get("sample_jobs", []):
            if isinstance(item, dict):
                sample.append({key: item.get(key) for key in ("title", "url", "location", "country", "employment_type", "status", "external_job_id")})
        safe_summary = {
            key: value for key, value in summary.items() if key != "sample_jobs"
        }
        safe_summary["sample_jobs"] = sample
        source = preview.source
        return PreviewRead(
            id=preview.id,
            source_id=preview.source_id,
            version=preview.version,
            status=preview.status,
            boundary_fingerprint=preview.boundary_fingerprint,
            source_url=source.official_jobs_url if source else None,
            canonical_url=source.canonical_url if source else None,
            lifecycle_status=source.lifecycle_status if source else "UNKNOWN",
            readiness_status=source.readiness_status if source else "UNKNOWN",
            validation_summary=safe_summary,
            created_at=preview.created_at,
            expires_at=preview.expires_at,
            approved_at=preview.approved_at,
            discarded_at=preview.discarded_at,
        )
