from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
import hmac
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.db.session import get_session_factory

from .schemas import AnalysisRead, ApplicationRead, DashboardSnapshot, EventRead, HistoryRead, JobPage, JobRead, MatchRead, RunRead, SourceHealthRead, SourceHealthSummaryRead
from .service import DashboardService

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def get_session() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def authorize_dashboard(x_dashboard_token: Annotated[str | None, Header()] = None) -> None:
    from backend.app.mode import is_demo_mode
    if is_demo_mode():
        return
    # Local-only operator boundary: callers must provide the configured token.
    from os import environ
    expected = environ.get("DASHBOARD_TOKEN")
    if not expected or not isinstance(x_dashboard_token, str) or not hmac.compare_digest(x_dashboard_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="dashboard authorization required")


SessionDep = Annotated[Session, Depends(get_session)]
AuthDep = Annotated[None, Depends(authorize_dashboard)]


def service(session: SessionDep) -> DashboardService:
    return DashboardService(session)


@router.get("/jobs", response_model=JobPage)
def jobs(
    _: AuthDep,
    query_service: Annotated[DashboardService, Depends(service)],
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), status_filter: str | None = Query(None, alias="status", pattern="^(ACTIVE|CLOSED|EXPIRED|FILLED|UNAVAILABLE|UNKNOWN)$"),
    view: Literal["recommended", "unseen", "applied", "all"] = "all",
    company_id: UUID | None = None,
    search: str | None = Query(None, max_length=200),
    analysis_status: str | None = Query(None, max_length=40),
    application_status: str | None = Query(None, max_length=40),
    starred: bool | None = None,
    sort: Literal["best_fit", "newest_discovered", "newest_posted"] = "newest_discovered",
) -> JobPage:
    return query_service.jobs(page=page, page_size=page_size, status=status_filter, view=view, company_id=company_id, search=search, analysis_status=analysis_status, application_status=application_status, starred=starred, sort=sort)


@router.get("/matching", response_model=JobPage)
def matching(_: AuthDep, query_service: Annotated[DashboardService, Depends(service)], page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)) -> JobPage:
    return query_service.matching_jobs(page=page, page_size=page_size)


@router.get("/jobs/{job_id}", response_model=JobRead)
def job(job_id: UUID, _: AuthDep, query_service: Annotated[DashboardService, Depends(service)]) -> JobRead:
    try: return query_service.job(job_id)
    except LookupError as error: raise HTTPException(404, str(error)) from error


@router.get("/analyses/{analysis_id}", response_model=AnalysisRead)
def analysis(analysis_id: UUID, _: AuthDep, query_service: Annotated[DashboardService, Depends(service)]) -> AnalysisRead:
    try: return query_service.analysis(analysis_id)
    except LookupError as error: raise HTTPException(404, str(error)) from error


@router.get("/matches/{match_id}", response_model=MatchRead)
def match(match_id: UUID, _: AuthDep, query_service: Annotated[DashboardService, Depends(service)]) -> MatchRead:
    try: return query_service.match(match_id)
    except LookupError as error: raise HTTPException(404, str(error)) from error


@router.get("/jobs/{job_id}/history", response_model=HistoryRead)
def history(job_id: UUID, _: AuthDep, query_service: Annotated[DashboardService, Depends(service)]) -> HistoryRead:
    return query_service.history(job_id)


@router.get("/runs", response_model=list[RunRead])
def runs(_: AuthDep, query_service: Annotated[DashboardService, Depends(service)], limit: int = Query(25, ge=1, le=100)) -> list[RunRead]:
    return query_service.runs(limit=limit)


@router.get("/runs/{run_id}/events", response_model=list[EventRead])
def run_events(run_id: UUID, _: AuthDep, query_service: Annotated[DashboardService, Depends(service)]) -> list[EventRead]:
    return query_service.run_events(run_id)


@router.get("/applications", response_model=list[ApplicationRead])
def applications(_: AuthDep, query_service: Annotated[DashboardService, Depends(service)], page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)) -> list[ApplicationRead]:
    return query_service.applications(page=page, page_size=page_size)


@router.get("/applications/{application_id}", response_model=ApplicationRead)
def application(application_id: UUID, _: AuthDep, query_service: Annotated[DashboardService, Depends(service)]) -> ApplicationRead:
    try:
        return query_service.application(application_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.get("/snapshot", response_model=DashboardSnapshot)
def snapshot(_: AuthDep, query_service: Annotated[DashboardService, Depends(service)]) -> DashboardSnapshot:
    return DashboardSnapshot(
        new_jobs=query_service.jobs(page=1, page_size=25, view="unseen"),
        matching_jobs=query_service.matching_jobs(page=1, page_size=25),
        runs=query_service.runs(limit=25),
        source_health=query_service.source_health(),
    )


@router.get("/source-health", response_model=list[SourceHealthRead])
def source_health(_: AuthDep, query_service: Annotated[DashboardService, Depends(service)]) -> list[SourceHealthRead]:
    return query_service.source_health()


@router.get("/source-health/summary", response_model=SourceHealthSummaryRead)
def source_health_summary(_: AuthDep, query_service: Annotated[DashboardService, Depends(service)]) -> SourceHealthSummaryRead:
    return query_service.source_health_summary()


class StarCommand(BaseModel):
    starred: bool


@router.patch("/jobs/{job_id}/star", response_model=JobRead)
def star_job(job_id: UUID, command: StarCommand, _: AuthDep, session: SessionDep, query_service: Annotated[DashboardService, Depends(service)]) -> JobRead:
    from backend.app.db.models import Job
    row = session.get(Job, job_id)
    if row is None:
        raise HTTPException(404, "job not found")
    row.starred = command.starred
    session.commit()
    session.refresh(row)
    return query_service.job(job_id)


@router.post("/jobs/{job_id}/seen", response_model=JobRead)
def mark_job_seen(
    job_id: UUID,
    _: AuthDep,
    session: SessionDep,
    query_service: Annotated[DashboardService, Depends(service)],
    x_actor_type: Annotated[str | None, Header()] = None,
) -> JobRead:
    if x_actor_type != "HUMAN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="human authorization required")
    from backend.app.db.models import Job
    row = session.get(Job, job_id)
    if row is None:
        raise HTTPException(404, "job not found")
    if row.seen_at is None:
        row.seen_at = datetime.now(timezone.utc)
        session.commit()
    return query_service.job(job_id)
