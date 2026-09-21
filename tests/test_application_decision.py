from __future__ import annotations

from backend.app.analysis.domain import Confidence, FitCategory, JobAnalysis
from backend.app.db.models import Job
from backend.app.matching.decision import ApplicationDecision, derive_application_decision
from backend.app.matching.domain import MatchClassification, RequirementMatch


def _analysis(*, confidence: Confidence = Confidence.HIGH) -> JobAnalysis:
    return JobAnalysis(
        overall_fit=80,
        fit_category=FitCategory.GOOD,
        matching_requirements=[],
        missing_requirements=[],
        transferable_skills=[],
        experience_alignment="Relevant experience.",
        education_alignment="Relevant education.",
        location_alignment="Denmark.",
        work_arrangement_alignment="Full-time.",
        strengths=[],
        concerns=[],
        evidence=[],
        confidence=confidence,
        analysis_version="test-v1",
    )


def _requirement(
    text: str,
    classification: MatchClassification,
    *,
    importance: str = "unknown",
) -> RequirementMatch:
    return RequirementMatch(
        requirement=text,
        importance=importance,
        classification=classification,
        candidate_evidence_ids=[] if classification == MatchClassification.MISSING else ["profile:test"],
        job_evidence=text,
        rationale="Test classification.",
        deterministic_weight=1 if importance == "required" else 0.5,
        score_contribution=1 if classification == MatchClassification.DIRECT else 0,
    )


def _job(country: str | None = "DK") -> Job:
    return Job(
        title="Role",
        source_job_url="https://example.test/job",
        description="Role description",
        country_code=country,
        availability_status="ACTIVE",
    )


def test_trifork_like_score_and_missing_seniority_is_low_priority():
    decision = derive_application_decision(
        job=_job(),
        analysis=_analysis(confidence=Confidence.MEDIUM),
        requirements=[
            _requirement("C# and .NET", MatchClassification.DIRECT),
            _requirement("Experience level as a senior developer", MatchClassification.MISSING),
        ],
        score=53,
        confidence="LOW",
        evidence_completeness="LOW",
    )
    assert decision.recommendation == ApplicationDecision.LOW_PRIORITY
    assert decision.critical_gaps == ("Experience level as a senior developer",)


def test_missing_required_requirement_caps_high_score_at_low_priority():
    decision = derive_application_decision(
        job=_job(),
        analysis=_analysis(),
        requirements=[
            _requirement("C# and .NET", MatchClassification.DIRECT),
            _requirement("Cloud delivery experience is required", MatchClassification.MISSING, importance="required"),
        ],
        score=90,
        confidence="HIGH",
        evidence_completeness="HIGH",
    )
    assert decision.recommendation == ApplicationDecision.LOW_PRIORITY


def test_ineligible_location_is_always_skip():
    decision = derive_application_decision(
        job=_job("SE"), analysis=_analysis(), requirements=[], score=95,
        confidence="HIGH", evidence_completeness="HIGH",
    )
    assert decision.recommendation == ApplicationDecision.SKIP
    assert decision.critical_gaps == ("Ineligible location: SE",)


def test_low_evidence_never_produces_apply():
    decision = derive_application_decision(
        job=_job(),
        analysis=_analysis(confidence=Confidence.LOW),
        requirements=[_requirement("C# and .NET", MatchClassification.DIRECT)],
        score=92,
        confidence="LOW",
        evidence_completeness="LOW",
    )
    assert decision.recommendation == ApplicationDecision.CONSIDER
    assert len(decision.reasons) <= 3


def test_unsolicited_application_is_skipped():
    job = _job()
    job.title = "Uopfordrede ansøgninger Danmark"

    decision = derive_application_decision(
        job=job,
        analysis=_analysis(),
        requirements=[],
        score=80,
        confidence="HIGH",
        evidence_completeness="HIGH",
    )

    assert decision.recommendation == ApplicationDecision.SKIP
    assert decision.critical_gaps == ("No specific role or bounded requirements to evaluate",)
