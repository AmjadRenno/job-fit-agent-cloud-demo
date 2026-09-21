from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.db.models import Base, Company, Job, JobSource, SourcePreview
from backend.app.sources.lifecycle import (
    InvalidSourceTransition,
    archive_company,
    can_transition,
    mark_boundary_changed,
    preview_can_activate,
    transition_source,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def make_source(session: Session, *, lifecycle_status: str = "DRAFT") -> JobSource:
    company = Company(name="Example", domain="example.com", country="Denmark")
    source = JobSource(
        source_id="example_dk",
        display_name="Example Denmark",
        official_jobs_url="https://example.com/careers",
        canonical_url="https://example.com/careers",
        allowed_domains=["example.com"],
        lifecycle_status=lifecycle_status,
        enabled=lifecycle_status == "ACTIVE",
        company=company,
    )
    session.add(source)
    session.flush()
    return source


def test_lifecycle_allows_onboarding_preview_and_approved_activation(session: Session):
    source = make_source(session)
    assert can_transition("DRAFT", "ONBOARDING") is True
    transition_source(source, "ONBOARDING")
    transition_source(source, "PREVIEW_READY")
    with pytest.raises(InvalidSourceTransition, match="preview version"):
        transition_source(source, "ACTIVE", human_approved=True)
    transition_source(source, "ACTIVE", preview_valid=True, human_approved=True, preview_version=1)
    assert source.lifecycle_status == "ACTIVE"
    assert source.enabled is True
    assert source.approved_preview_version == 1


def test_invalid_transitions_and_boundary_changes_are_rejected_or_revalidated(session: Session):
    source = make_source(session)
    with pytest.raises(InvalidSourceTransition):
        transition_source(source, "ACTIVE", preview_valid=True, human_approved=True, preview_version=1)
    with pytest.raises(InvalidSourceTransition):
        transition_source(source, "PREVIEW_READY")
    transition_source(source, "ONBOARDING")
    transition_source(source, "PREVIEW_READY")
    transition_source(source, "ACTIVE", preview_valid=True, human_approved=True, preview_version=1)
    mark_boundary_changed(source)
    assert source.lifecycle_status == "REVALIDATION_REQUIRED"
    assert source.enabled is False


def test_preview_must_match_source_and_be_unexpired(session: Session):
    source = make_source(session)
    transition_source(source, "ONBOARDING")
    transition_source(source, "PREVIEW_READY")
    source.boundary_fingerprint = "boundary-v1"
    preview = SourcePreview(
        source=source,
        version=1,
        boundary_fingerprint="boundary-v1",
        status="READY",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    session.add(preview)
    session.flush()
    assert preview_can_activate(source, preview) is True
    preview.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert preview_can_activate(source, preview) is False


def test_archiving_company_disables_sources_and_preserves_jobs(session: Session):
    source = make_source(session, lifecycle_status="ACTIVE")
    company = source.company
    job = Job(
        source=source,
        company=company,
        source_job_url="https://example.com/careers/role-1",
        title="Role",
        availability_status="ACTIVE",
    )
    session.add(job)
    archive_company(company, [source])
    session.commit()
    assert company.lifecycle_status == "ARCHIVED"
    assert source.lifecycle_status == "ARCHIVED"
    assert source.enabled is False
    assert session.get(Job, job.id) is not None


def test_source_and_preview_uniqueness_constraints(session: Session):
    source = make_source(session)
    session.add(JobSource(
        source_id="other_dk",
        display_name="Other",
        official_jobs_url="https://example.com/careers",
        canonical_url="https://example.com/careers",
        allowed_domains=["example.com"],
    ))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    session.add_all([
        SourcePreview(source=source, version=1, boundary_fingerprint="v1"),
        SourcePreview(source=source, version=1, boundary_fingerprint="v1"),
    ])
    with pytest.raises(IntegrityError):
        session.flush()
