from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class CompanyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=1, max_length=255)
    country: str = Field(min_length=1, max_length=100)


class CompanyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    domain: str | None = Field(default=None, min_length=1, max_length=255)
    country: str | None = Field(default=None, min_length=1, max_length=100)


class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    official_jobs_url: HttpUrl
    canonical_url: HttpUrl | None = None
    allowed_domains: list[str] = Field(min_length=1, max_length=20)
    robots_txt_url: HttpUrl | None = None
    crawl_delay_seconds: int = Field(default=5, ge=0, le=3600)


class SourcePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    official_jobs_url: HttpUrl | None = None
    canonical_url: HttpUrl | None = None
    allowed_domains: list[str] | None = Field(default=None, min_length=1, max_length=20)
    robots_txt_url: HttpUrl | None = None
    crawl_delay_seconds: int | None = Field(default=None, ge=0, le=3600)


class CompanySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    domain: str
    country: str
    lifecycle_status: str


class SourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    source_id: str
    display_name: str
    lifecycle_status: str
    readiness_status: str
    health_status: str
    canonical_url: str | None
    enabled: bool


class CompanyRead(CompanySummary):
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    sources: list[SourceSummary]
    source_count: int = 0
    primary_careers_url: str | None = None
    last_successful_run: datetime | None = None
    job_count: int = 0
    unseen_job_count: int = 0
    recommended_job_count: int = 0
    health_status: str = "UNKNOWN"


class SourceRead(SourceSummary):
    company_id: UUID | None
    official_jobs_url: str
    allowed_domains: list[str]
    robots_txt_url: str | None
    crawl_delay_seconds: int
    boundary_fingerprint: str | None
    last_validated_at: datetime | None
    approved_preview_version: int | None
    approved_at: datetime | None
    archived_at: datetime | None
    last_successful_run: datetime | None
    created_at: datetime
    updated_at: datetime


class PreviewRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    source_id: UUID
    version: int
    status: str
    boundary_fingerprint: str
    source_url: str | None
    canonical_url: str | None
    lifecycle_status: str
    readiness_status: str
    validation_summary: dict[str, Any]
    created_at: datetime
    expires_at: datetime | None
    approved_at: datetime | None
    discarded_at: datetime | None


class OnboardingCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl | None = None


class SourceApprovalCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_version: int = Field(ge=1)


class SourceList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CompanyRead]
