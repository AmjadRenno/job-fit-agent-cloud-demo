from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DashboardPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class AnalysisSummaryRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    created_at: datetime
    seniority_level: str | None
    language_requirement: str | None
    must_have: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)


class MatchSummaryRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    analysis_id: UUID | None
    overall_score: int
    confidence: str
    recommendation: str
    matched_requirements: list[str] = Field(default_factory=list)
    partial_matches: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    critical_gaps: list[str] = Field(default_factory=list)
    created_at: datetime


class CoverLetterEligibilityRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligible: bool
    reason: str


class JobRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    source: str
    source_id: UUID
    source_lifecycle_status: str
    source_enabled: bool
    title: str
    source_job_url: str
    canonical_url: str | None
    description: str | None
    location_raw: str | None
    city: str | None
    country_code: str | None
    employment_type: str | None
    availability_status: str
    first_seen_at: datetime
    last_seen_at: datetime
    date_posted: date | None = None
    seen_at: datetime | None = None
    # Application-tracking surface (Phase 12 fix #11): the current application
    # state for this job, when one exists; None means not tracked yet.
    application_status: str | None = None
    is_applied: bool = False
    company_id: UUID | None = None
    company_name: str | None = None
    language: str | None = None
    seniority_level: str | None = None
    is_remote: bool | None = None
    is_hybrid: bool | None = None
    starred: bool = False
    analysis_status: str
    latest_analysis: AnalysisSummaryRead | None = None
    latest_match: MatchSummaryRead | None = None
    cover_letter: CoverLetterEligibilityRead
    match_score: int | None = None
    recommendation: str | None = None
    matched_requirements: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class JobPage(DashboardPage):
    items: list[JobRead]


class MatchRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    job_id: UUID
    analysis_id: UUID | None
    overall_score: int
    confidence: str
    recommendation: str
    critical_gaps: list[str] | None
    reasoning: str | None
    evidence: dict | None
    model: str
    prompt_version: str
    created_at: datetime


class AnalysisRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    job_id: UUID
    run_id: UUID | None
    model: str
    prompt_version: str
    analysis_result: dict
    created_at: datetime


class HistoryRead(BaseModel):
    analyses: list[AnalysisRead]
    matches: list[MatchRead]


class RunRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    started_at: datetime
    ended_at: datetime | None
    status: str
    sources_total: int
    sources_success: int
    sources_partial: int
    sources_failed: int
    jobs_discovered: int
    jobs_new: int
    jobs_analyzed: int
    jobs_matched: int
    jobs_quarantined: int


class EventRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    run_id: UUID
    source_id: str | None
    job_id: UUID | None
    event_type: str
    status: str
    error_code: str | None
    duration_ms: int | None
    created_at: datetime


class SourceHealthRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    display_name: str
    health_status: str
    enabled: bool
    last_successful_run: datetime | None
    jobs_discovered: int
    jobs_persisted: int
    failures: int


class SourceHealthSummaryRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    health_status: str
    sources_considered: int
    healthy: int
    unhealthy: int
    blocked: int
    unknown: int


class ApplicationRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    job_id: UUID
    status: str
    notes: str | None
    applied_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DashboardSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_jobs: JobPage
    matching_jobs: JobPage
    runs: list[RunRead]
    source_health: list[SourceHealthRead]
