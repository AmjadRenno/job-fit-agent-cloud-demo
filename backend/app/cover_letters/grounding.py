from __future__ import annotations

from .domain import (
    ClaimClassification,
    CoverLetterDraft,
    GroundedClaimEvidenceMapping,
    LetterGroundingStatus,
)
from backend.app.matching.evidence import CandidateEvidenceIndex


class GroundingError(ValueError):
    pass


_ALLOWED_CLASSIFICATIONS = {
    "strong_hands_on": ClaimClassification.DIRECT,
    "significant_project": ClaimClassification.TRANSFERABLE,
    "developing": ClaimClassification.DEVELOPING,
    "coursework": ClaimClassification.COURSEWORK,
    "familiarity": ClaimClassification.FAMILIARITY,
}

# Strength order, strongest first. Declaration order of ClaimClassification
# already matches this (DIRECT > TRANSFERABLE > DEVELOPING > COURSEWORK >
# FAMILIARITY), so its index is used directly as a rank.
_CLASSIFICATION_RANK = {member: index for index, member in enumerate(ClaimClassification)}


def derive_claim_text(evidence_index: CandidateEvidenceIndex, evidence_ids: list[str]) -> str:
    """Deterministic candidate claim wording, never model-authored.

    The claim text is copied verbatim from the cited evidence items, deduplicated
    and ordered by evidence ID so the result is stable regardless of the order the
    model chose. Unknown IDs raise GroundingError so they surface through the
    existing 422 error contract instead of being silently ignored.
    """
    known = {item.evidence_id: item for item in evidence_index.evidence}
    claims: list[str] = []
    for evidence_id in sorted(set(evidence_ids)):
        item = known.get(evidence_id)
        if item is None:
            raise GroundingError(f"unknown candidate evidence ID: {evidence_id}")
        claims.append(item.claim)
    return "; ".join(claims)


def validate_grounding(draft: CoverLetterDraft, evidence_index: CandidateEvidenceIndex) -> CoverLetterDraft:
    known = {item.evidence_id: item for item in evidence_index.evidence}
    for mapping in draft.claim_evidence_mappings:
        if not isinstance(mapping, GroundedClaimEvidenceMapping):
            raise GroundingError("claim wording was not derived from cited evidence")
        items = [known.get(evidence_id) for evidence_id in mapping.evidence_ids]
        if any(item is None for item in items):
            raise GroundingError(f"unknown candidate evidence ID for claim: {mapping.claim}")
        if mapping.claim != derive_claim_text(evidence_index, mapping.evidence_ids):
            raise GroundingError(f"claim wording does not match cited evidence: {mapping.claim}")
        # The weakest cited evidence item is the ceiling for the whole claim: a
        # claim citing several items (e.g. one strong, unrelated item plus one
        # weak, relevant item) must be graded at the weakest item's level, not
        # promoted using whichever cited item happens to be strongest.
        allowed = [_ALLOWED_CLASSIFICATIONS[item.classification] for item in items if item]
        weakest = max(allowed, key=lambda classification: _CLASSIFICATION_RANK[classification])
        if mapping.classification != weakest:
            raise GroundingError(f"claim classification is inconsistent with evidence: {mapping.claim}")
    if draft.grounding_status != LetterGroundingStatus.GROUNDED:
        raise GroundingError("cover letter is not marked GROUNDED")
    return draft
