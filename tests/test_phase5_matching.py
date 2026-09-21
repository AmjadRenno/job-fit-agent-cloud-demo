from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.analysis.domain import Confidence, FitCategory, JobAnalysis
from backend.app.db.models import Base, Job, JobAnalysis as JobAnalysisRow, MatchResult as MatchResultRow
from backend.app.db.repository import ensure_source
from backend.app.matching.domain import MatchClassification, MatchResult
from backend.app.matching.evidence import CandidateEvidenceIndex
from backend.app.matching.service import JobMatchingService, MatchingPreconditionError

PROFILE = Path("data/candidate/profile.md")


def analysis_result() -> JobAnalysis:
    return JobAnalysis(
        overall_fit=65,
        fit_category=FitCategory.POSSIBLE,
        matching_requirements=["C# and .NET"],
        missing_requirements=["Rust production experience"],
        transferable_skills=["API development"],
        experience_alignment="Project evidence supports some requirements.",
        education_alignment="Datamatiker education is relevant.",
        location_alignment="Denmark aligns.",
        work_arrangement_alignment="Not explicit.",
        strengths=["C# project evidence"],
        concerns=["Rust is not evidenced."],
        evidence=[],
        confidence=Confidence.MEDIUM,
        analysis_version="phase3-analysis-v1",
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        source = ensure_source(session, source_id="solita_dk", display_name="Solita Denmark", official_jobs_url="https://www.solita.fi/join-us/?country=denmark", allowed_domains=["www.solita.fi"])
        session.flush()
        job = Job(source_id=source.id, title="Developer", source_job_url="https://www.solita.fi/positions/match-test/", description="Build APIs with C# and .NET. Rust production experience is required.", availability_status="ACTIVE")
        session.add(job)
        session.flush()
        row = JobAnalysisRow(job_id=job.id, analysis_result=analysis_result().model_dump(mode="json"), model="fake-model", prompt_version="phase3-analysis-v1")
        session.add(row)
        session.flush()
        yield session, job


def test_evidence_ids_are_stable_and_classifications_are_explicit():
    first = CandidateEvidenceIndex.from_profile(PROFILE)
    second = CandidateEvidenceIndex.from_profile(PROFILE)
    assert first.evidence
    assert [item.evidence_id for item in first.evidence] == [item.evidence_id for item in second.evidence]
    assert {item.classification for item in first.evidence} >= {"strong_hands_on", "significant_project", "developing", "coursework"}
    assert all(item.evidence_id.startswith("profile:") for item in first.evidence)


def test_coursework_evidence_is_not_direct_professional_evidence():
    index = CandidateEvidenceIndex.from_profile(PROFILE)
    coursework = [item for item in index.evidence if item.classification == "coursework"]
    assert coursework
    assert all(item.classification != "strong_hands_on" for item in coursework)


def test_matching_distinguishes_direct_transferable_and_missing(session):
    current_session, job = session
    result = JobMatchingService(current_session).match_job(job.id, PROFILE)
    by_requirement = {item.requirement: item for item in result.requirements}
    assert by_requirement["C# and .NET"].classification == MatchClassification.DIRECT
    assert by_requirement["API development"].classification in {MatchClassification.DIRECT, MatchClassification.TRANSFERABLE}
    assert by_requirement["Rust production experience"].classification == MatchClassification.MISSING
    assert by_requirement["Rust production experience"].candidate_evidence_ids == []
    persisted = current_session.query(MatchResultRow).order_by(MatchResultRow.created_at.desc()).first()
    assert persisted.recommendation in {"APPLY", "CONSIDER", "LOW_PRIORITY", "SKIP"}
    assert persisted.reasoning
    assert persisted.evidence["decision"]["recommendation"] == persisted.recommendation


def test_score_is_reproducible_and_history_is_append_only(session):
    current_session, job = session
    service = JobMatchingService(current_session)
    first = service.match_job(job.id, PROFILE)
    second = service.match_job(job.id, PROFILE)
    current_session.commit()
    assert first.overall_score == second.overall_score
    assert current_session.query(MatchResultRow).count() == 2
    assert current_session.query(MatchResultRow).all()[0].analysis_id == current_session.query(JobAnalysisRow).one().id


def test_missing_analysis_stops_matching(session):
    current_session, job = session
    current_session.query(JobAnalysisRow).delete()
    current_session.flush()
    with pytest.raises(MatchingPreconditionError):
        JobMatchingService(current_session).match_job(job.id, PROFILE)


def test_match_contract_rejects_malformed_output():
    with pytest.raises(ValueError):
        MatchResult.model_validate({"job_id": "x", "overall_score": 101})
