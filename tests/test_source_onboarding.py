from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.db.models import AgentRun, Base, ExecutionEvent, Job, JobAnalysis, JobSource, MatchResult, SourcePreview
from backend.app.sources import onboarding
from backend.app.sources.discovery import SourceBlockedError, SourceContract
from backend.app.sources.onboarding import OnboardingError, OnboardingTrialService


class FakeHandler:
    source_id = "solita_dk"
    display_name = "Solita Denmark"

    def __init__(self, records=None, error=None):
        self.records = records or []
        self.error = error
        self.urls: list[str] = []

    def can_handle(self, url: str) -> bool:
        return url.startswith("https://www.solita.fi/")

    def extract_record(self, url: str, *, html_content=None, transport=None):
        raise AssertionError("onboarding trial must use bounded discovery")

    def discover_jobs(self, careers_url: str, *, transport=None):
        self.urls.append(careers_url)
        if self.error:
            raise self.error
        return self.records


class UnsupportedPreviewHandler(FakeHandler):
    source_id = "energinet_dk"
    display_name = "Energinet Denmark"
    execution_ready = False
    contract = SourceContract(
        source_id=source_id,
        careers_url="https://jobs.example/careers/",
        allowed_domains=frozenset({"jobs.example"}),
        allowed_paths=("/careers/", "/job/"),
    )

    def can_handle(self, url: str) -> bool:
        return url.startswith("https://jobs.example/")


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def record(url="https://www.solita.fi/positions/role-123/", *, title="Platform Engineer", description="Build reliable APIs."):
    return {
        "source": "solita_dk",
        "url": url,
        "title": title,
        "description": description,
        "location": "Copenhagen, Denmark",
        "country": ["Denmark"],
        "employment_type": "FULL_TIME",
        "status": "unknown",
        "external_job_id": "123",
    }


def use_handler(monkeypatch, handler):
    monkeypatch.setattr(onboarding, "resolve_source_handler", lambda url: handler)


def test_successful_trial_persists_preview_and_reaches_preview_ready_only(session, monkeypatch):
    handler = FakeHandler([record()])
    use_handler(monkeypatch, handler)
    preview = OnboardingTrialService(session, now=lambda: datetime(2026, 9, 12, tzinfo=timezone.utc), handlers=[handler]).run(
        "https://www.solita.fi/join-us/?country=denmark#review"
    )

    source = session.query(JobSource).filter_by(source_id="solita_dk").one()
    assert handler.urls == ["https://www.solita.fi/join-us/?country=denmark"]
    assert preview.status == "READY"
    assert preview.version == 1
    assert preview.boundary_fingerprint == source.boundary_fingerprint
    assert source.lifecycle_status == "PREVIEW_READY"
    assert source.enabled is False
    assert source.readiness_status == "READY"
    assert preview.validation_summary["readiness"]["level"] == "HIGH"
    assert preview.validation_summary["sample_jobs"][0]["title"] == "Platform Engineer"
    assert preview.validation_summary["sample_jobs"][0]["url"] == "https://www.solita.fi/positions/role-123/"
    assert session.query(Job).count() == 0
    assert session.query(JobAnalysis).count() == 0
    assert session.query(MatchResult).count() == 0
    assert session.query(AgentRun).one().status == "SUCCESS"
    assert {event.event_type for event in session.query(ExecutionEvent)} >= {
        "onboarding_started", "onboarding_validation", "onboarding_robots_check",
        "onboarding_discovery", "onboarding_extraction", "onboarding_preview_created",
    }


def test_disabled_source_can_be_onboarded_again(session, monkeypatch):
    handler = FakeHandler([record()])
    use_handler(monkeypatch, handler)
    source = JobSource(
        source_id="solita_dk",
        display_name="Solita Denmark",
        official_jobs_url="https://www.solita.fi/join-us/",
        canonical_url="https://www.solita.fi/join-us/",
        allowed_domains=["www.solita.fi"],
        enabled=False,
        lifecycle_status="DISABLED",
    )
    session.add(source)
    session.commit()

    preview = OnboardingTrialService(session, handlers=[handler]).run(
        "https://www.solita.fi/join-us/"
    )

    assert preview.version == 1
    assert source.lifecycle_status == "PREVIEW_READY"
    assert source.readiness_status == "READY"


def test_mixed_valid_and_invalid_records_are_previewed_with_warning(session, monkeypatch):
    handler = FakeHandler([
        record(),
        record("https://evil.example/jobs/2/", title="External"),
        {"url": "https://www.solita.fi/positions/no-title/", "description": "text", "source": "solita_dk"},
    ])
    use_handler(monkeypatch, handler)
    preview = OnboardingTrialService(session, handlers=[handler]).run("https://www.solita.fi/join-us/")
    validation = preview.validation_summary["validation"]
    assert validation["valid_jobs"] == 1
    assert validation["rejected_jobs"] == 2
    assert preview.validation_summary["readiness"]["level"] == "MEDIUM"


def test_zero_jobs_discovered_persists_preview_with_warning(session, monkeypatch):
    handler = FakeHandler([])
    use_handler(monkeypatch, handler)
    preview = OnboardingTrialService(session, handlers=[handler]).run("https://www.solita.fi/join-us/")
    source = session.query(JobSource).filter_by(source_id="solita_dk").one()
    assert preview.status == "READY"
    assert preview.validation_summary["discovery"]["jobs_discovered"] == 0
    assert any("NO_JOBS_DISCOVERED" in w for w in preview.validation_summary["validation"]["warnings"])
    assert source.lifecycle_status == "PREVIEW_READY"
    assert session.query(SourcePreview).count() == 1
    assert session.query(Job).count() == 0
    assert session.query(AgentRun).one().status == "SUCCESS"


def test_safety_failure_is_categorized_and_fails_closed(session, monkeypatch):
    handler = FakeHandler(error=SourceBlockedError("robots.txt disallows URL"))
    use_handler(monkeypatch, handler)
    with pytest.raises(OnboardingError) as raised:
        OnboardingTrialService(session, handlers=[handler]).run("https://www.solita.fi/join-us/")
    assert raised.value.code == "ROBOTS_DISALLOWED"
    assert raised.value.failure.safety_blocked is True
    assert session.query(SourcePreview).count() == 0
    assert session.query(Job).count() == 0


def test_url_validation_rejects_unsafe_input_before_source_resolution(session, monkeypatch):
    handler = FakeHandler([record()])
    use_handler(monkeypatch, handler)
    service = OnboardingTrialService(session, handlers=[handler])
    for value, expected in (
        ("javascript:alert(1)", "UNSUPPORTED_SCHEME"),
        ("http://www.solita.fi/join-us/", "UNSUPPORTED_SCHEME"),
        ("https://localhost/careers/", "INVALID_URL"),
        ("https://127.0.0.1/careers/", "INVALID_URL"),
        ("not a url", "UNSUPPORTED_SCHEME"),
    ):
        with pytest.raises(OnboardingError) as raised:
            service.run(value)
        assert raised.value.code == expected
    assert handler.urls == []
    assert session.query(JobSource).count() == 0


def test_retryable_discovery_failure_is_classified_without_activation(session, monkeypatch):
    handler = FakeHandler(error=TimeoutError("timeout"))
    use_handler(monkeypatch, handler)
    with pytest.raises(OnboardingError) as raised:
        OnboardingTrialService(session, handlers=[handler]).run("https://www.solita.fi/join-us/")
    assert raised.value.code == "TIMEOUT"
    assert raised.value.failure.retryable is True
    source = session.query(JobSource).filter_by(source_id="solita_dk").one()
    assert source.lifecycle_status == "ONBOARDING"
    assert source.enabled is False


def test_unsupported_handler_can_create_preview_but_is_not_execution_ready(session, monkeypatch):
    handler = UnsupportedPreviewHandler([record("https://jobs.example/job/1/", title="Example role")])
    use_handler(monkeypatch, handler)
    preview = OnboardingTrialService(session, handlers=[]).run(
        "https://jobs.example/careers/",
        company_name="Example",
        company_domain="jobs.example",
    )

    source = session.query(JobSource).filter_by(source_id="energinet_dk").one()
    assert preview.status == "READY"
    assert preview.validation_summary["execution"]["ready"] is False
    assert preview.validation_summary["readiness"]["status"] == "EXECUTION_NOT_READY"
    assert source.lifecycle_status == "PREVIEW_READY"
    assert source.readiness_status == "BLOCKED"
    assert source.enabled is False
    assert session.query(Job).count() == 0


class GenericPreviewTransport:
    def __init__(self, pages):
        self.pages = pages
        self.requested = []
        self.sleep = lambda _: None

    def get(self, url):
        from types import SimpleNamespace
        self.requested.append(url)
        return SimpleNamespace(status_code=200, text=self.pages[url], url=url)


def test_generic_preview_rejects_navigation_and_external_ats_links(session):
    careers_url = "https://example.dk/join/job-openings/"
    transport = GenericPreviewTransport({
        "https://example.dk/robots.txt": "User-agent: *\nAllow: /\n",
        careers_url: """<a href='/about/'>About</a><a href='/contact/'>Contact</a>
            <a href='https://example.homerun.co/senior-developer'>Senior Developer Type Full-time Location Copenhagen</a>""",
    })
    preview = OnboardingTrialService(session, transport=transport).run(careers_url, source_id="example_preview")
    summary = preview.validation_summary
    assert summary["discovery"]["jobs_verified"] == 0
    assert summary["sample_jobs"] == []
    assert summary["external_job_candidates"] == [{
        "title": "Senior Developer", "host": "example.homerun.co",
        "url": "https://example.homerun.co/senior-developer",
    }]
    assert any("EXTERNAL_ATS_NOT_APPROVED" in warning for warning in summary["validation"]["warnings"])
    assert transport.requested == ["https://example.dk/robots.txt", careers_url]


def test_generic_preview_verifies_current_jobposting_and_rejects_expired_detail(session):
    careers_url = "https://example.dk/jobs/"
    current_url = "https://example.dk/jobs/data-engineer/"
    expired_url = "https://example.dk/jobs/old-engineer/"
    description = "Build data platforms and reliable pipelines for our customers. " * 3
    def detail(title, valid_through):
        import json
        posting = {"@type": "JobPosting", "title": title, "description": description,
                   "jobLocation": {"address": {"addressLocality": "Copenhagen", "addressCountry": "DK"}},
                   "validThrough": valid_through}
        return f"<html><script type='application/ld+json'>{json.dumps(posting)}</script><main><h1>{title}</h1></main></html>"
    transport = GenericPreviewTransport({
        "https://example.dk/robots.txt": "User-agent: *\nAllow: /\n",
        careers_url: "<a href='/about/'>About</a><a href='/jobs/data-engineer/'>Data Engineer</a><a href='/jobs/old-engineer/'>Old Engineer</a>",
        current_url: detail("Data Engineer", "2099-12-31"),
        expired_url: detail("Old Engineer", "2020-01-01"),
    })
    preview = OnboardingTrialService(session, transport=transport).run(careers_url, source_id="example_preview")
    summary = preview.validation_summary
    assert summary["discovery"]["jobs_verified"] == 1
    assert summary["sample_jobs"][0]["title"] == "Data Engineer"
    assert summary["sample_jobs"][0]["country"] == ["DK"]
    assert summary["sample_jobs"][0]["status"] == "unknown"
    assert summary["sample_jobs"][0]["valid_through"] == "2099-12-31"
    assert any("JOB_POSTING_EXPIRED" in warning for warning in summary["validation"]["warnings"])
    assert "https://example.dk/about/" not in transport.requested
