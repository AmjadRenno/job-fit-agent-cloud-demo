from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.dashboard.api import authorize_dashboard, get_session

from .service import ApplicationAuthorizationError, ApplicationService, CoverLetterAssociationError

router = APIRouter(prefix="/api/applications", tags=["applications"])
SessionDep = Annotated[Session, Depends(get_session)]
AuthDep = Annotated[None, Depends(authorize_dashboard)]


class ApplicationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_state: str = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=2000)


class CoverLetterAssociationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cover_letter_id: UUID


class ApplicationResponse(BaseModel):
    id: UUID
    job_id: UUID
    status: str
    notes: str | None
    applied_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApplicationEventResponse(BaseModel):
    id: UUID
    application_id: UUID
    from_status: str | None
    to_status: str
    triggered_by: str
    notes: str | None
    created_at: datetime


def application_service(session: SessionDep) -> ApplicationService:
    return ApplicationService(session)


def human_command(x_actor_type: Annotated[str | None, Header()] = None) -> None:
    if x_actor_type != "HUMAN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="human authorization required")


HumanDep = Annotated[None, Depends(human_command)]


@router.post("/jobs/{job_id}", response_model=ApplicationResponse, status_code=201)
def create_application(job_id: UUID, _: AuthDep, __: HumanDep, service: Annotated[ApplicationService, Depends(application_service)], command: ApplicationCommand) -> ApplicationResponse:
    try:
        if command.target_state != "INTERESTING":
            raise HTTPException(status_code=409, detail="application creation must start at INTERESTING")
        row = service.create_for_human(job_id, human_authorized=True, notes=command.notes)
        return ApplicationResponse.model_validate(row, from_attributes=True)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error


@router.post("/{application_id}/transition", response_model=ApplicationResponse)
def transition_application(application_id: UUID, _: AuthDep, __: HumanDep, service: Annotated[ApplicationService, Depends(application_service)], command: ApplicationCommand) -> ApplicationResponse:
    try:
        row = service.transition(application_id, command.target_state, human_authorized=True, notes=command.notes)
        return ApplicationResponse.model_validate(row, from_attributes=True)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ApplicationAuthorizationError as error:
        raise HTTPException(403, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.post("/{application_id}/cover-letter", response_model=ApplicationResponse)
def associate_cover_letter(application_id: UUID, _: AuthDep, __: HumanDep, service: Annotated[ApplicationService, Depends(application_service)], command: CoverLetterAssociationCommand) -> ApplicationResponse:
    try:
        row = service.attach_cover_letter(application_id, command.cover_letter_id, human_authorized=True)
        return ApplicationResponse.model_validate(row, from_attributes=True)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ApplicationAuthorizationError as error:
        raise HTTPException(403, str(error)) from error
    except CoverLetterAssociationError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/{application_id}/history", response_model=list[ApplicationEventResponse])
def application_history(application_id: UUID, _: AuthDep, service: Annotated[ApplicationService, Depends(application_service)]) -> list[ApplicationEventResponse]:
    return [ApplicationEventResponse.model_validate(event, from_attributes=True) for event in service.history(application_id)]
