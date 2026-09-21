from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from backend.app.dashboard.schemas import AnalysisRead, JobRead, MatchRead


class WorkflowAnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)
    html_content: str | None = None


class WorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: JobRead
    analysis: AnalysisRead
    match: MatchRead


class WorkflowDiscoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    careers_url: str = Field(min_length=1)


class WorkflowDiscoveryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: JobRead
    analysis: AnalysisRead | None = None
    match: MatchRead | None = None

    def __getitem__(self, key: str):
        return self.model_dump()[key]
