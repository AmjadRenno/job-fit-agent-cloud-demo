from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.analysis.domain import Confidence, FitCategory, JobAnalysis
from backend.app.cover_letters.api import get_session as cover_get_session
from backend.app.cover_letters.domain import ClaimClassification, ClaimEvidenceMapping, CoverLetterDraft, LetterGroundingStatus
from backend.app.db.models import Base, CandidateProfile, CoverLetter, Job, JobAnalysis as JobAnalysisRow, MatchResult
from backend.app.main import app
from backend.app.workflow.api import get_session as workflow_get_session, workflow_service
from backend.app.workflow.service import JobFitWorkflowService


class FakeAnalyzer:
    model = "fake-analyzer-v1"

    def __init__(self, result: JobAnalysis | None = None) -> None:
        self.result = result or JobAnalysis(
            overall_fit=78,
            fit_category=FitCategory.GOOD,
            matching_requirements=["ASP.NET Core", "Python"],
            missing_requirements=["Kubernetes production deployment"],
            transferable_skills=["Cloud platform experience"],
            experience_alignment="Candidate has demonstrated experience in relevant technologies.",
            education_alignment="Datamatiker foundation aligns well with software requirements.",
            location_alignment="Location in Denmark aligns with job requirements.",
            work_arrangement_alignment="Hybrid work model is acceptable.",
            strengths=["ASP.NET Core", "Software engineering experience"],
            concerns=["Lack of extensive production Kubernetes experience"],
            evidence=[],
            confidence=Confidence.HIGH,
            analysis_version="phase3-analysis-v1",
        )

    def analyze(self, context):
        return self.result


class FakeCoverLetterGenerator:
    model = "fake-generator-v1"

    def __init__(self, evidence_id: str | None = None) -> None:
        self.evidence_id = evidence_id

    def generate(self, context) -> CoverLetterDraft:
        evidence_id = self.evidence_id
        if not evidence_id and context.get("candidate_evidence"):
            evidence_id = context["candidate_evidence"][0]["evidence_id"]
        evidence_id = evidence_id or "c-and-net-ecosystem-asp-net-core-blazor-entity-framework-core-rest-apis-asp-net-core-identity-and-web-application-development"
        return CoverLetterDraft(
            cover_letter="I am excited to apply for this role given my strong ASP.NET Core experience.",
            claim_evidence_mappings=[
                ClaimEvidenceMapping(
                    evidence_ids=[evidence_id],
                    classification=ClaimClassification.DIRECT,
                    rationale="Directly supported by candidate profile evidence.",
                )
            ],
            grounding_status=LetterGroundingStatus.GROUNDED,
            validation_notes=[],
            analysis_version="phase3-analysis-v1",
            matching_version="phase5-matching-v1",
        )


SOLITA_HTML_SAMPLE = """<!DOCTYPE html>
<html>
<head>
    <title>Senior Developer - Solita</title>
    <script type="application/ld+json">
    {
        "@type": "JobPosting",
        "title": "Senior Developer",
        "description": "Join our team at Solita Denmark. We build cloud and data solutions. Apply to this position.",
        "employmentType": "FULL_TIME",
        "jobLocation": {
            "address": {
                "addressLocality": "Aalborg",
                "addressCountry": "Denmark"
            }
        }
    }
    </script>
</head>
<body>
    <main>
        <h1>Senior Developer</h1>
        <p>Join our team at Solita Denmark. We build cloud and data solutions. Apply to this position.</p>
    </main>
</body>
</html>
"""


@pytest.fixture
def test_setup():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)

    fake_analyzer = FakeAnalyzer()
    service_instance = JobFitWorkflowService(
        session=session,
        analyzer=fake_analyzer,
        profile_path=Path("data/candidate/profile.md"),
    )

    def override_session():
        yield session

    def override_service():
        return service_instance

    app.dependency_overrides[workflow_get_session] = override_session
    app.dependency_overrides[cover_get_session] = override_session
    app.dependency_overrides[workflow_service] = override_service
    os.environ["DASHBOARD_TOKEN"] = "test-token"

    client = TestClient(app)
    try:
        yield client, session
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_workflow_requires_authentication(test_setup):
    client, _ = test_setup
    payload = {
        "url": "https://www.solita.fi/positions/senior-developer-12345/",
        "html_content": SOLITA_HTML_SAMPLE,
    }

    # Missing all auth headers
    res = client.post("/api/workflow/analyze", json=payload)
    assert res.status_code == 401

    # Missing human actor header
    res = client.post(
        "/api/workflow/analyze",
        headers={"X-Dashboard-Token": "test-token"},
        json=payload,
    )
    assert res.status_code == 403


def test_workflow_rejects_unsupported_sources(test_setup):
    client, _ = test_setup
    headers = {"X-Dashboard-Token": "test-token", "X-Actor-Type": "HUMAN"}

    res = client.post(
        "/api/workflow/analyze",
        headers=headers,
        json={"url": "https://unapproved-source.com/jobs/123"},
    )
    assert res.status_code == 400
    assert "not from an approved job source" in res.json()["detail"]


def test_workflow_end_to_end_extraction_analysis_matching(test_setup):
    client, session = test_setup
    headers = {"X-Dashboard-Token": "test-token", "X-Actor-Type": "HUMAN"}
    target_url = "https://www.solita.fi/positions/senior-developer-99999/"

    payload = {
        "url": target_url,
        "html_content": SOLITA_HTML_SAMPLE,
    }

    res = client.post("/api/workflow/analyze", headers=headers, json=payload)
    assert res.status_code == 200
    data = res.json()

    # Verify Job
    assert data["job"]["title"] == "Senior Developer"
    assert data["job"]["source"] == "solita_dk"
    assert data["job"]["availability_status"] == "ACTIVE"

    # Verify Analysis
    assert data["analysis"]["analysis_result"]["fit_category"] == "GOOD"
    assert data["analysis"]["job_id"] == data["job"]["id"]

    # Verify Match
    assert data["match"]["job_id"] == data["job"]["id"]
    assert data["match"]["analysis_id"] == data["analysis"]["id"]
    assert data["match"]["overall_score"] > 0
    assert data["match"]["confidence"] in {"HIGH", "MEDIUM", "LOW"}

    # Verify Database Persistence
    assert session.query(Job).count() == 1
    assert session.query(JobAnalysisRow).count() == 1
    assert session.query(MatchResult).count() == 1

    # Verify subsequent cover letter generation succeeds with the created job and match
    from backend.app.cover_letters.api import service as cover_service_dep
    from backend.app.cover_letters.service import CoverLetterService

    def override_cover_service():
        return CoverLetterService(session, FakeCoverLetterGenerator())

    app.dependency_overrides[cover_service_dep] = override_cover_service

    cl_res = client.post(
        "/api/cover-letters/generate",
        headers=headers,
        json={
            "job_id": data["job"]["id"],
            "match_id": data["match"]["id"],
            "profile_path": "data/candidate/profile.md",
        },
    )
    assert cl_res.status_code == 201
    assert "ASP.NET Core" in cl_res.json()["content"]


def test_workflow_discovery_ranks_and_skips_reanalysis_for_unchanged_jobs(test_setup, monkeypatch):
    client, session = test_setup
    headers = {"X-Dashboard-Token": "test-token", "X-Actor-Type": "HUMAN"}

    class RecordingAnalyzer(FakeAnalyzer):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def analyze(self, context):
            self.calls += 1
            return super().analyze(context)

    analyzer = RecordingAnalyzer()
    service = JobFitWorkflowService(session=session, analyzer=analyzer, profile_path=Path("data/candidate/profile.md"))

    class FakeHandler:
        source_id = "solita_dk"
        display_name = "Solita Denmark"

        def can_handle(self, url):
            return url.startswith("https://www.solita.fi/")

        def discover_jobs(self, careers_url, *, transport=None):
            return [
                {"url": "https://www.solita.fi/positions/strong-match/", "title": "Senior Platform Engineer", "description": "Strong cloud engineering and C# experience.", "location": "Copenhagen, Denmark", "employment_type": "FULL_TIME", "status": "active", "source": "solita_dk"},
                {"url": "https://www.solita.fi/positions/weak-match/", "title": "Junior Operations Analyst", "description": "Basic spreadsheet and coordination tasks.", "location": "Aalborg, Denmark", "employment_type": "FULL_TIME", "status": "active", "source": "solita_dk"},
            ]

        def extract_record(self, url, *, html_content=None, transport=None):
            record_map = {
                "https://www.solita.fi/positions/strong-match/": {
                    "url": url,
                    "title": "Senior Platform Engineer",
                    "description": "Strong cloud engineering and C# experience. Build and operate distributed systems.",
                    "location": "Copenhagen, Denmark",
                    "employment_type": "FULL_TIME",
                    "status": "active",
                    "source": "solita_dk",
                    "external_job_id": "strong-match",
                    "availability_signal": "Apply to this position",
                },
                "https://www.solita.fi/positions/weak-match/": {
                    "url": url,
                    "title": "Junior Operations Analyst",
                    "description": "Coordinate reports and general operations tasks.",
                    "location": "Aalborg, Denmark",
                    "employment_type": "FULL_TIME",
                    "status": "active",
                    "source": "solita_dk",
                    "external_job_id": "weak-match",
                    "availability_signal": "Apply to this position",
                },
            }
            return record_map[url]

    monkeypatch.setattr("backend.app.workflow.service.resolve_source_handler", lambda url: FakeHandler())
    monkeypatch.setattr("backend.app.workflow.sources.resolve_source_handler", lambda url: FakeHandler())

    res = service.discover_and_match("https://www.solita.fi/join-us/?country=denmark")
    assert [item["job"]["title"] for item in res] == ["Senior Platform Engineer", "Junior Operations Analyst"]
    assert res[0]["match"]["overall_score"] >= res[1]["match"]["overall_score"]
    assert analyzer.calls == 2

    repeat = service.discover_and_match("https://www.solita.fi/join-us/?country=denmark")
    assert [item["job"]["title"] for item in repeat] == ["Senior Platform Engineer", "Junior Operations Analyst"]
    assert analyzer.calls == 2

    res_api = client.post("/api/workflow/discover", headers=headers, json={"careers_url": "https://www.solita.fi/join-us/?country=denmark"})
    assert res_api.status_code == 200
    assert [item["job"]["title"] for item in res_api.json()] == ["Senior Platform Engineer", "Junior Operations Analyst"]


def test_single_job_then_discovery_reuses_analysis_and_match(test_setup, monkeypatch):
    _, session = test_setup

    class RecordingAnalyzer(FakeAnalyzer):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def analyze(self, context):
            self.calls += 1
            return super().analyze(context)

    analyzer = RecordingAnalyzer()
    service = JobFitWorkflowService(session=session, analyzer=analyzer, profile_path=Path("data/candidate/profile.md"))
    url = "https://www.solita.fi/positions/example-7926456003/"
    record = {
        "url": url,
        "title": "Senior Data Engineer",
        "description": "Build data platforms. Apply to this position.",
        "location": "Denmark",
        "employment_type": "FULL_TIME",
        "status": "active",
        "source": "solita_dk",
        "external_job_id": "7926456003",
        "availability_signal": "Apply to this position",
    }

    class FakeHandler:
        source_id = "solita_dk"
        display_name = "Solita Denmark"

        def can_handle(self, value):
            return value.startswith("https://www.solita.fi/")

        def extract_record(self, value, *, html_content=None, transport=None):
            return {**record, "url": value}

        def discover_jobs(self, careers_url, *, transport=None):
            return [{**record, "country": ["Denmark"]}]

    monkeypatch.setattr("backend.app.workflow.service.resolve_source_handler", lambda value: FakeHandler())

    first = service.process_url(url)
    first_hash = session.query(Job).one().content_hash
    second = service.discover_and_match("https://www.solita.fi/join-us/?country=denmark")

    assert first.job.id == second[0].job.id
    assert first_hash == session.query(Job).one().content_hash
    assert analyzer.calls == 1
    assert session.query(JobAnalysisRow).count() == 1
    assert session.query(MatchResult).count() == 1
