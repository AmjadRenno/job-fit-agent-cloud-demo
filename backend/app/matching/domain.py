from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class MatchClassification(StrEnum):
    DIRECT = "DIRECT"
    TRANSFERABLE = "TRANSFERABLE"
    DEVELOPING = "DEVELOPING"
    COURSEWORK = "COURSEWORK"
    FAMILIARITY = "FAMILIARITY"
    MISSING = "MISSING"


class RequirementMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement: str = Field(min_length=1)
    importance: str = Field(pattern="^(required|preferred|unknown)$")
    classification: MatchClassification
    candidate_evidence_ids: list[str] = Field(default_factory=list)
    job_evidence: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    deterministic_weight: float = Field(ge=0, le=1)
    score_contribution: float = Field(ge=0, le=1)


class MatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    analysis_id: str
    candidate_profile_version: str
    requirements: list[RequirementMatch]
    overall_score: float = Field(ge=0, le=100)
    confidence: str = Field(pattern="^(HIGH|MEDIUM|LOW)$")
    model_confidence: str = Field(pattern="^(HIGH|MEDIUM|LOW)$")
    evidence_completeness: str = Field(pattern="^(HIGH|MEDIUM|LOW)$")
    matching_version: str = Field(min_length=1)
    recommendation: str = Field(pattern="^(APPLY|CONSIDER|LOW_PRIORITY|SKIP)$")
    decision_reasons: list[str] = Field(min_length=1, max_length=3)
    critical_gaps: list[str] = Field(default_factory=list)
    next_step: str = Field(min_length=1)
