from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FitCategory(StrEnum):
    STRONG = "STRONG"
    GOOD = "GOOD"
    POSSIBLE = "POSSIBLE"
    WEAK = "WEAK"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    source: Literal["candidate_profile", "job", "deterministic_rule", "inference"]
    claim: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    classification: str | None = None
    confidence: Confidence | None = None


class JobAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_fit: int = Field(ge=0, le=100)
    fit_category: FitCategory
    matching_requirements: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    transferable_skills: list[str] = Field(default_factory=list)
    experience_alignment: str = Field(min_length=1)
    education_alignment: str = Field(min_length=1)
    location_alignment: str = Field(min_length=1)
    work_arrangement_alignment: str = Field(min_length=1)
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: Confidence
    analysis_version: str = Field(min_length=1)

    @field_validator("matching_requirements", "missing_requirements", "transferable_skills", "strengths", "concerns")
    @classmethod
    def reject_blank_items(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]
