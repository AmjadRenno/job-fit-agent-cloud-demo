from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.import_json import import_phase1_json
from backend.app.db.models import Application, Base, CoverLetter, Job, JobAnalysis, MatchResult
from backend.app.db.repository import JobRepository, canonical_job_values, ensure_source


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def add_solita_source(session):
    return ensure_source(
        session,
        source_id="solita_dk",
        display_name="Solita Denmark",
        official_jobs_url="https://www.solita.fi/join-us/?country=denmark",
        allowed_domains=["www.solita.fi"],
        robots_txt_url="https://www.solita.fi/robots.txt",
        crawl_delay_seconds=10,
        enabled=True,
    )


def test_canonical_job_resolves_string_source_to_foreign_key(session):
    source = add_solita_source(session)
    job = JobRepository(session).upsert_job({
        "source": "solita_dk",
        "title": "Senior Data Engineer",
        "url": "https://www.solita.fi/positions/example-7926456003/",
        "description": "Build data platforms.",
        "location": "Denmark",
        "country": ["Denmark"],
        "employment_type": "unknown",
        "status": "unknown",
    })
    session.flush()
    assert job.source_id == source.id
    assert job.country_code == "DK"
    assert job.city is None
    assert job.is_hybrid is None
    assert job.is_remote is None
    assert job.employment_type == "UNKNOWN"
    assert job.availability_status == "UNKNOWN"


def test_equivalent_solita_country_inputs_produce_the_same_canonical_hash():
    base = {
        "source": "solita_dk",
        "title": "Senior Data Engineer",
        "url": "https://www.solita.fi/positions/example-7926456003/",
        "description": "Build data platforms.",
        "location": "",
        "employment_type": "unknown",
        "status": "unknown",
    }

    hashes = {
        canonical_job_values({**base, "country": country})["content_hash"]
        for country in (None, "Denmark", ["Denmark"])
    }

    assert len(hashes) == 1


def test_unknown_and_availability_checks_are_enforced(session):
    add_solita_source(session)
    repository = JobRepository(session)
    job = repository.upsert_job({
        "source": "solita_dk", "title": "Role", "url": "https://www.solita.fi/role",
        "employment_type": "UNKNOWN", "status": "UNKNOWN",
    })
    session.flush()
    assert job.employment_type == "UNKNOWN"
    with pytest.raises(IntegrityError):
        session.add(Job(source_id=job.source_id, source_job_url="https://www.solita.fi/other", title="Role", employment_type="FULL_TIME", availability_status="NOT_A_STATUS"))
        session.flush()


def test_source_scoped_url_uniqueness_is_enforced(session):
    add_solita_source(session)
    repository = JobRepository(session)
    record = {"source": "solita_dk", "title": "Role", "url": "https://www.solita.fi/role"}
    repository.upsert_job(record)
    session.flush()
    repository.upsert_job(record)
    session.flush()
    assert session.query(Job).count() == 1


def test_external_job_id_survives_a_changed_source_url(session):
    add_solita_source(session)
    repository = JobRepository(session)
    first = repository.upsert_job({
        "source": "solita_dk",
        "external_job_id": "stable-123",
        "title": "Original title",
        "url": "https://www.solita.fi/original-role",
    })
    session.flush()

    refreshed = repository.upsert_job({
        "source": "solita_dk",
        "external_job_id": "stable-123",
        "title": "Updated title",
        "url": "https://www.solita.fi/updated-role",
    })
    session.flush()

    assert refreshed.id == first.id
    assert refreshed.source_job_url == "https://www.solita.fi/updated-role"
    assert refreshed.title == "Updated title"
    assert session.query(Job).count() == 1


def test_multiple_analyses_for_same_job_and_run_are_allowed(session):
    add_solita_source(session)
    job = JobRepository(session).upsert_job({"source": "solita_dk", "title": "Role", "url": "https://www.solita.fi/role"})
    session.flush()
    first = JobAnalysis(job_id=job.id, model="model-a", prompt_version="v1")
    second = JobAnalysis(job_id=job.id, model="model-b", prompt_version="v2")
    session.add_all([first, second])
    session.flush()
    assert first.id != second.id


def test_phase1_json_import_keeps_artifact_and_persists_mapping(session):
    artifact = Path("data/runs/2026-08-25/solita/jobs.json")
    if not artifact.exists():
        pytest.skip("Phase 1 artifact is not present")
    jobs = import_phase1_json(session, artifact)
    session.commit()
    assert len(jobs) == 1
    assert jobs[0].source.source_id == "solita_dk"
    assert jobs[0].availability_status == "ACTIVE"
    assert json.loads(artifact.read_text(encoding="utf-8"))["source"] == "solita_dk"


def test_migration_target_contains_expected_tables():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"companies", "job_sources", "source_previews", "jobs", "agent_runs", "job_analyses", "applications", "application_events", "execution_events"} <= tables
