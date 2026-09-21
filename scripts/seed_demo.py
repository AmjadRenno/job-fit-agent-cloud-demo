"""Idempotently create the synthetic V3 portfolio demo dataset."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from backend.app.analysis.domain import Confidence, FitCategory, JobAnalysis as AnalysisContract
from backend.app.db.models import AgentRun, JobAnalysis
from backend.app.db.repository import JobRepository, ensure_source
from backend.app.db.session import create_session_factory
from backend.app.matching.service import JobMatchingService
from backend.app.mode import is_demo_mode

DEMO_SOURCE_ID = "demo_synthetic"
DEMO_PROMPT_VERSION = "demo-seed-v2"
DEMO_JOBS = [
    ("dotnet-backend", ".NET Backend Developer", "Build C# and ASP.NET Core APIs with PostgreSQL, Docker, and Azure.", ["C# and .NET", "ASP.NET Core APIs"], [], Confidence.HIGH),
    ("ai-product", "AI Product Developer", "Create React and TypeScript experiences powered by Generative AI and LLM APIs. Familiarity with MCP is a plus.", ["React and TypeScript"], [], Confidence.LOW),
    ("full-stack", "Full-stack Developer", "Develop FastAPI services and React interfaces, using PostgreSQL and Docker in a product team.", ["Python and FastAPI", "React"], ["PostgreSQL"], Confidence.HIGH),
    ("cloud-devops", "Cloud / DevOps Engineer", "Operate Azure infrastructure, Docker delivery pipelines, Redis, and Elasticsearch for reliable services.", ["Docker"], ["Azure infrastructure", "Redis", "Elasticsearch"], Confidence.MEDIUM),
    ("senior-platform", "Senior Platform Architect", "Lead a platform organisation with 10+ years of Azure architecture and people leadership.", [], ["10+ years of Azure architecture", "people leadership"], Confidence.LOW),
    ("product-engineer", "Product Engineer", "Build accessible TypeScript product features and collaborate on backend API design.", ["TypeScript", "REST APIs"], ["accessibility"], Confidence.MEDIUM),
]


def require_demo_seed_mode() -> None:
    if not is_demo_mode():
        raise RuntimeError("Synthetic demo seed requires APP_MODE=demo")


def _analysis(title: str, matched: list[str], missing: list[str], confidence: Confidence) -> AnalysisContract:
    return AnalysisContract(
        overall_fit=75 if matched else 25,
        fit_category=FitCategory.GOOD if matched else FitCategory.WEAK,
        matching_requirements=matched,
        missing_requirements=missing,
        transferable_skills=[],
        experience_alignment=f"Synthetic analysis for {title}.",
        education_alignment="Synthetic demo evidence is intentionally limited.",
        location_alignment="Synthetic Denmark-based demo role.",
        work_arrangement_alignment="Synthetic hybrid role.",
        strengths=matched,
        concerns=missing,
        evidence=[],
        confidence=confidence,
        analysis_version=DEMO_PROMPT_VERSION,
    )


def seed_demo(session, profile_path: Path) -> dict[str, int]:
    source = ensure_source(session, source_id=DEMO_SOURCE_ID, display_name="Demo Synthetic Jobs", official_jobs_url="https://demo.invalid/jobs", allowed_domains=["demo.invalid"], robots_txt_url="https://demo.invalid/robots.txt", enabled=True, company_name="Demo Works", company_domain="demo.invalid")
    source.lifecycle_status = "ACTIVE"; source.readiness_status = "READY"; source.health_status = "HEALTHY"
    run = session.scalar(select(AgentRun).where(AgentRun.error_summary == "synthetic demo seed"))
    if run is None:
        run = AgentRun(started_at=datetime.now(timezone.utc), ended_at=datetime.now(timezone.utc), status="SUCCESS", sources_total=1, sources_success=1, error_summary="synthetic demo seed")
        session.add(run); session.flush()
    created = analyses = matches = 0
    repository = JobRepository(session)
    for key, title, description, matched, missing, confidence in DEMO_JOBS:
        existed = session.scalar(select(__import__('backend.app.db.models', fromlist=['Job']).Job).where(__import__('backend.app.db.models', fromlist=['Job']).Job.source_id == source.id, __import__('backend.app.db.models', fromlist=['Job']).Job.external_job_id == key))
        job = repository.upsert_job({"source": DEMO_SOURCE_ID, "external_job_id": key, "url": f"https://demo.invalid/jobs/{key}", "title": title, "description": "DEMO / SYNTHETIC — " + description, "location": "Copenhagen", "country": ["Denmark"], "employment_type": "FULL_TIME", "status": "ACTIVE", "language": "English"})
        # PostgreSQL does not assign this UUID until the upsert is flushed.
        # The following persisted-analysis lookup must never use a null job id.
        session.flush()
        created += int(existed is None)
        analysis = session.scalar(select(JobAnalysis).where(JobAnalysis.job_id == job.id, JobAnalysis.prompt_version == DEMO_PROMPT_VERSION))
        if analysis is None:
            contract = _analysis(title, matched, missing, confidence)
            analysis = JobAnalysis(job_id=job.id, run_id=run.id, analysis_result=contract.model_dump(mode="json"), analysis_notes='{"synthetic":true}', model="deterministic-demo-seed", prompt_version=DEMO_PROMPT_VERSION, schema_valid=True)
            session.add(analysis); job.analysis_status = "COMPLETED"; session.flush(); analyses += 1
        from backend.app.db.models import MatchResult
        if session.scalar(select(MatchResult).where(MatchResult.analysis_id == analysis.id)) is None:
            JobMatchingService(session).match_job(job.id, profile_path, run_id=run.id); matches += 1
    run.jobs_discovered = len(DEMO_JOBS); run.jobs_new = created; run.jobs_analyzed = len(DEMO_JOBS); run.jobs_matched = len(DEMO_JOBS)
    session.commit()
    return {"jobs_created": created, "analyses_created": analyses, "matches_created": matches, "jobs_total": len(DEMO_JOBS)}


if __name__ == "__main__":
    require_demo_seed_mode()
    with create_session_factory()() as session:
        print(seed_demo(session, Path(__file__).resolve().parents[1] / "data/candidate/profile.md"))
