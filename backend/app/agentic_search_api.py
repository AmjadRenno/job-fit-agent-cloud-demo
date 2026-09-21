from __future__ import annotations

from typing import Any
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from backend.app.agentic_search import AgenticSearchService, OpenAIAgenticPlanner
from backend.app.dashboard.api import AuthDep, SessionDep
from backend.app.mcp import tools
from backend.app.mode import agentic_search_enabled

router = APIRouter(prefix="/api/search", tags=["search"])

class AgenticSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)

class AgenticSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str; trigger_reason: str; original_query: str; refined_query: str | None = None
    interpreted_intent: str | None = None; explanation: str | None = None
    tool_calls: list[dict[str, str]]; items: list[dict[str, Any]]; error_code: str | None = None; trace: dict[str, Any]

class SessionReadTools:
    def __init__(self, session): self.session = session
    def search_jobs(self, query: str) -> dict[str, Any]: return tools.search_jobs(self.session, query=query)

@router.get("/agentic/status")
def agentic_status(_: AuthDep) -> dict[str, bool]:
    return {"enabled": agentic_search_enabled()}

@router.post("/agentic", response_model=AgenticSearchResponse)
def agentic_search(_: AuthDep, session: SessionDep, body: AgenticSearchRequest):
    planner = OpenAIAgenticPlanner() if agentic_search_enabled() else None
    return AgenticSearchService(SessionReadTools(session), planner).search(body.query).__dict__
