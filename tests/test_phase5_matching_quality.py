"""Phase 5.1 regression tests: matching-quality fix.

These lock in the confirmed failure modes from the matching-quality
investigation (over-classification toward DIRECT). They are written against
the real canonical profile and the real deterministic matching service.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.analysis.domain import Confidence, FitCategory, JobAnalysis
from backend.app.db.models import Base, Job, JobAnalysis as JobAnalysisRow
from backend.app.db.repository import ensure_source
from backend.app.matching.domain import MatchClassification
from backend.app.matching.evidence import CandidateEvidenceIndex
from backend.app.matching.service import JobMatchingService

PROFILE = Path("data/candidate/profile.md")

META_SECTIONS = {
    "Retrieval and Claim Rules",
    "Source Note",
    "Evidence Classification",
}


def analysis_with(requirements: list[str]) -> JobAnalysis:
    return JobAnalysis(
        overall_fit=0,
        fit_category=FitCategory.INSUFFICIENT_EVIDENCE,
        matching_requirements=requirements,
        missing_requirements=[],
        transferable_skills=[],
        experience_alignment="Controlled fixture input; no analysis claim.",
        education_alignment="Controlled fixture input; no analysis claim.",
        location_alignment="Controlled fixture input; no analysis claim.",
        work_arrangement_alignment="Controlled fixture input; no analysis claim.",
        strengths=[],
        concerns=[],
        evidence=[],
        confidence=Confidence.LOW,
        analysis_version="phase5-matching-quality-test-v1",
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        source = ensure_source(
            session,
            source_id="solita_dk",
            display_name="Solita Denmark",
            official_jobs_url="https://www.solita.fi/join-us/?country=denmark",
            allowed_domains=["www.solita.fi"],
        )
        session.flush()
        job = Job(
            source_id=source.id,
            title="Developer",
            source_job_url="https://www.solita.fi/positions/quality-test/",
            description="Software development role.",
            availability_status="ACTIVE",
        )
        session.add(job)
        session.flush()
        yield session, job


# ---------------------------------------------------------------------------
# Evidence classification boundaries
# ---------------------------------------------------------------------------

def test_education_evidence_is_coursework_not_strong_hands_on():
    index = CandidateEvidenceIndex.from_profile(PROFILE)
    education = [item for item in index.evidence if item.section == "Education"]
    assert education
    assert all(item.classification != "strong_hands_on" for item in education)
    assert all(item.classification == "coursework" for item in education)


def test_language_evidence_is_familiarity_not_strong_hands_on():
    index = CandidateEvidenceIndex.from_profile(PROFILE)
    languages = [item for item in index.evidence if item.section == "Languages"]
    assert languages
    assert all(item.classification == "familiarity" for item in languages)


def test_meta_guidance_is_not_skill_evidence_and_not_retrievable():
    index = CandidateEvidenceIndex.from_profile(PROFILE)
    meta = [item for item in index.evidence if item.section in META_SECTIONS]
    assert meta, "meta sections are expected to contain bullets"
    assert all(item.classification == "familiarity" for item in meta)
    # meta-guidance must never be returned as a matching candidate
    for requirement in ("project delivery", "production experience", "claim rules"):
        assert all(item.section not in META_SECTIONS for item in index.find(requirement))


def test_coursework_evidence_cannot_be_direct():
    index = CandidateEvidenceIndex.from_profile(PROFILE)
    assert any(item.classification == "coursework" for item in index.evidence)


# ---------------------------------------------------------------------------
# Retrieval / selection gates
# ---------------------------------------------------------------------------

def test_python_requirement_is_not_direct_from_unrelated_csharp_evidence(session):
    classification, _ = classify(
        *session, "Strong Python development experience beyond small scripts."
    )
    assert classification != MatchClassification.DIRECT


def test_plsql_is_not_satisfied_by_sql_alone(session):
    classification, _ = classify(*session, "Experience with Python, SQL, and PL/SQL.")
    assert classification != MatchClassification.DIRECT


def test_databricks_azure_powerbi_stay_missing_without_evidence(session):
    classification, evidence_ids = classify(
        *session,
        "Several years of Databricks, Azure Data Factory, Power BI, and Azure experience.",
    )
    assert classification == MatchClassification.MISSING
    assert evidence_ids == []


def test_single_generic_token_does_not_create_direct(session):
    classification, _ = classify(*session, "Experience with data")
    assert classification == MatchClassification.MISSING


def test_terraforming_iac_requirement_does_not_match_meta_guidance(session):
    # Previously this matched a Retrieval-and-Claim-Rules guidance line.
    classification, _ = classify(
        *session,
        "Terraform and Infrastructure as Code experience, including ownership of modules.",
    )
    assert classification == MatchClassification.MISSING


# ---------------------------------------------------------------------------
# Decision gates
# ---------------------------------------------------------------------------

def test_multiyear_experience_is_not_direct_for_junior_profile(session):
    classification, _ = classify(
        *session,
        "At least 5–7 years of experience with data engineering and data solutions.",
    )
    assert classification != MatchClassification.DIRECT


def test_severity_cap_detects_seniority_requirements():
    from backend.app.matching.service import _severity_cap

    assert _severity_cap("At least 5–7 years of experience with data engineering")
    assert _severity_cap("5+ years of production experience")
    assert _severity_cap("Senior software engineer role")
    assert not _severity_cap("Python backend development")
    assert not _severity_cap("C# and .NET development with REST APIs")


def test_domain_overlap_is_transferable_not_direct(session):
    classification, _ = classify(*session, "Decision-support application development")
    assert classification == MatchClassification.TRANSFERABLE


def test_genuine_direct_skill_match_remains_direct(session):
    """Positive control: real DIRECT matches must survive the gates."""
    classification, _ = classify(*session, "C# and .NET development with REST APIs")
    assert classification == MatchClassification.DIRECT


def classify(session, job, requirement: str) -> tuple[MatchClassification, list[str]]:
    row = JobAnalysisRow(
        job_id=job.id,
        analysis_result=analysis_with([requirement]).model_dump(mode="json"),
        model="fake-model",
        prompt_version="phase3-analysis-v1",
    )
    session.add(row)
    session.flush()
    result = JobMatchingService(session).match_job(job.id, PROFILE)
    match = next(item for item in result.requirements if item.requirement == requirement)
    return match.classification, match.candidate_evidence_ids