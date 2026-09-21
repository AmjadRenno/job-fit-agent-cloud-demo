from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.analysis.domain import Confidence, FitCategory, JobAnalysis as AnalysisContract
from backend.app.candidate_profile.service import CandidateProfileService
from backend.app.cover_letters.domain import ClaimClassification, CoverLetterDraft, GroundedClaimEvidenceMapping, LetterGroundingStatus
from backend.app.cover_letters.grounding import GroundingError, validate_grounding
from backend.app.dashboard.api import get_session
from backend.app.db.models import Base, CandidateProfile, Job, JobAnalysis, JobSource, MatchResult
from backend.app.main import app
from backend.app.matching.evidence import CandidateEvidenceIndex
from backend.app.profile_api import profile_service


ROOT = Path(__file__).parents[1]


def make_profile(tmp_path: Path) -> Path:
    path = tmp_path / "profile.md"
    shutil.copy(ROOT / "data/candidate/profile.md", path)
    return path


def make_session() -> tuple[Session, Job]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine)
    source = JobSource(source_id="solita_dk", display_name="Solita", official_jobs_url="https://www.solita.fi/jobs", canonical_url="https://www.solita.fi/jobs", allowed_domains=["www.solita.fi"], lifecycle_status="ACTIVE", readiness_status="READY", enabled=True)
    session.add(source); session.flush()
    job = Job(source_id=source.id, title="C# Developer", source_job_url="https://www.solita.fi/jobs/csharp", description="Build REST APIs with C# and ASP.NET Core.", content_hash="same-job", availability_status="ACTIVE", analysis_status="COMPLETED")
    session.add(job); session.flush()
    analysis = AnalysisContract(overall_fit=70, fit_category=FitCategory.GOOD, matching_requirements=["C# and ASP.NET Core"], missing_requirements=[], transferable_skills=[], experience_alignment="Relevant evidence exists.", education_alignment="Relevant education exists.", location_alignment="Denmark.", work_arrangement_alignment="Unknown.", strengths=["C#"], concerns=[], evidence=[], confidence=Confidence.MEDIUM, analysis_version="test")
    session.add(JobAnalysis(job_id=job.id, analysis_result=analysis.model_dump(mode="json"), analysis_notes='{"job_content_hash":"same-job"}', model="test", prompt_version="test", created_at=datetime.now(timezone.utc)))
    session.commit()
    return session, job


def test_canonical_facts_ignore_stale_profile_data_and_preferences_stay_separate(tmp_path: Path):
    session, _ = make_session()
    session.add(CandidateProfile(version="legacy-demo", profile_hash="legacy", profile_data={"name": "Prototype Person", "headline": "Fake role", "skills": [{"name": "Invented"}], "minimum_fit": 70, "cover_letter_tone": "Warm & conversational"}))
    session.commit()
    service = CandidateProfileService(session, make_profile(tmp_path))
    assert "Prototype Person" not in {item["claim"] for item in service.facts_view()["items"]}
    assert service.legacy_view()["name"] == "Demo Candidate"
    assert service.preferences()["minimum_fit"] == 70
    before = service.canonical().version
    service.save_preferences({"minimum_fit": 80, "cover_letter_tone": "Concise & technical"})
    assert service.canonical().version == before
    assert service.preferences()["minimum_fit"] == 80
    session.close()


def test_fact_change_refreshes_evidence_and_matches_without_reanalyzing_jobs(tmp_path: Path):
    session, job = make_session()
    service = CandidateProfileService(session, make_profile(tmp_path))
    # Establish the current evidence/profile version and first deterministic match.
    assert service.recalculate_matches() == 1
    old_match = session.query(MatchResult).one()
    old_profile = session.get(CandidateProfile, old_match.candidate_profile_id)
    assert old_profile is not None
    facts, recalculated = service.update_confirmed_facts(["Rust programming through a personal learning project."])
    assert recalculated == 1
    assert session.query(JobAnalysis).filter_by(job_id=job.id).count() == 1
    assert session.query(MatchResult).filter_by(job_id=job.id).count() == 2
    latest = session.query(MatchResult).filter_by(job_id=job.id).order_by(MatchResult.created_at.desc(), MatchResult.id.desc()).first()
    assert session.get(CandidateProfile, latest.candidate_profile_id).version == facts["version"]
    assert old_profile.version != facts["version"]
    updated_item = next(item for item in facts["items"] if item["claim"].startswith("Rust programming"))
    assert updated_item["classification"] == "developing"
    assert (tmp_path / "profile.md.bak").is_file()
    session.close()


def test_user_confirmed_fact_cannot_be_promoted_to_direct_grounded_claim(tmp_path: Path):
    session, _ = make_session()
    service = CandidateProfileService(session, make_profile(tmp_path))
    facts, _ = service.update_confirmed_facts(["Kubernetes operations from recent self-study."])
    item = next(value for value in facts["items"] if value["claim"].startswith("Kubernetes"))
    draft = CoverLetterDraft(
        cover_letter="Unsupported claim",
        claim_evidence_mappings=[GroundedClaimEvidenceMapping(evidence_ids=[item["evidence_id"]], classification=ClaimClassification.DIRECT, rationale="not allowed", claim=item["claim"])],
        grounding_status=LetterGroundingStatus.GROUNDED,
        analysis_version="test",
        matching_version="test",
    )
    with pytest.raises(GroundingError, match="classification is inconsistent"):
        validate_grounding(draft, CandidateEvidenceIndex.from_profile(service.profile_path))
    session.close()


def test_target_role_and_work_preferences_are_not_candidate_evidence(tmp_path: Path):
    index = CandidateEvidenceIndex.from_profile(make_profile(tmp_path))
    assert all(item.section not in {"Target Roles", "Location / Work Preferences"} for item in index.find("Junior Software Developer"))


def test_profile_api_returns_canonical_facts_not_legacy_demo_data(tmp_path: Path):
    session, _ = make_session()
    session.add(CandidateProfile(version="stale", profile_hash="stale", profile_data={"name": "Prototype Person", "skills": [{"name": "Invented"}]}))
    session.commit()

    def override_session():
        yield session

    def override_service():
        return CandidateProfileService(session, make_profile(tmp_path))

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[profile_service] = override_service
    try:
        import os
        os.environ["DASHBOARD_TOKEN"] = "test-token"
        response = TestClient(app).get("/api/profile", headers={"X-Dashboard-Token": "test-token"})
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Demo Candidate"
        assert all(item["claim"] != "Prototype Person" for item in body["facts"]["items"])
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_profile_api_fact_update_reports_only_real_match_recalculation(tmp_path: Path):
    session, job = make_session()

    def override_session():
        yield session

    def override_service():
        return CandidateProfileService(session, make_profile(tmp_path))

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[profile_service] = override_service
    try:
        import os
        os.environ["DASHBOARD_TOKEN"] = "test-token"
        client = TestClient(app)
        response = client.put(
            "/api/profile",
            headers={"X-Dashboard-Token": "test-token", "X-Actor-Type": "HUMAN"},
            json={
                "facts": {"confirmed_additions": ["Rust programming through a personal learning project."]},
                "preferences": {"minimum_fit": 80, "target_roles": ["Backend Developer"], "locations": ["Denmark"], "work_modes": ["Hybrid"], "job_languages": ["English"], "cover_letter_tone": "Concise & technical"},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["recalculated_matches"] == 1
        assert body["preferences"]["minimum_fit"] == 80
        assert session.query(JobAnalysis).filter_by(job_id=job.id).count() == 1
        assert session.query(MatchResult).filter_by(job_id=job.id).count() == 1
    finally:
        app.dependency_overrides.clear()
        session.close()
