from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.dashboard.api import authorize_dashboard, get_session
from backend.app.sources.onboarding import OnboardingError

from .schemas import (
    CompanyCreate,
    CompanyPatch,
    CompanyRead,
    OnboardingCommand,
    PreviewRead,
    SourceApprovalCommand,
    SourceCreate,
    SourcePatch,
    SourceRead,
)
from .service import SourceManagementError, SourceManagementService

router = APIRouter(prefix="/api/sources", tags=["sources"])
SessionDep = Annotated[Session, Depends(get_session)]
AuthDep = Annotated[None, Depends(authorize_dashboard)]


def human_command(x_actor_type: Annotated[str | None, Header()] = None) -> None:
    if x_actor_type != "HUMAN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="human authorization required")


HumanDep = Annotated[None, Depends(human_command)]


def service(session: SessionDep) -> SourceManagementService:
    return SourceManagementService(session)


def handle_error(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, SourceManagementError):
        return HTTPException(status_code=error.status_code, detail=str(error))
    if isinstance(error, OnboardingError):
        if error.code in {"INVALID_STATE_TRANSITION", "REVALIDATION_REQUIRED"}:
            return HTTPException(status_code=409, detail=str(error))
        return HTTPException(status_code=422, detail=str(error))
    return HTTPException(status_code=500, detail="source management operation failed")


@router.post("/companies", response_model=CompanyRead, status_code=201)
def create_company(command: CompanyCreate, _: AuthDep, __: HumanDep, source_service: Annotated[SourceManagementService, Depends(service)]) -> CompanyRead:
    try:
        return source_service.create_company(command)
    except Exception as error:
        raise handle_error(error) from error


@router.get("/companies", response_model=list[CompanyRead])
def list_companies(_: AuthDep, source_service: Annotated[SourceManagementService, Depends(service)]) -> list[CompanyRead]:
    return source_service.list_companies()


@router.get("/companies/{company_id}", response_model=CompanyRead)
def get_company(company_id: UUID, _: AuthDep, source_service: Annotated[SourceManagementService, Depends(service)]) -> CompanyRead:
    try:
        return source_service.get_company(company_id)
    except Exception as error:
        raise handle_error(error) from error


@router.patch("/companies/{company_id}", response_model=CompanyRead)
def patch_company(company_id: UUID, command: CompanyPatch, _: AuthDep, __: HumanDep, source_service: Annotated[SourceManagementService, Depends(service)]) -> CompanyRead:
    try:
        return source_service.update_company(company_id, command)
    except Exception as error:
        raise handle_error(error) from error


@router.delete("/companies/{company_id}", response_model=CompanyRead)
def archive_company(company_id: UUID, _: AuthDep, __: HumanDep, source_service: Annotated[SourceManagementService, Depends(service)]) -> CompanyRead:
    try:
        return source_service.archive_company(company_id)
    except Exception as error:
        raise handle_error(error) from error


@router.post("/companies/{company_id}/sources", response_model=SourceRead, status_code=201)
def create_source(company_id: UUID, command: SourceCreate, _: AuthDep, __: HumanDep, source_service: Annotated[SourceManagementService, Depends(service)]) -> SourceRead:
    try:
        return source_service.create_source(company_id, command)
    except Exception as error:
        raise handle_error(error) from error


@router.get("/{source_id}", response_model=SourceRead)
def get_source(source_id: UUID, _: AuthDep, source_service: Annotated[SourceManagementService, Depends(service)]) -> SourceRead:
    try:
        return source_service.get_source(source_id)
    except Exception as error:
        raise handle_error(error) from error


@router.patch("/{source_id}", response_model=SourceRead)
def patch_source(source_id: UUID, command: SourcePatch, _: AuthDep, __: HumanDep, source_service: Annotated[SourceManagementService, Depends(service)]) -> SourceRead:
    try:
        return source_service.update_source(source_id, command)
    except Exception as error:
        raise handle_error(error) from error


@router.delete("/{source_id}", response_model=SourceRead)
def archive_source(source_id: UUID, _: AuthDep, __: HumanDep, source_service: Annotated[SourceManagementService, Depends(service)]) -> SourceRead:
    try:
        return source_service.archive_source(source_id)
    except Exception as error:
        raise handle_error(error) from error


@router.post("/{source_id}/onboarding", response_model=PreviewRead, status_code=201)
def onboard_source(source_id: UUID, command: OnboardingCommand, _: AuthDep, __: HumanDep, source_service: Annotated[SourceManagementService, Depends(service)]) -> PreviewRead:
    try:
        return source_service.onboard(source_id, str(command.url) if command.url else None)
    except Exception as error:
        raise handle_error(error) from error


@router.get("/{source_id}/previews", response_model=list[PreviewRead])
def list_previews(source_id: UUID, _: AuthDep, source_service: Annotated[SourceManagementService, Depends(service)]) -> list[PreviewRead]:
    try:
        return source_service.list_previews(source_id)
    except Exception as error:
        raise handle_error(error) from error


@router.get("/{source_id}/previews/{preview_id}", response_model=PreviewRead)
def get_preview(source_id: UUID, preview_id: UUID, _: AuthDep, source_service: Annotated[SourceManagementService, Depends(service)]) -> PreviewRead:
    try:
        return source_service.get_preview(source_id, preview_id)
    except Exception as error:
        raise handle_error(error) from error


@router.post("/{source_id}/revalidate", response_model=PreviewRead, status_code=201)
def revalidate_source(source_id: UUID, _: AuthDep, __: HumanDep, source_service: Annotated[SourceManagementService, Depends(service)]) -> PreviewRead:
    try:
        return source_service.revalidate(source_id)
    except Exception as error:
        raise handle_error(error) from error


@router.post("/{source_id}/approve", response_model=SourceRead)
def approve_source(
    source_id: UUID,
    command: SourceApprovalCommand,
    _: AuthDep,
    __: HumanDep,
    source_service: Annotated[SourceManagementService, Depends(service)],
) -> SourceRead:
    try:
        return source_service.approve(source_id, command.preview_version)
    except Exception as error:
        raise handle_error(error) from error
