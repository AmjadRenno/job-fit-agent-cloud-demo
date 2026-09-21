"""Phase 13 Vestas vertical-slice tests — Vestas end-to-end through the
existing deterministic pipeline.

Proves the deliberate per-source gate lift (Phase 12 recorded intent):
- an ACTIVE, DK-located Vestas job can enter analysis and matching, and the
  real deterministic matching contract classifies its requirements;
- every data precondition (explicit availability, eligible country, persisted
  analysis, applied-exclusion, analysis-status guard) applies to Vestas exactly
  as it does to Solita;
- unapproved sources remain blocked at both the analysis and matching gates;
- the daily-run default source set now includes the Vestas pilot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.analysis.context import (
    ANALYSIS_ELIGIBLE_SOURCES,
    AnalysisPreconditionError,
    CandidateProfile,
    build_context,
)
from backend.app.analysis.domain import Confidence, FitCategory, JobAnalysis
from backend.app.analysis.service import JobAnalysisService
from backend.app.applications.service import ApplicationService
from backend.app.db.models import (
    Base,
    Job,
    JobAnalysis as JobAnalysisRow,
    JobSource,
    MatchResult as MatchResultRow,
)
from backend.app.db.repository import ensure_source
from backend.app.matching.domain import MatchClassification
from backend.app.matching.service import (
    MATCHING_ELIGIBLE_SOURCES,
    JobMatchingService,
    MatchingPreconditionError,
)
from backend.app.workflow.processing import JobProcessingService

PROFILE = Path("data/candidate/profile.md")


def _analysis_result() -> JobAnalysis:
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


class CountingAnalyzer:
    model = "fake-vestas-analyzer"

    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, context):
        self.calls += 1
        return _analysis_result()


def make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    for source_id in ("solita_dk", "vestas_dk", "netcompany_dk"):
        ensure_source(
            session,
            source_id=source_id,
            display_name=source_id.replace("_", " ").title(),
            official_jobs_url=f"https://www.{source_id.replace('_', '.')}.invalid/",
            allowed_domains=[f"www.{source_id.replace('_', '.')}.invalid"],
        )
    session.commit()
    return session


def _job_for_source(
    session: Session,
    source_key: str,
    url: str,
    *,
    availability_status: str = "ACTIVE",
    country_code: str = "DK",
    description: str = "Build APIs with C# and .NET. Rust production experience is required.",
) -> Job:
    source = session.scalar(select(JobSource).where(JobSource.source_id == source_key))
    job = Job(
        source_id=source.id,
        title="Role",
        source_job_url=url,
        description=description,
        country_code=country_code,
        availability_status=availability_status,
    )
    session.add(job)
    session.flush()
    return job


def _vestas_record(url: str = "https://careers.vestas.com/job/Aarhus-Role/9000000001/") -> dict:
    return {
        "source": "vestas_dk",
        "title": "Role",
        "url": url,
        "description": "Build APIs with C# and .NET.",
        "country": ["Denmark"],
        "location": "Denmark",
        "employment_type": "FULL_TIME",
        "status": "active",
    }


def test_analysis_gate_accepts_vestas_and_sets_are_explicit():
    assert ANALYSIS_ELIGIBLE_SOURCES == frozenset({"solita_dk", "vestas_dk", "trifork_dk", "demo_synthetic"})
    assert MATCHING_ELIGIBLE_SOURCES == frozenset({"solita_dk", "vestas_dk", "trifork_dk", "demo_synthetic"})
    session = make_session()
    job = _job_for_source(
        session,
        "vestas_dk",
        "https://careers.vestas.com/job/Copenhagen-Software-Engineer/9000000001/",
    )
    context = build_context(job, CandidateProfile.load(PROFILE))
    assert context.source == "vestas_dk"
    assert context.country_code == "DK"
    session.close()


def test_vestas_job_reaches_matching_through_existing_deterministic_contract():
    session = make_session()
    job = _job_for_source(session, "vestas_dk", "https://careers.vestas.com/job/Role/9000000002/")
    analyzer = CountingAnalyzer()
    analysis = JobAnalysisService(session, analyzer).analyze_job(job.id, PROFILE)
    match = JobMatchingService(session).match_job(job.id, PROFILE)
    assert analyzer.calls == 1
    assert analysis.matching_requirements == ["C# and .NET"]
    assert job.source.source_id == "vestas_dk"
    by_requirement = {item.requirement: item for item in match.requirements}
    assert by_requirement["C# and .NET"].classification == MatchClassification.DIRECT
    assert by_requirement["Rust production experience"].classification == MatchClassification.MISSING
    assert by_requirement["Rust production experience"].candidate_evidence_ids == []
    assert 0 <= match.overall_score <= 100
    assert session.query(MatchResultRow).count() == 1
    session.close()


def test_vestas_without_explicit_availability_is_blocked_before_analyzer():
    session = make_session()
    job = _job_for_source(
        session,
        "vestas_dk",
        "https://careers.vestas.com/job/Role/9000000003/",
        availability_status="UNKNOWN",
    )
    analyzer = CountingAnalyzer()
    with pytest.raises(AnalysisPreconditionError, match="not explicitly available"):
        JobAnalysisService(session, analyzer).analyze_job(job.id, PROFILE)
    assert analyzer.calls == 0
    session.close()


def test_vestas_with_ineligible_country_remains_blocked():
    session = make_session()
    job = _job_for_source(
        session,
        "vestas_dk",
        "https://careers.vestas.com/job/Role/9000000004/",
        country_code="SE",
    )
    with pytest.raises(AnalysisPreconditionError, match="eligible locations"):
        build_context(job, CandidateProfile.load(PROFILE))
    session.close()


def test_vestas_matching_requires_persisted_analysis():
    session = make_session()
    job = _job_for_source(session, "vestas_dk", "https://careers.vestas.com/job/Role/9000000005/")
    with pytest.raises(MatchingPreconditionError, match="persisted Phase 3 JobAnalysis"):
        JobMatchingService(session).match_job(job.id, PROFILE)
    session.close()


def test_already_analyzed_vestas_job_is_skipped():
    session = make_session()
    processing = JobProcessingService(session, analyzer=CountingAnalyzer(), profile_path=PROFILE)
    url = "https://careers.vestas.com/job/Aarhus-Role/9000000006/"
    first = processing.process_record(_vestas_record(url))
    second = processing.process_record(_vestas_record(url))
    assert first.analysis_performed is True
    assert second.analysis_performed is False
    assert session.query(JobAnalysisRow).count() == 1
    assert session.query(MatchResultRow).count() == 1
    session.close()


def test_applied_vestas_job_is_not_rematched():
    session = make_session()
    processing = JobProcessingService(session, analyzer=CountingAnalyzer(), profile_path=PROFILE)
    url = "https://careers.vestas.com/job/Copenhagen-Role/9000000007/"
    result = processing.process_record(_vestas_record(url))
    application = ApplicationService(session).create_for_human(result.job.id, human_authorized=True)
    ApplicationService(session).transition(application.id, "TO_APPLY", human_authorized=True)
    ApplicationService(session).transition(application.id, "APPLIED", human_authorized=True)
    rerun = processing.process_record(_vestas_record(url))
    assert rerun.match_performed is False
    assert session.query(MatchResultRow).count() == 1  # only the pre-application match
    session.close()


def test_unapproved_source_is_blocked_at_analysis():
    session = make_session()
    job = _job_for_source(session, "netcompany_dk", "https://www.netcompany.com/jobs/role-1/")
    with pytest.raises(AnalysisPreconditionError, match="not approved for analysis"):
        build_context(job, CandidateProfile.load(PROFILE))
    session.close()


def test_unapproved_source_is_blocked_at_matching():
    session = make_session()
    job = _job_for_source(session, "netcompany_dk", "https://www.netcompany.com/jobs/role-2/")
    session.add(
        JobAnalysisRow(
            job_id=job.id,
            analysis_result=_analysis_result().model_dump(mode="json"),
            model="fake-model",
            prompt_version="phase3-analysis-v1",
        )
    )
    session.flush()
    with pytest.raises(MatchingPreconditionError, match="not approved for matching"):
        JobMatchingService(session).match_job(job.id, PROFILE)
    session.close()


def test_solita_behavior_is_unchanged():
    session = make_session()
    job = _job_for_source(session, "solita_dk", "https://www.solita.fi/positions/unchanged-1/")
    context = build_context(job, CandidateProfile.load(PROFILE))
    assert context.source == "solita_dk"
    assert context.country_code == "DK"
    job.availability_status = "UNKNOWN"
    with pytest.raises(AnalysisPreconditionError, match="not explicitly available"):
        build_context(job, CandidateProfile.load(PROFILE))
    session.close()


def test_daily_run_default_source_runners_include_vestas(tmp_path):
    from scripts.source_registry import REGISTERED_SOURCE_RUNNERS

    assert set(REGISTERED_SOURCE_RUNNERS) == {"solita_dk", "vestas_dk"}


def test_run_vestas_payload_shape_and_contract_url(tmp_path, monkeypatch):
    from backend.app.sources.phase1_trial import run_vestas
    from backend.app.sources.vestas_contract import VESTAS_DRAFT_CONTRACT

    captured: dict[str, str] = {}

    def fake_discover_jobs(self, careers_url, *, transport=None):
        captured["url"] = careers_url
        return [
            {
                "source": "vestas_dk",
                "title": "Service Technician",
                "url": "https://careers.vestas.com/job/Aarhus-Service-Technician/1234567890/",
                "description": "Maintain wind turbines across Danish sites.",
                "status": "unknown",
                "external_job_id": "1234567890",
                "country": ["Denmark"],
            }
        ]

    monkeypatch.setattr(
        "backend.app.workflow.sources.VestasSourceHandler.discover_jobs",
        fake_discover_jobs,
    )
    payload = run_vestas(tmp_path)
    assert captured["url"] == VESTAS_DRAFT_CONTRACT.careers_url
    assert payload["source"] == "vestas_dk"
    assert payload["jobs"][0]["status"] == "unknown"  # ACTIVE is never inferred
    persisted = json.loads((tmp_path / "jobs.json").read_text(encoding="utf-8"))
    assert persisted["source"] == "vestas_dk"
    assert persisted["jobs"][0]["external_job_id"] == "1234567890"
