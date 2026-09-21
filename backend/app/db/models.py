from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class GUID(TypeDecorator[uuid.UUID]):
    impl = CHAR(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return value if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value, dialect):
        return None if value is None else uuid.UUID(str(value))


class StringList(TypeDecorator[list[str]]):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(ARRAY(Text))
        return dialect.type_descriptor(JSON)


class JSONValue(TypeDecorator[Any]):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB)
        return dialect.type_descriptor(JSON)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Company(TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint("lifecycle_status IN ('ACTIVE','ARCHIVED')", name="ck_companies_lifecycle_status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    country: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(Text, nullable=False, default="ACTIVE", server_default="ACTIVE")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job_sources: Mapped[list[JobSource]] = relationship(back_populates="company")
    jobs: Mapped[list[Job]] = relationship(back_populates="company")


class JobSource(TimestampMixin, Base):
    __tablename__ = "job_sources"
    __table_args__ = (
        CheckConstraint(
            "lifecycle_status IN ('DRAFT','ONBOARDING','PREVIEW_READY','ACTIVE','DISABLED','REVALIDATION_REQUIRED','ARCHIVED')",
            name="ck_job_sources_lifecycle_status",
        ),
        CheckConstraint(
            "readiness_status IN ('UNKNOWN','READY','BLOCKED')",
            name="ck_job_sources_readiness_status",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id"))
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    official_jobs_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(Text, unique=True)
    allowed_domains: Mapped[list[str]] = mapped_column(StringList(), nullable=False)
    robots_txt_url: Mapped[str | None] = mapped_column(Text)
    crawl_delay_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lifecycle_status: Mapped[str] = mapped_column(Text, nullable=False, default="DRAFT", server_default="DRAFT")
    readiness_status: Mapped[str] = mapped_column(Text, nullable=False, default="UNKNOWN", server_default="UNKNOWN")
    boundary_fingerprint: Mapped[str | None] = mapped_column(Text)
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_preview_version: Mapped[int | None] = mapped_column(Integer)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    health_status: Mapped[str] = mapped_column(Text, nullable=False, default="UNKNOWN")
    company: Mapped[Company | None] = relationship(back_populates="job_sources")
    jobs: Mapped[list[Job]] = relationship(back_populates="source")
    previews: Mapped[list[SourcePreview]] = relationship(back_populates="source")


class SourcePreview(Base):
    __tablename__ = "source_previews"
    __table_args__ = (
        UniqueConstraint("source_id", "version", name="uq_source_previews_source_version"),
        CheckConstraint(
            "status IN ('READY','APPROVED','DISCARDED','EXPIRED','INVALID')",
            name="ck_source_previews_status",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_sources.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    boundary_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="READY", server_default="READY")
    validation_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONValue())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[JobSource] = relationship(back_populates="previews")


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source_id", "external_job_id", name="uq_jobs_source_external"),
        UniqueConstraint("source_id", "source_job_url", name="uq_jobs_source_url"),
        UniqueConstraint("source_id", "canonical_url", name="uq_jobs_source_canonical_url"),
        CheckConstraint("employment_type IS NULL OR employment_type IN ('FULL_TIME','PART_TIME','STUDENT','CONTRACT','UNKNOWN')", name="ck_jobs_employment_type"),
        CheckConstraint("availability_status IN ('ACTIVE','CLOSED','EXPIRED','FILLED','UNAVAILABLE','UNKNOWN')", name="ck_jobs_availability_status"),
        Index("idx_jobs_source_id", "source_id"),
        Index("idx_jobs_availability", "availability_status"),
        Index("idx_jobs_first_seen", "first_seen_at"),
        Index("idx_jobs_content_hash", "content_hash"),
        Index("idx_jobs_country_city", "country_code", "city"),
    )
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_sources.id"), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id"))
    external_job_id: Mapped[str | None] = mapped_column(Text)
    source_job_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    raw_content: Mapped[str | None] = mapped_column(Text)
    location_raw: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(CHAR(2))
    is_hybrid: Mapped[bool | None] = mapped_column(Boolean)
    is_remote: Mapped[bool | None] = mapped_column(Boolean)
    employment_type: Mapped[str | None] = mapped_column(Text, default="UNKNOWN")
    seniority_level: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    date_posted: Mapped[date | None] = mapped_column(Date)
    application_deadline: Mapped[date | None] = mapped_column(Date)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(Text)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    availability_status: Mapped[str] = mapped_column(Text, nullable=False, default="UNKNOWN")
    # Persistent analysis guard (Phase 12 fix #6): set to COMPLETED by the
    # analysis service after a validated analysis is persisted; the processing
    # pipeline skips re-analysis while it is COMPLETED and the content hash is
    # unchanged. Never used for evaluation semantics.
    analysis_status: Mapped[str] = mapped_column(Text, nullable=False, default="PENDING", server_default="PENDING")
    starred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[JobSource] = relationship(back_populates="jobs")
    company: Mapped[Company | None] = relationship(back_populates="jobs")


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="RUNNING")
    sources_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sources_success: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sources_partial: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sources_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_analyzed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_matched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_quarantined: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)


class JobAnalysis(Base):
    __tablename__ = "job_analyses"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    responsibilities: Mapped[list[str] | None] = mapped_column(StringList())
    must_have: Mapped[list[str] | None] = mapped_column(StringList())
    nice_to_have: Mapped[list[str] | None] = mapped_column(StringList())
    technologies: Mapped[list[str] | None] = mapped_column(StringList())
    seniority_level: Mapped[str | None] = mapped_column(Text)
    language_requirement: Mapped[str | None] = mapped_column(Text)
    key_constraints: Mapped[list[str] | None] = mapped_column(StringList())
    analysis_notes: Mapped[str | None] = mapped_column(Text)
    analysis_result: Mapped[dict[str, Any]] = mapped_column(JSONValue(), nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_used: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    schema_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    validation_errors: Mapped[dict[str, Any] | None] = mapped_column(JSONValue())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    profile_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    profile_data: Mapped[dict[str, Any] | None] = mapped_column(JSONValue())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MatchResult(Base):
    __tablename__ = "match_results"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_analyses.id"))
    candidate_profile_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("candidate_profiles.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    overall_score: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    matched_requirements: Mapped[list[str] | None] = mapped_column(StringList())
    partial_matches: Mapped[list[str] | None] = mapped_column(StringList())
    gaps: Mapped[list[str] | None] = mapped_column(StringList())
    critical_gaps: Mapped[list[str] | None] = mapped_column(StringList())
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONValue())
    reasoning: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    tokens_used: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        CheckConstraint("status IN ('DISCOVERED','INTERESTING','TO_APPLY','APPLIED','INTERVIEW','REJECTED','OFFER','CLOSED','IGNORED')", name="ck_applications_status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False, unique=True)
    match_result_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("match_results.id"))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="DISCOVERED")
    notes: Mapped[str | None] = mapped_column(Text)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ApplicationEvent(Base):
    __tablename__ = "application_events"
    __table_args__ = (
        CheckConstraint("triggered_by IN ('HUMAN','SYSTEM')", name="ck_application_events_triggered_by"),
    )
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text, nullable=False)
    triggered_by: Mapped[str] = mapped_column(Text, nullable=False, default="HUMAN")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CoverLetter(Base):
    __tablename__ = "cover_letters"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    application_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("applications.id"))
    match_result_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("match_results.id"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    grounding_valid: Mapped[bool | None] = mapped_column(Boolean)
    grounding_notes: Mapped[str | None] = mapped_column(Text)
    human_reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    human_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    source_id: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONValue())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    evaluation_date: Mapped[date] = mapped_column(Date, nullable=False)
    dataset_version: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_size: Mapped[int] = mapped_column(Integer, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONValue(), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
