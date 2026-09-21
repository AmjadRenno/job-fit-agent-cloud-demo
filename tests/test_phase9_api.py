from __future__ import annotations

import os
from uuid import uuid4

from typing import Annotated

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.app.cover_letters.api import get_session as cover_get_session
from backend.app.cover_letters.api import service as cover_service_dep
from backend.app.cover_letters.domain import ClaimClassification
from backend.app.cover_letters.service import CoverLetterService
from backend.app.db.models import Base, CoverLetter
from backend.app.main import app
from tests.test_phase9_cover_letters import LANGUAGE_CLAIM, FakeGenerator, draft_for, language_claim_wording, language_evidence_ids, language_proficiency_draft, setup


def test_cover_letter_command_requires_human_authorization():
    session, job, match, _, evidence = setup()
    engine = session.get_bind()
    def override():
        yield session
    app.dependency_overrides[cover_get_session] = override
    os.environ["DASHBOARD_TOKEN"] = "test-token"
    client = TestClient(app)
    payload = {"job_id": str(job.id), "match_id": str(match.id)}
    assert client.post("/api/cover-letters/generate", json=payload).status_code == 401
    assert client.post("/api/cover-letters/generate", headers={"X-Dashboard-Token": "test-token"}, json=payload).status_code == 403
    app.dependency_overrides.clear()
    session.close()


def test_generate_api_returns_draft_and_maps_preconditions():
    session, job, match, _, evidence = setup()
    engine = session.get_bind()
    def override():
        yield session
    def fake_service(session_dep: Annotated[Session, Depends(cover_get_session)]):
        return CoverLetterService(session_dep, FakeGenerator(draft_for(evidence.evidence_id)))
    app.dependency_overrides[cover_get_session] = override
    app.dependency_overrides[cover_service_dep] = fake_service
    os.environ["DASHBOARD_TOKEN"] = "test-token"
    client = TestClient(app)
    payload = {"job_id": str(job.id), "match_id": str(match.id)}
    response = client.post("/api/cover-letters/generate", headers={"X-Dashboard-Token": "test-token", "X-Actor-Type": "HUMAN"}, json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["job_id"] == str(job.id)
    assert body["match_result_id"] == str(match.id)
    assert body["application_id"] is None
    assert body["human_reviewed"] is False
    assert body["grounding_valid"] is True
    assert session.query(CoverLetter).count() == 1
    bad = {"job_id": str(uuid4()), "match_id": str(match.id)}
    assert client.post("/api/cover-letters/generate", headers={"X-Dashboard-Token": "test-token", "X-Actor-Type": "HUMAN"}, json=bad).status_code == 409
    assert session.query(CoverLetter).count() == 1
    app.dependency_overrides.clear()
    session.close()


def test_generate_api_returns_422_for_unsupported_language_overclaim():
    """Regression for the browser E2E cover-letter 422.

    Mirrors the production run against 'Uopfordrede ansøgninger Danmark':
    POST /api/cover-letters/generate must keep returning 422 and must not
    persist a draft when the generator self-labels a DIRECT classification
    against familiarity-only language evidence ('good working proficiency').
    Post claim-derivation (v3) the mapping contract carries no model-authored
    wording, so the exact grounding detail now quotes the deterministic derived
    evidence wording instead of model text.
    """
    session, job, match, _, _ = setup()
    match.evidence = {"requirements": [{
        "requirement": LANGUAGE_CLAIM,
        "job_evidence": "Business-level proficiency in Danish and English is expected.",
        "candidate_evidence_ids": language_evidence_ids(),
    }]}
    session.flush()

    def override():
        yield session

    def fake_service(session_dep: Annotated[Session, Depends(cover_get_session)]):
        return CoverLetterService(session_dep, FakeGenerator(language_proficiency_draft(ClaimClassification.DIRECT)))

    app.dependency_overrides[cover_get_session] = override
    app.dependency_overrides[cover_service_dep] = fake_service
    os.environ["DASHBOARD_TOKEN"] = "test-token"
    client = TestClient(app)
    payload = {"job_id": str(job.id), "match_id": str(match.id)}
    response = client.post(
        "/api/cover-letters/generate",
        headers={"X-Dashboard-Token": "test-token", "X-Actor-Type": "HUMAN"},
        json=payload,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == f"claim classification is inconsistent with evidence: {language_claim_wording()}"
    assert session.query(CoverLetter).count() == 0
    app.dependency_overrides.clear()
    session.close()


def test_review_api_is_human_only_and_updates_metadata_only():
    session, job, match, _, evidence = setup()
    letter = CoverLetter(job_id=job.id, content="Draft", model="test", prompt_version="v1")
    session.add(letter); session.commit()
    def override():
        yield session

    def fake_service(session_dep: Annotated[Session, Depends(cover_get_session)]):
        return CoverLetterService(session_dep, FakeGenerator(draft_for(evidence.evidence_id)))

    app.dependency_overrides[cover_get_session] = override
    app.dependency_overrides[cover_service_dep] = fake_service
    os.environ["DASHBOARD_TOKEN"] = "test-token"
    client = TestClient(app)
    token = {"X-Dashboard-Token": "test-token"}
    url = f"/api/cover-letters/{letter.id}/review"
    assert client.post(url, headers=token, json={"human_reviewed": True}).status_code == 403
    response = client.post(url, headers={**token, "X-Actor-Type": "HUMAN"}, json={"human_reviewed": True, "human_notes": "Checked against profile"})
    assert response.status_code == 200
    body = response.json()
    assert body["human_reviewed"] is True
    assert body["human_notes"] == "Checked against profile"
    assert body["content"] == "Draft"
    row = session.get(CoverLetter, letter.id)
    assert row.human_reviewed is True
    assert row.content == "Draft"
    assert client.post(f"/api/cover-letters/{uuid4()}/review", headers={**token, "X-Actor-Type": "HUMAN"}, json={"human_reviewed": True}).status_code == 404
    app.dependency_overrides.clear()
    session.close()
