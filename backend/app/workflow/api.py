from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.analysis.context import AnalysisPreconditionError
from backend.app.dashboard.api import authorize_dashboard, get_session
from backend.app.matching.service import MatchingPreconditionError

from .schemas import WorkflowAnalyzeRequest, WorkflowDiscoverRequest, WorkflowDiscoveryItem, WorkflowResult
from .service import (
    ExtractionError,
    JobFitWorkflowService,
    UnsupportedSourceError,
)

router = APIRouter(prefix="/api/workflow", tags=["workflow"])
SessionDep = Annotated[Session, Depends(get_session)]
AuthDep = Annotated[None, Depends(authorize_dashboard)]


def human_command(x_actor_type: Annotated[str | None, Header()] = None) -> None:
    if x_actor_type != "HUMAN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="human authorization required",
        )


HumanDep = Annotated[None, Depends(human_command)]


def workflow_service(session: SessionDep) -> JobFitWorkflowService:
    return JobFitWorkflowService(session)


@router.post("/analyze", response_model=WorkflowResult, status_code=200)
def analyze_job_url(
    command: WorkflowAnalyzeRequest,
    _: AuthDep,
    __: HumanDep,
    service: Annotated[JobFitWorkflowService, Depends(workflow_service)],
) -> WorkflowResult:
    try:
        return service.process_url(command.url, html_content=command.html_content)
    except UnsupportedSourceError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ExtractionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (AnalysisPreconditionError, MatchingPreconditionError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/discover", response_model=list[WorkflowDiscoveryItem], status_code=200)
def discover_jobs(
    command: WorkflowDiscoverRequest,
    _: AuthDep,
    __: HumanDep,
    service: Annotated[JobFitWorkflowService, Depends(workflow_service)],
) -> list[WorkflowDiscoveryItem]:
    try:
        return service.discover_and_match(command.careers_url)
    except UnsupportedSourceError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except ExtractionError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (AnalysisPreconditionError, MatchingPreconditionError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
