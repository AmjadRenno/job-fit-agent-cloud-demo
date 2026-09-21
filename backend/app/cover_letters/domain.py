from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class LetterGroundingStatus(StrEnum):
    GROUNDED = "GROUNDED"
    REJECTED = "REJECTED"


class ClaimClassification(StrEnum):
    DIRECT = "DIRECT"
    TRANSFERABLE = "TRANSFERABLE"
    DEVELOPING = "DEVELOPING"
    COURSEWORK = "COURSEWORK"
    FAMILIARITY = "FAMILIARITY"


class ClaimEvidenceMapping(BaseModel):
    """Model-authored evidence selection only.

    Candidate claim wording is never authored by the model: the application
    derives it verbatim from the cited evidence (GroundedClaimEvidenceMapping),
    so unsupported overclaim wording is structurally impossible to persist.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_ids: list[str] = Field(min_length=1)
    classification: ClaimClassification
    rationale: str = Field(min_length=1)


class GroundedClaimEvidenceMapping(ClaimEvidenceMapping):
    """Derived mapping: claim text is copied verbatim from the cited evidence."""

    claim: str = Field(min_length=1)


class CoverLetterDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cover_letter: str = Field(min_length=1)
    claim_evidence_mappings: list[ClaimEvidenceMapping] = Field(min_length=1)
    grounding_status: LetterGroundingStatus
    validation_notes: list[str] = Field(default_factory=list)
    analysis_version: str = Field(min_length=1)
    matching_version: str = Field(min_length=1)
