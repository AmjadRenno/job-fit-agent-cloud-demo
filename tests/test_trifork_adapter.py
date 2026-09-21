from __future__ import annotations

from backend.app.sources.trifork_discovery import TriforkSourceHandler, _application_status
from backend.app.workflow.sources import resolve_source_handler
from backend.app.workflow.service import JobFitWorkflowService, UnsupportedSourceError
from backend.app.db.models import Base, JobSource
from backend.app.db.repository import ensure_source
from backend.app.workflow.processing import JobProcessingService
from backend.app.analysis.context import AnalysisPreconditionError
from backend.app.analysis.domain import Confidence, FitCategory, JobAnalysis
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
import pytest
from pathlib import Path


LISTING = "https://trifork.com/join/job-openings/"
ATS = "https://trifork.homerun.co"
DUCKWISE = "https://duckwise.dk/career/product-designer-ux/"


def _item(url: str, title: str, city: str, label: str) -> str:
    return (
        f'<a class="job-item" data-location="{city}" data-type="full-time" href="{url}">'
        f'<div class="job-pos job-pos-role"><h3>{title}</h3></div>'
        '<div class="job-pos job-pos-type"><p>Type</p><p>Full-time</p></div>'
        f'<div class="job-pos job-pos-location"><p>Location</p><p>{label}</p></div></a>'
    )


class Response:
    status_code = 200

    def __init__(self, url: str, text: str):
        self.url = url
        self.text = text

    def raise_for_status(self) -> None:
        return None


class Transport:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.calls: list[str] = []
        self.sleep = lambda _: None

    def get(self, url: str) -> Response:
        self.calls.append(url)
        return Response(url, self.pages[url])


def test_trifork_uses_official_danish_list_and_keeps_duckwise_unverified():
    listing = "".join((
        _item(f"{ATS}/sap-developer", "Senior SAP Developer", "aarhus", "Aarhus C, Denmark"),
        _item(f"{ATS}/software-craftsman", "Software Craftsman", "copenhagen", "Copenhagen, Denmark"),
        _item(DUCKWISE, "Product Designer (UX)", "copenhagen", "Copenhagen"),
        _item(f"{ATS}/net-developer", "Medior .NET Developer", "eindhoven", "Eindhoven, NL"),
    ))
    details = {
        f"{ATS}/sap-developer": '<html><body><main><h1>Senior SAP Developer</h1><p>'
        + "Senior SAP Developer role with substantial Danish software delivery responsibilities. " * 4
        + '</p><a href="/sap-developer/da/apply"><span>Notify me</span></a></main></body></html>',
        f"{ATS}/software-craftsman": '<html><body><main><h1>Erfaren software håndværker</h1><p>'
        + "Software Craftsman role developing .NET and web solutions in Copenhagen. " * 5
        + '</p><a href="/software-craftsman/en/apply"><span>Apply now</span></a></main></body></html>',
    }
    transport = Transport({
        "https://trifork.com/robots.txt": "User-agent: *\nDisallow: /blog",
        "https://trifork.homerun.co/robots.txt": "User-agent: *\n",
        LISTING: listing,
        **details,
    })
    handler = TriforkSourceHandler()
    records = handler.discover_jobs(LISTING, transport=transport)
    assert [(record["title"], record["location"], record["country"]) for record in records] == [
        ("Software Craftsman", "Copenhagen, Denmark", ["Denmark"]),
    ]
    assert records[0]["status"] == "active"
    assert "job-specific Apply now" in records[0]["availability_signal"]
    assert any("APPLICATION_CLOSED" in warning for warning in handler.warnings)
    assert handler.external_candidates == [{
        "title": "Product Designer (UX)", "url": DUCKWISE, "host": "duckwise.dk",
    }]
    assert DUCKWISE not in transport.calls
    assert f"{ATS}/net-developer" not in transport.calls


def test_trifork_handler_does_not_claim_other_hosts_or_apply_paths():
    assert resolve_source_handler(LISTING) is not None
    assert resolve_source_handler(f"{ATS}/sap-developer") is not None
    assert resolve_source_handler("https://other.homerun.co/sap-developer") is None
    assert resolve_source_handler(f"{ATS}/apply") is None


def test_discovery_does_not_bypass_source_approval():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        with pytest.raises(UnsupportedSourceError, match="preview and human approval"):
            JobFitWorkflowService(session).discover_and_match(LISTING)
        assert session.scalar(select(Base.metadata.tables["job_sources"].c.source_id)) is None


def test_application_signal_requires_control_for_same_job():
    url = f"{ATS}/sap-developer"
    assert _application_status("<p>Apply now</p>", url) == "unknown"
    assert _application_status('<a href="/another-job/en/apply">Apply now</a>', url) == "unknown"
    assert _application_status('<a href="/sap-developer/da/apply">Notify me</a>', url) == "closed"
    assert _application_status('<a href="/sap-developer/en/apply"><span>Apply now</span></a>', url) == "active"


def test_approved_open_trifork_job_reaches_analysis_and_matching():
    class Analyzer:
        model = "test-analyzer"

        def analyze(self, context):
            return JobAnalysis(
                overall_fit=60,
                fit_category=FitCategory.POSSIBLE,
                matching_requirements=["C# and .NET"],
                missing_requirements=[],
                transferable_skills=[],
                experience_alignment="Relevant software projects.",
                education_alignment="Relevant education.",
                location_alignment="Copenhagen, Denmark.",
                work_arrangement_alignment="Full-time.",
                strengths=["C# project evidence"],
                concerns=[],
                evidence=[],
                confidence=Confidence.MEDIUM,
                analysis_version="phase3-analysis-v1",
            )

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        source = ensure_source(
            session,
            source_id="trifork_dk",
            display_name="Trifork Denmark",
            official_jobs_url=LISTING,
            allowed_domains=["trifork.com", "trifork.homerun.co"],
        )
        session.commit()
        record = {
            "source": "trifork_dk",
            "url": f"{ATS}/software-craftsman",
            "title": "Software Craftsman",
            "description": "Build Danish software with C# and .NET in Copenhagen.",
            "location": "Copenhagen, Denmark",
            "country": ["Denmark"],
            "status": "active",
        }
        processor = JobProcessingService(session, analyzer=Analyzer(), profile_path=Path("data/candidate/profile.md"))
        with pytest.raises(AnalysisPreconditionError, match="human approval"):
            processor.process_record(record)
        session.rollback()
        source_row = session.get(JobSource, source.id)
        source_row.enabled = True
        source_row.lifecycle_status = "ACTIVE"
        session.flush()
        result = processor.process_record(record)
        assert result.analysis_performed is True
        assert result.match_performed is True
        assert result.match is not None
