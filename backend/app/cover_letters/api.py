from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.dashboard.api import authorize_dashboard, get_session

from .llm import OpenAICoverLetterGenerator
from .service import CoverLetterAuthorizationError, CoverLetterPreconditionError, CoverLetterService

router = APIRouter(prefix="/api/cover-letters", tags=["cover-letters"])
SessionDep = Annotated[Session, Depends(get_session)]
AuthDep = Annotated[None, Depends(authorize_dashboard)]


class CoverLetterCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    match_id: UUID
    profile_path: str = Field(default="data/candidate/profile.md", pattern=r"^data/candidate/profile\.md$")


class CoverLetterReviewCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    human_reviewed: bool
    human_notes: str | None = Field(default=None, max_length=2000)


class CoverLetterRead(BaseModel):
    id: UUID
    job_id: UUID
    application_id: UUID | None
    match_result_id: UUID | None
    content: str
    model: str
    prompt_version: str
    grounding_valid: bool | None
    grounding_notes: str | None
    human_reviewed: bool
    human_notes: str | None
    created_at: datetime


def service(session: SessionDep) -> CoverLetterService:
    return CoverLetterService(session, OpenAICoverLetterGenerator())


def human_command(x_actor_type: Annotated[str | None, Header()] = None) -> None:
    if x_actor_type != "HUMAN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="human authorization required")


HumanDep = Annotated[None, Depends(human_command)]


@router.post("/generate", response_model=CoverLetterRead, status_code=201)
def generate(_: AuthDep, __: HumanDep, command: CoverLetterCommand, letter_service: Annotated[CoverLetterService, Depends(service)]) -> CoverLetterRead:
    try:
        draft = letter_service.generate_for_human(
            job_id=command.job_id,
            match_id=command.match_id,
            profile_path=__import__("pathlib").Path(command.profile_path),
            human_authorized=True,
        )
        from sqlalchemy import select
        from backend.app.db.models import CoverLetter
        row = letter_service.session.scalar(select(CoverLetter).where(CoverLetter.job_id == command.job_id, CoverLetter.match_result_id == command.match_id).order_by(CoverLetter.created_at.desc()))
        return CoverLetterRead.model_validate(row, from_attributes=True)
    except (CoverLetterAuthorizationError, PermissionError) as error:
        raise HTTPException(403, str(error)) from error
    except CoverLetterPreconditionError as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/{letter_id}/review", response_model=CoverLetterRead)
def review_letter(letter_id: UUID, _: AuthDep, __: HumanDep, letter_service: Annotated[CoverLetterService, Depends(service)], command: CoverLetterReviewCommand) -> CoverLetterRead:
    try:
        row = letter_service.review(letter_id, human_authorized=True, human_reviewed=command.human_reviewed, human_notes=command.human_notes)
        return CoverLetterRead.model_validate(row, from_attributes=True)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except (CoverLetterAuthorizationError, PermissionError) as error:
        raise HTTPException(403, str(error)) from error


@router.get("/jobs/{job_id}", response_model=list[CoverLetterRead])
def list_job_letters(job_id: UUID, _: AuthDep, session: SessionDep) -> list[CoverLetterRead]:
    from sqlalchemy import select
    from backend.app.db.models import CoverLetter
    rows = session.scalars(select(CoverLetter).where(CoverLetter.job_id == job_id).order_by(CoverLetter.created_at.desc())).all()
    return [CoverLetterRead.model_validate(row, from_attributes=True) for row in rows]
