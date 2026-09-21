from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session

from pydantic import ValidationError

from backend.app.analysis.domain import Confidence, FitCategory, JobAnalysis
from backend.app.cover_letters.domain import ClaimClassification, ClaimEvidenceMapping, CoverLetterDraft, GroundedClaimEvidenceMapping, LetterGroundingStatus
from backend.app.cover_letters.grounding import GroundingError, derive_claim_text, validate_grounding
from backend.app.cover_letters.llm import PROMPT_VERSION, SYSTEM_PROMPT, build_prompt
from backend.app.cover_letters.service import CoverLetterPreconditionError, CoverLetterService
from backend.app.db.models import AgentRun, Base, CandidateProfile, CoverLetter, Job, JobAnalysis as JobAnalysisRow, MatchResult
from backend.app.matching.evidence import CandidateEvidenceIndex
from backend.app.db.repository import ensure_source

PROFILE = Path("data/candidate/profile.md")


def make_analysis() -> JobAnalysis:
    return JobAnalysis(overall_fit=70, fit_category=FitCategory.GOOD, matching_requirements=["C#"], missing_requirements=[], transferable_skills=[], experience_alignment="Aligned.", education_alignment="Relevant.", location_alignment="Denmark.", work_arrangement_alignment="Unknown.", strengths=["C#"], concerns=[], evidence=[], confidence=Confidence.MEDIUM, analysis_version="phase3-analysis-v1")


def setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine)
    source = ensure_source(session, source_id="solita_dk", display_name="Solita", official_jobs_url="https://www.solita.fi", allowed_domains=["www.solita.fi"])
    session.flush()
    job = Job(source_id=source.id, title="Developer", source_job_url="https://www.solita.fi/role", description="Build APIs with C#.", availability_status="ACTIVE")
    session.add(job); session.flush()
    run = AgentRun(started_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc), status="SUCCESS")
    session.add(run); session.flush()
    profile_version = __import__("hashlib").sha256(PROFILE.read_bytes()).hexdigest()[:16]
    profile = CandidateProfile(version=profile_version, profile_hash=profile_version, is_active=True)
    session.add(profile); session.flush()
    index = CandidateEvidenceIndex.from_profile(PROFILE)
    evidence = next(item for item in index.evidence if item.claim == "C# and .NET ecosystem: ASP.NET Core, Blazor, Entity Framework Core, REST APIs, ASP.NET Core Identity, and web application development.")
    match = MatchResult(job_id=job.id, analysis_id=None, candidate_profile_id=profile.id, run_id=run.id, overall_score=70, confidence="MEDIUM", recommendation="NOT_EVALUATED", evidence={"requirements": [{"candidate_evidence_ids": [evidence.evidence_id], "job_evidence": "Build APIs with C#.", "requirement": "C#"}]}, model="deterministic-job-fit-core", prompt_version="phase5-matching-v1")
    session.add(match); session.flush()
    analysis = JobAnalysisRow(job_id=job.id, run_id=run.id, analysis_result=make_analysis().model_dump(mode="json"), model="fake", prompt_version="phase3-analysis-v1")
    session.add(analysis); session.flush()
    match.analysis_id = analysis.id
    session.commit()
    return session, job, match, profile, evidence


class FakeGenerator:
    model = "fake-letter-model"

    def __init__(self, draft: CoverLetterDraft):
        self.draft = draft
        self.context = None

    def generate(self, context):
        self.context = context
        return self.draft


def draft_for(evidence_id: str, classification: ClaimClassification = ClaimClassification.DIRECT) -> CoverLetterDraft:
    """Model-output draft: mapping-only (no claim wording); the service derives it."""
    return CoverLetterDraft(cover_letter="I bring C# experience demonstrated in my projects.", claim_evidence_mappings=[{"evidence_ids": [evidence_id], "classification": classification, "rationale": "The profile identifies C# project evidence."}], grounding_status=LetterGroundingStatus.GROUNDED, analysis_version="phase3-analysis-v1", matching_version="phase5-matching-v1")


# Regression fixture for the browser E2E 422:
# "claim classification is inconsistent with evidence: Fluent in Danish and
# English at a business level". The candidate profile only supports
# "good working proficiency" for Danish and English (Languages evidence is
# classified as familiarity), so a draft claiming business-level fluency is an
# overclaim that must keep failing the grounding guardrail.
LANGUAGE_CLAIM = "Fluent in Danish and English at a business level"


def language_evidence_ids() -> list[str]:
    index = CandidateEvidenceIndex.from_profile(PROFILE)
    language_ids = [
        item.evidence_id
        for item in index.evidence
        if item.section == "Languages" and "good working proficiency" in item.claim
    ]
    assert len(language_ids) >= 2, "profile must expose Danish and English working-proficiency evidence"
    return language_ids


def language_proficiency_draft(classification: ClaimClassification, *, cover_letter: str | None = None) -> CoverLetterDraft:
    return CoverLetterDraft(
        cover_letter=cover_letter or LANGUAGE_CLAIM,
        claim_evidence_mappings=[{
            "evidence_ids": language_evidence_ids(),
            "classification": classification,
            "rationale": "The candidate lists Danish and English in the Languages section.",
        }],
        grounding_status=LetterGroundingStatus.GROUNDED,
        analysis_version="phase3-analysis-v1",
        matching_version="phase5-matching-v1",
    )


def language_claim_wording() -> str:
    """Deterministic derived wording for the language evidence (v3 contract)."""
    return derive_claim_text(CandidateEvidenceIndex.from_profile(PROFILE), language_evidence_ids())


def grounded_draft_for(
    evidence_ids: list[str],
    classification: ClaimClassification,
    *,
    claim: str | None = None,
) -> CoverLetterDraft:
    """Derived-shape draft for direct grounding tests; wording defaults to the
    deterministic derivation so wording-equality passes unless overridden."""
    index = CandidateEvidenceIndex.from_profile(PROFILE)
    return CoverLetterDraft(
        cover_letter="I bring relevant experience.",
        claim_evidence_mappings=[GroundedClaimEvidenceMapping(
            evidence_ids=evidence_ids,
            classification=classification,
            rationale="Grounded test mapping.",
            claim=claim if claim is not None else derive_claim_text(index, evidence_ids),
        )],
        grounding_status=LetterGroundingStatus.GROUNDED,
        analysis_version="phase3-analysis-v1",
        matching_version="phase5-matching-v1",
    )


def test_grounding_rejects_unsupported_language_proficiency_overclaim():
    """Regression for the live Docker E2E overclaim, post claim-derivation (v3).

    The model can no longer author claim wording at all: the mapping contract
    rejects a model-supplied `claim` (extra=forbid), and a fabricated grounded
    mapping carrying the historical overclaim wording is rejected by the
    deterministic wording-equality invariant. A DIRECT self-label against
    familiarity-only evidence is still rejected with the exact inconsistency
    detail — now the derived evidence wording — and nothing is persisted; the
    conservative FAMILIARITY path still produces a GROUNDED draft whose
    persisted claim wording is the deterministic derivation.
    """
    session, job, match, profile, _ = setup()
    match.evidence = {"requirements": [{
        "requirement": LANGUAGE_CLAIM,
        "job_evidence": "Business-level proficiency in Danish and English is expected.",
        "candidate_evidence_ids": language_evidence_ids(),
    }]}
    session.flush()

    # The LLM mapping contract no longer accepts model-authored claim text.
    with pytest.raises(ValidationError):
        ClaimEvidenceMapping(claim=LANGUAGE_CLAIM, evidence_ids=language_evidence_ids(), classification=ClaimClassification.FAMILIARITY, rationale="Overclaim attempt.")

    # A fabricated grounded mapping with the historical overclaim wording is rejected.
    with pytest.raises(GroundingError) as wording_exc:
        validate_grounding(grounded_draft_for(language_evidence_ids(), ClaimClassification.FAMILIARITY, claim=LANGUAGE_CLAIM), CandidateEvidenceIndex.from_profile(PROFILE))
    assert str(wording_exc.value) == f"claim wording does not match cited evidence: {LANGUAGE_CLAIM}"
    assert session.query(CoverLetter).count() == 0

    # DIRECT self-label against familiarity-only evidence keeps failing (service path).
    service = CoverLetterService(session, FakeGenerator(language_proficiency_draft(ClaimClassification.DIRECT)))
    with pytest.raises(GroundingError) as exc:
        service.generate_for_human(job_id=job.id, match_id=match.id, profile_path=PROFILE, human_authorized=True)
    assert str(exc.value) == f"claim classification is inconsistent with evidence: {language_claim_wording()}"
    assert session.query(CoverLetter).count() == 0

    # The supported FAMILIARITY path still produces a GROUNDED draft whose persisted
    # claim wording is the deterministic derivation, not model-authored text.
    supported = CoverLetterService(session, FakeGenerator(language_proficiency_draft(
        ClaimClassification.FAMILIARITY,
        cover_letter="I have good working proficiency in Danish and English.",
    )))
    accepted = supported.generate_for_human(job_id=job.id, match_id=match.id, profile_path=PROFILE, human_authorized=True)
    assert accepted.grounding_status == LetterGroundingStatus.GROUNDED
    row = session.query(CoverLetter).one()
    notes = json.loads(row.grounding_notes)
    assert notes["claim_evidence_mappings"][0]["claim"] == language_claim_wording()
    assert LANGUAGE_CLAIM not in row.grounding_notes
    session.close()


def test_service_derives_claim_wording_from_cited_evidence():
    """Mapping-only LLM output: persisted claim text is the deterministic
    derivation of the cited evidence claims, exactly matching their wording."""
    session, job, match, profile, evidence = setup()
    language_ids = language_evidence_ids()
    match.evidence = {"requirements": [{
        "requirement": "Danish and English communication",
        "job_evidence": "Danish and English communication is expected.",
        "candidate_evidence_ids": language_ids,
    }]}
    session.flush()
    service = CoverLetterService(session, FakeGenerator(language_proficiency_draft(
        ClaimClassification.FAMILIARITY,
        cover_letter="I have good working proficiency in Danish and English.",
    )))
    service.generate_for_human(job_id=job.id, match_id=match.id, profile_path=PROFILE, human_authorized=True)
    row = session.query(CoverLetter).one()
    notes = json.loads(row.grounding_notes)
    mapping = notes["claim_evidence_mappings"][0]
    index = CandidateEvidenceIndex.from_profile(PROFILE)
    claims_by_id = {item.evidence_id: item.claim for item in index.evidence}
    assert mapping["evidence_ids"] == language_ids
    assert mapping["claim"] == "; ".join(claims_by_id[evidence_id] for evidence_id in sorted(set(language_ids)))
    assert mapping["claim"] == language_claim_wording()
    assert "Fluent" not in mapping["claim"]
    session.close()


def test_generator_prompt_forbids_restating_requirements_as_candidate_claims():
    """Prompt-level regression for the browser E2E language overclaim.

    The grounding guardrail rejects unsupported overclaims with 422, but the
    generator must not produce them in the first place. The system prompt has
    to separate job requirements from candidate facts, require exact
    claim/classification support from the cited evidence, forbid inferring
    proficiency from the requirement's wording, pin familiarity-level language
    evidence to conservative wording (working proficiency, never fluent or
    business-level), and omit unsupported requirements instead of upgrading
    them.
    """
    lowered = " ".join(SYSTEM_PROMPT.lower().split())
    # Requirements are employer asks, not candidate facts.
    assert "they are not" in lowered and "facts about the candidate" in lowered
    # Mapping-only authorship: the model never words candidate claims.
    assert "you do not author candidate claim wording" in lowered
    assert "derives the claim wording verbatim from the cited evidence" in lowered
    # No conversion of a requirement into a candidate claim.
    assert "never restate a requirement's wording as if it were evidence" in lowered
    assert "never cite candidate evidence for a requirement" in lowered
    # No inference of proficiency from the requirement wording.
    assert "never infer candidate proficiency from the requirement" in lowered
    # Evidence wording is never paraphrased, upgraded, or weakened.
    assert "never paraphrase, upgrade, or weaken evidence wording" in lowered
    # Omit instead of upgrade/guess/invent.
    assert "omit the requirement from the candidate claims" in lowered
    # Pre-existing anti-hallucination and grounding-preservation rules remain.
    assert "must map to supplied evidence ids and preserve its classification" in lowered
    assert "never invent employers, job titles, years, qualifications, certifications, or" in lowered
    assert PROMPT_VERSION == "phase9-cover-letter-v3"

    context = {
        "job_title": "Developer",
        "requirements": [{"requirement": LANGUAGE_CLAIM, "importance": "required"}],
        "job_evidence": ["Business-level proficiency in Danish and English is expected."],
        "candidate_evidence": [{
            "evidence_id": "profile:language",
            "claim": "Danish: good working proficiency.",
            "classification": "FAMILIARITY",
            "section": "Languages",
            "confidence": "MEDIUM",
        }],
    }
    messages = build_prompt(context)
    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[0]["content"] == SYSTEM_PROMPT
    user_content = messages[1]["content"]
    assert "<job_data>" in user_content and "</job_data>" in user_content
    assert "<approved_candidate_evidence>" in user_content and "</approved_candidate_evidence>" in user_content
    # The requirement stays visible as bounded job data, not as a candidate claim.
    assert LANGUAGE_CLAIM in user_content


def test_grounding_rejects_unknown_ids_and_inconsistent_classification():
    index = CandidateEvidenceIndex.from_profile(PROFILE)
    evidence = index.evidence[0]
    with pytest.raises(GroundingError):
        validate_grounding(grounded_draft_for(["profile:missing"], ClaimClassification.DIRECT, claim="unavailable wording"), index)
    with pytest.raises(GroundingError):
        validate_grounding(grounded_draft_for([evidence.evidence_id], ClaimClassification.COURSEWORK), index)
    # Raw model output (undetermined wording) must never reach grounding acceptance.
    with pytest.raises(GroundingError):
        validate_grounding(draft_for(evidence.evidence_id), index)


def test_grounding_uses_the_weakest_cited_evidence_as_the_ceiling():
    """A claim citing a strong, unrelated item alongside a weak, relevant item
    must be graded at the weakest cited item's level, never promoted using
    whichever cited item happens to be strongest (docs: weakest-item ceiling)."""
    index = CandidateEvidenceIndex.from_profile(PROFILE)
    strong_unrelated = next(item for item in index.evidence if item.classification == "strong_hands_on")
    weak_relevant = next(item for item in index.evidence if item.classification == "familiarity")
    mixed_ids = [strong_unrelated.evidence_id, weak_relevant.evidence_id]

    with pytest.raises(GroundingError) as exc:
        validate_grounding(grounded_draft_for(mixed_ids, ClaimClassification.DIRECT), index)
    assert "claim classification is inconsistent with evidence" in str(exc.value)

    # The claim is accepted only when graded at the weakest cited item's level.
    accepted = validate_grounding(grounded_draft_for(mixed_ids, ClaimClassification.FAMILIARITY), index)
    assert accepted.claim_evidence_mappings[0].classification == ClaimClassification.FAMILIARITY


def test_service_requires_match_relationship_and_resolved_evidence():
    session, job, match, profile, evidence = setup()
    generator = FakeGenerator(draft_for(evidence.evidence_id))
    service = CoverLetterService(session, generator)
    result = service.generate_for_human(job_id=job.id, match_id=match.id, profile_path=PROFILE, human_authorized=True)
    assert result.grounding_status == LetterGroundingStatus.GROUNDED
    assert generator.context["candidate_evidence"][0]["evidence_id"] == evidence.evidence_id
    assert "data/candidate/profile.md" not in json.dumps(generator.context)
    assert session.query(CoverLetter).count() == 1
    row = session.query(CoverLetter).one()
    assert row.grounding_valid is True
    assert row.human_reviewed is False
    notes = json.loads(row.grounding_notes)
    assert notes["candidate_profile_version"] == profile.version
    session.close()


def test_service_never_persists_a_rejected_or_unsupported_draft():
    session, job, match, profile, evidence = setup()
    rejected = draft_for(evidence.evidence_id).model_copy(update={"grounding_status": LetterGroundingStatus.REJECTED})
    with pytest.raises(GroundingError):
        CoverLetterService(session, FakeGenerator(rejected)).generate_for_human(job_id=job.id, match_id=match.id, profile_path=PROFILE, human_authorized=True)
    assert session.query(CoverLetter).count() == 0
    with pytest.raises(GroundingError):
        CoverLetterService(session, FakeGenerator(draft_for("profile:missing"))).generate_for_human(job_id=job.id, match_id=match.id, profile_path=PROFILE, human_authorized=True)
    assert session.query(CoverLetter).count() == 0
    session.close()


def test_review_updates_only_human_metadata():
    session, job, match, profile, evidence = setup()
    service = CoverLetterService(session, FakeGenerator(draft_for(evidence.evidence_id)))
    service.generate_for_human(job_id=job.id, match_id=match.id, profile_path=PROFILE, human_authorized=True)
    row = session.query(CoverLetter).one()
    original_content = row.content
    with pytest.raises(PermissionError):
        service.review(row.id, human_authorized=False, human_reviewed=True)
    reviewed = service.review(row.id, human_authorized=True, human_reviewed=True, human_notes="Checked against profile evidence")
    assert reviewed.human_reviewed is True
    assert reviewed.human_notes == "Checked against profile evidence"
    assert reviewed.content == original_content
    assert reviewed.grounding_valid is True
    assert session.query(CoverLetter).count() == 1
    with pytest.raises(LookupError):
        service.review(uuid4(), human_authorized=True, human_reviewed=True)
    session.close()


def test_service_rejects_wrong_job_and_missing_evidence():
    session, job, match, profile, evidence = setup()
    other = Job(source_id=job.source_id, title="Other", source_job_url="https://www.solita.fi/other", availability_status="ACTIVE")
    session.add(other); session.flush()
    with pytest.raises(CoverLetterPreconditionError):
        CoverLetterService(session, FakeGenerator(draft_for(evidence.evidence_id))).generate_for_human(job_id=other.id, match_id=match.id, profile_path=PROFILE, human_authorized=True)
    match.evidence = {"requirements": [{"candidate_evidence_ids": ["profile:missing"]}]}
    session.flush()
    with pytest.raises(CoverLetterPreconditionError):
        CoverLetterService(session, FakeGenerator(draft_for("profile:missing"))).generate_for_human(job_id=job.id, match_id=match.id, profile_path=PROFILE, human_authorized=True)
    session.close()


def test_service_rejects_analysis_from_another_job():
    session, job, match, profile, evidence = setup()
    other = Job(source_id=job.source_id, title="Other", source_job_url="https://www.solita.fi/other", availability_status="ACTIVE")
    session.add(other); session.flush()
    analysis = JobAnalysisRow(job_id=other.id, analysis_result=make_analysis().model_dump(mode="json"), model="fake", prompt_version="phase3-analysis-v1")
    session.add(analysis); session.flush()
    match.analysis_id = analysis.id
    session.flush()
    with pytest.raises(CoverLetterPreconditionError):
        CoverLetterService(session, FakeGenerator(draft_for(evidence.evidence_id))).generate_for_human(job_id=job.id, match_id=match.id, profile_path=PROFILE, human_authorized=True)
    session.close()


def test_generation_is_human_authorized_and_history_is_append_only(session_fixture=None):
    session, job, match, profile, evidence = setup()
    service = CoverLetterService(session, FakeGenerator(draft_for(evidence.evidence_id)))
    with pytest.raises(PermissionError):
        service.generate_for_human(job_id=job.id, match_id=match.id, profile_path=PROFILE, human_authorized=False)
    service.generate_for_human(job_id=job.id, match_id=match.id, profile_path=PROFILE, human_authorized=True)
    service.generate_for_human(job_id=job.id, match_id=match.id, profile_path=PROFILE, human_authorized=True)
    assert session.query(CoverLetter).count() == 2
    session.close()
