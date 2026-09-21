from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.db.models import CandidateProfile, CoverLetter, Job, JobAnalysis, MatchResult
from backend.app.matching.evidence import CandidateEvidenceIndex

from .domain import CoverLetterDraft, GroundedClaimEvidenceMapping
from .grounding import derive_claim_text, validate_grounding
from .llm import PROMPT_VERSION, CoverLetterGenerator


class CoverLetterAuthorizationError(PermissionError):
    pass


class CoverLetterPreconditionError(ValueError):
    pass


def cover_letter_eligibility(
    session: Session,
    *,
    job: Job,
    match: MatchResult | None,
    profile_path: Path,
    require_completed_status: bool = True,
) -> tuple[bool, str]:
    """Return the same grounding prerequisite used by generation.

    This is deliberately a read-only check.  The dashboard can explain why a
    button is unavailable without duplicating or weakening generation rules.
    """
    if require_completed_status and job.analysis_status != "COMPLETED":
        return False, "NOT_ANALYZED"
    if match is None or match.job_id != job.id or match.analysis_id is None:
        return False, "NO_VALID_MATCH"
    analysis = session.get(JobAnalysis, match.analysis_id)
    if analysis is None or analysis.job_id != job.id:
        return False, "NO_VALID_MATCH"
    if match.candidate_profile_id is None or session.get(CandidateProfile, match.candidate_profile_id) is None:
        return False, "PROFILE_VERSION_UNAVAILABLE"
    evidence_index = CandidateEvidenceIndex.from_profile(profile_path)
    known = {item.evidence_id for item in evidence_index.evidence}
    evidence = match.evidence if isinstance(match.evidence, dict) else {}
    requirements = evidence.get("requirements", [])
    if not isinstance(requirements, list) or any(not isinstance(item, dict) for item in requirements):
        return False, "GROUNDING_EVIDENCE_UNAVAILABLE"
    evidence_ids = {
        evidence_id
        for requirement in requirements
        for evidence_id in requirement.get("candidate_evidence_ids", [])
    }
    if not evidence_ids or any(not isinstance(item, str) or item not in known for item in evidence_ids):
        return False, "GROUNDING_EVIDENCE_UNAVAILABLE"
    return True, "ELIGIBLE"


class CoverLetterService:
    def __init__(self, session: Session, generator: CoverLetterGenerator) -> None:
        self.session = session
        self.generator = generator

    def generate_for_human(
        self,
        *,
        job_id: UUID,
        match_id: UUID,
        profile_path: Path,
        human_authorized: bool,
    ) -> CoverLetterDraft:
        if not human_authorized:
            raise CoverLetterAuthorizationError("human authorization is required")
        job = self.session.get(Job, job_id)
        match = self.session.get(MatchResult, match_id)
        if job is None or match is None:
            raise CoverLetterPreconditionError("job and match are required")
        if match.job_id != job.id:
            raise CoverLetterPreconditionError("match does not belong to selected job")
        eligible, reason = cover_letter_eligibility(
            self.session, job=job, match=match, profile_path=profile_path,
            # Generation preserves the existing structural analysis/match gate.
            # The dashboard's stricter status-facing explanation is read-only.
            require_completed_status=False,
        )
        if not eligible:
            messages = {
                "NOT_ANALYZED": "job must be analyzed before generating a cover letter",
                "NO_VALID_MATCH": "match must reference a valid analysis",
                "PROFILE_VERSION_UNAVAILABLE": "candidate profile version is unavailable",
                "GROUNDING_EVIDENCE_UNAVAILABLE": "match references unresolved candidate evidence",
            }
            raise CoverLetterPreconditionError(messages[reason])
        analysis = self.session.get(JobAnalysis, match.analysis_id)
        assert analysis is not None
        profile_row = self.session.get(CandidateProfile, match.candidate_profile_id)
        assert profile_row is not None
        evidence_index = CandidateEvidenceIndex.from_profile(profile_path)
        known = {item.evidence_id: item for item in evidence_index.evidence}
        match_evidence = match.evidence if isinstance(match.evidence, dict) else {}
        requirements = match_evidence.get("requirements", [])
        if not isinstance(requirements, list) or any(not isinstance(requirement, dict) for requirement in requirements):
            raise CoverLetterPreconditionError("match evidence is not structurally valid")
        evidence_ids = {
            evidence_id
            for requirement in requirements
            if isinstance(requirement, dict)
            for evidence_id in requirement.get("candidate_evidence_ids", [])
        }
        if not evidence_ids or any(evidence_id not in known for evidence_id in evidence_ids):
            raise CoverLetterPreconditionError("match references unresolved candidate evidence")
        bounded_evidence = [known[evidence_id] for evidence_id in sorted(evidence_ids)]
        context = {
            "job_title": job.title,
            "requirements": [requirement for requirement in requirements if isinstance(requirement, dict)],
            "job_evidence": [requirement.get("job_evidence", "") for requirement in requirements if isinstance(requirement, dict)],
            "candidate_evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "claim": item.claim,
                    "classification": item.classification,
                    "section": item.section,
                    "confidence": item.confidence,
                }
                for item in bounded_evidence
            ],
        }
        draft = CoverLetterDraft.model_validate(self.generator.generate(context))
        # Claim wording is never model-authored: derive each mapping's claim text
        # verbatim from the cited evidence (deterministic; unknown IDs raise
        # GroundingError through the existing 422 contract) before grounding
        # validates wording equality and classification consistency.
        grounded_draft = CoverLetterDraft(
            cover_letter=draft.cover_letter,
            claim_evidence_mappings=[
                GroundedClaimEvidenceMapping(
                    evidence_ids=mapping.evidence_ids,
                    classification=mapping.classification,
                    rationale=mapping.rationale,
                    claim=derive_claim_text(evidence_index, mapping.evidence_ids),
                )
                for mapping in draft.claim_evidence_mappings
            ],
            grounding_status=draft.grounding_status,
            validation_notes=draft.validation_notes,
            analysis_version=draft.analysis_version,
            matching_version=draft.matching_version,
        )
        validated = validate_grounding(grounded_draft, evidence_index)
        row = CoverLetter(
            job_id=job.id,
            application_id=None,
            match_result_id=match.id,
            content=validated.cover_letter,
            model=self.generator.model,
            prompt_version=PROMPT_VERSION,
            grounding_valid=True,
            grounding_notes=json.dumps({
                "grounding_status": validated.grounding_status,
                "claim_evidence_mappings": [mapping.model_dump(mode="json") for mapping in validated.claim_evidence_mappings],
                "analysis_version": validated.analysis_version,
                "matching_version": validated.matching_version,
                "candidate_profile_version": profile_row.version,
            }),
            human_reviewed=False,
        )
        self.session.add(row)
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return validated

    def review(self, letter_id: UUID, *, human_authorized: bool, human_reviewed: bool, human_notes: str | None = None) -> CoverLetter:
        if not human_authorized:
            raise CoverLetterAuthorizationError("human authorization is required")
        letter = self.session.get(CoverLetter, letter_id)
        if letter is None:
            raise LookupError("cover letter not found")
        # Human review metadata only: generated content, grounding metadata,
        # and model/prompt versions remain immutable.
        letter.human_reviewed = human_reviewed
        letter.human_notes = human_notes
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return letter


def profile_version(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
