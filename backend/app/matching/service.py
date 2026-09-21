from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.analysis.domain import JobAnalysis as AnalysisContract
from backend.app.db.models import CandidateProfile as CandidateProfileRow
from backend.app.db.models import Job, JobAnalysis as JobAnalysisRow, MatchResult as MatchResultRow
from job_fit_core.requirements import Requirement, score_requirement

from .domain import MatchClassification, MatchResult, RequirementMatch
from .decision import derive_application_decision
from .evidence import CandidateEvidenceIndex

MATCHING_VERSION = "phase14-decision-v2"

# Sources the matching contract accepts. Energinet is archived and has no
# approved execution handler; historical jobs cannot be matched.
# Mirrors ANALYSIS_ELIGIBLE_SOURCES; unapproved sources remain blocked.
# Matching deliberately performs no availability check of its own: the
# processing pipeline and the analysis gate guarantee that only explicitly
# available jobs ever possess a persisted analysis, which matching requires.
MATCHING_ELIGIBLE_SOURCES = frozenset({"solita_dk", "vestas_dk", "trifork_dk", "demo_synthetic"})

# A DIRECT match requires a strong hands-on evidence item to share at least this
# many meaningful terms with the requirement. A single generic/shared token is
# not enough to claim direct skill parity.
_MIN_DIRECT_TERMS = 2

# Additionally, DIRECT requires the shared terms to cover at least this fraction
# of the requirement's meaningful terms, so a partial domain overlap (e.g. only
# "modelling" + "design" of a multi-skill requirement) is not promoted to DIRECT.
_MIN_DIRECT_COVERAGE = 0.5

# Evidence classification strength (kept from the Phase 5 implementation).
_STRENGTH = {
    "strong_hands_on": 5,
    "significant_project": 4,
    "developing": 3,
    "coursework": 2,
    "familiarity": 1,
}

# Classification when evidence exists but does not pass the DIRECT gate.
# Strong hands-on evidence that only partially overlaps a requirement is
# transferable, not direct; project evidence is transferable by design.
_NON_DIRECT = {
    "strong_hands_on": MatchClassification.TRANSFERABLE,
    "significant_project": MatchClassification.TRANSFERABLE,
    "developing": MatchClassification.DEVELOPING,
    "coursework": MatchClassification.COURSEWORK,
    "familiarity": MatchClassification.FAMILIARITY,
}

# Terms that do not by themselves establish meaningful skill evidence. Shared
# generic words such as "experience", "data", "software", "development", or
# "systems" must not be the sole basis for a match.
_GENERIC_TERMS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "beyond",
        "by",
        "developer",
        "development",
        "data",
        "experience",
        "experienced",
        "for",
        "from",
        "good",
        "in",
        "including",
        "is",
        "it",
        "knowledge",
        "least",
        "multiple",
        "of",
        "on",
        "or",
        "plus",
        "practical",
        "preferred",
        "production",
        "required",
        "several",
        "software",
        "strong",
        "systems",
        "that",
        "the",
        "this",
        "to",
        "very",
        "with",
        "work",
        "working",
        "years",
        "year",
        "your",
    }
)

_SENIORITY_PATTERNS = (
    re.compile(r"\b(?:senior|lead|principal|staff|architect)\b", re.IGNORECASE),
    re.compile(r"\d{1,3}\s*\+?\s*(?:-|–|—)?\s*\d{0,3}\s*years?", re.IGNORECASE),
    re.compile(r"\b(?:at\s+least|minimum|min\.?)\s*\d{1,3}", re.IGNORECASE),
)


class MatchingPreconditionError(ValueError):
    pass


class JobMatchingService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def match_job(self, job_id: Any, profile_path: Path, run_id: Any = None) -> MatchResult:
        job = self.session.get(Job, job_id)
        if job is None:
            raise LookupError(f"job not found: {job_id}")
        if job.source.source_id not in MATCHING_ELIGIBLE_SOURCES:
            raise MatchingPreconditionError(
                f"source {job.source.source_id!r} is not approved for matching; "
                f"approved sources: {sorted(MATCHING_ELIGIBLE_SOURCES)}"
            )
        if job.source.source_id == "trifork_dk" and (
            job.source.lifecycle_status != "ACTIVE" or not job.source.enabled
        ):
            raise MatchingPreconditionError("Trifork source requires human approval before matching")
        analysis_row = self.session.scalar(
            select(JobAnalysisRow).where(JobAnalysisRow.job_id == job.id).order_by(JobAnalysisRow.created_at.desc())
        )
        if analysis_row is None:
            raise MatchingPreconditionError("a persisted Phase 3 JobAnalysis is required")
        analysis = AnalysisContract.model_validate(analysis_row.analysis_result)
        evidence_index = CandidateEvidenceIndex.from_profile(profile_path)
        profile_version = _profile_version(profile_path)
        profile_row = self._ensure_profile(profile_path, profile_version)

        requirement_matches = []
        for requirement_text in _analysis_requirements(analysis):
            requirement = Requirement(requirement_text)
            candidates = evidence_index.find(requirement_text)
            selected = _select_evidence(requirement_text, candidates)
            classification = _classification(requirement_text, selected)
            if _severity_cap(requirement_text) and classification != MatchClassification.DIRECT:
                # A requirement that demands an explicit experience/seniority
                # level cannot be satisfied by transferable/project/coursework
                # evidence. The junior-profile evidence does not establish it.
                classification = MatchClassification.MISSING
                selected = ()
            status = "matched" if classification == MatchClassification.DIRECT else (
                "partially_matched" if classification != MatchClassification.MISSING else "missing"
            )
            contribution = score_requirement(requirement, status)
            requirement_matches.append(RequirementMatch(
                requirement=requirement_text,
                importance=requirement.importance,
                classification=classification,
                candidate_evidence_ids=[item.evidence_id for item in selected],
                job_evidence=_job_evidence(job.description, requirement_text),
                rationale=_rationale(classification, selected),
                deterministic_weight=0.5 if requirement.importance == "unknown" else 1.0,
                score_contribution=contribution,
            ))

        total = sum(item.score_contribution for item in requirement_matches)
        maximum = sum(0.5 if item.importance == "unknown" else 1.0 for item in requirement_matches) or 1.0
        score = round(total / maximum * 100, 2)
        completeness = _evidence_completeness(requirement_matches)
        confidence = "HIGH" if completeness == "HIGH" and analysis.confidence == "HIGH" else (
            "MEDIUM" if completeness != "LOW" and analysis.confidence != "LOW" else "LOW"
        )
        decision = derive_application_decision(
            job=job,
            analysis=analysis,
            requirements=requirement_matches,
            score=score,
            confidence=confidence,
            evidence_completeness=completeness,
        )
        result = MatchResult(
            job_id=str(job.id),
            analysis_id=str(analysis_row.id),
            candidate_profile_version=profile_version,
            requirements=requirement_matches,
            overall_score=score,
            confidence=confidence,
            model_confidence=analysis.confidence,
            evidence_completeness=completeness,
            matching_version=MATCHING_VERSION,
            recommendation=decision.recommendation.value,
            decision_reasons=list(decision.reasons),
            critical_gaps=list(decision.critical_gaps),
            next_step=decision.next_step,
        )
        self.session.add(MatchResultRow(
            job_id=job.id,
            analysis_id=analysis_row.id,
            candidate_profile_id=profile_row.id,
            run_id=run_id,
            overall_score=round(score),
            confidence=result.confidence,
            recommendation=decision.recommendation.value,
            matched_requirements=[item.requirement for item in requirement_matches if item.classification == MatchClassification.DIRECT],
            partial_matches=[item.requirement for item in requirement_matches if item.classification == MatchClassification.TRANSFERABLE],
            gaps=[item.requirement for item in requirement_matches if item.classification == MatchClassification.MISSING],
            critical_gaps=list(decision.critical_gaps),
            evidence={
                "matching_version": MATCHING_VERSION,
                "job_content_hash": job.content_hash,
                "requirements": [item.model_dump(mode="json") for item in requirement_matches],
                "decision": decision.as_dict(),
            },
            reasoning=" ".join(decision.reasons) + " " + decision.next_step,
            model="deterministic-job-fit-core",
            prompt_version=MATCHING_VERSION,
            # Sub-second application timestamps make the append-only latest
            # match deterministic even on SQLite/PostgreSQL defaults that only
            # retain second precision.
            created_at=datetime.now(timezone.utc),
        ))
        self.session.flush()
        return result

    def _ensure_profile(self, path: Path, version: str) -> CandidateProfileRow:
        row = self.session.scalar(select(CandidateProfileRow).where(CandidateProfileRow.version == version))
        if row is None:
            row = CandidateProfileRow(version=version, profile_hash=version, is_active=True)
            self.session.add(row)
            self.session.flush()
        return row


def _profile_version(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _analysis_requirements(analysis: AnalysisContract) -> list[str]:
    values = analysis.matching_requirements + analysis.missing_requirements + analysis.transferable_skills
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _meaningful_terms(value: str) -> set[str]:
    """Extract meaningful skill terms from a requirement or evidence claim.

    Generic/weak words are dropped, as are bare numbers. A trailing plural is
    folded to its singular so "APIs" also matches "api" and "systems" matches
    "system".
    """
    tokens = {
        term
        for term in re.findall(r"[a-z0-9]+(?:[#+./][a-z0-9]+)*", value.casefold())
        if term not in _GENERIC_TERMS and not term.isdigit()
    }
    for term in list(tokens):
        if term.endswith("s") and len(term) > 4 and term[:-1] not in tokens:
            tokens.add(term[:-1])
    return tokens


def _select_evidence(requirement: str, candidates) -> tuple[Any, ...]:
    if not candidates:
        return ()
    requirement_terms = _meaningful_terms(requirement)
    selected = [
        item
        for item in candidates
        if requirement_terms & _meaningful_terms(item.claim)
    ]
    selected.sort(
        key=lambda item: (
            len(requirement_terms & _meaningful_terms(item.claim)),
            _STRENGTH.get(item.classification, 0),
        ),
        reverse=True,
    )
    return tuple(selected)


def _classification(requirement: str, candidates) -> MatchClassification:
    if not candidates:
        return MatchClassification.MISSING
    requirement_terms = _meaningful_terms(requirement)
    best_strong = max(
        (
            item
            for item in candidates
            if item.classification == "strong_hands_on"
        ),
        key=lambda item: len(requirement_terms & _meaningful_terms(item.claim)),
        default=None,
    )
    if best_strong is not None:
        common = requirement_terms & _meaningful_terms(best_strong.claim)
        # DIRECT requires strong hands-on evidence with a minimum number of
        # shared meaningful terms that also covers a majority of the
        # requirement's meaningful terms (parity, not partial-domain overlap).
        if (
            len(common) >= _MIN_DIRECT_TERMS
            and common and len(common) / len(requirement_terms) >= _MIN_DIRECT_COVERAGE
        ):
            return MatchClassification.DIRECT
    best = max(
        candidates,
        key=lambda item: (
            _STRENGTH.get(item.classification, 0),
            len(requirement_terms & _meaningful_terms(item.claim)),
        ),
    )
    return _NON_DIRECT[best.classification]


def _severity_cap(requirement: str) -> bool:
    """Detect explicit experience/seniority-level requirements.

    Returns True when the requirement demands a measurable seniority level
    (year counts, "X+ years", "at least N years") or a senior title. These
    requirements are capped below DIRECT unless direct-grade skill evidence
    exists, so a junior-profile match is never promoted to direct.
    """
    return any(pattern.search(requirement) for pattern in _SENIORITY_PATTERNS)


def _job_evidence(description: str, requirement: str) -> str:
    terms = [term for term in requirement.casefold().split() if len(term) > 2]
    sentence = next((part.strip() for part in description.split(".") if any(term in part.casefold() for term in terms)), description[:240])
    return sentence[:500]


def _rationale(classification: MatchClassification, evidence) -> str:
    if classification == MatchClassification.MISSING:
        return "No supporting candidate evidence was found in the canonical profile."
    return f"Classified as {classification.value} from {len(evidence)} canonical profile evidence item(s)."


def _evidence_completeness(items: list[RequirementMatch]) -> str:
    if not items or any(item.classification == MatchClassification.MISSING for item in items):
        return "LOW"
    if any(item.classification in {MatchClassification.COURSEWORK, MatchClassification.FAMILIARITY, MatchClassification.DEVELOPING} for item in items):
        return "MEDIUM"
    return "HIGH"
