"""Bounded, deterministic-first agentic search over persisted read tools only."""
from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from backend.app.mode import agentic_search_enabled, require_agentic_search_mode
from backend.app.search import SearchJobsQuery

MAX_TOOL_CALLS = 4
MAX_RESULTS = 20
MAX_QUERY_LENGTH = 200
AGENTIC_TIMEOUT_SECONDS = 20.0


class AgenticPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interpreted_intent: str = Field(max_length=300)
    refined_query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    explanation: str = Field(max_length=400)
    job_ids: list[str] = Field(default_factory=list, max_length=MAX_RESULTS)


class AgenticPlanner(Protocol):
    model: str
    def plan(self, query: str) -> AgenticPlan: ...


class OpenAIAgenticPlanner:
    model = "gpt-4o-mini"

    def __init__(self) -> None:
        require_agentic_search_mode()
        from openai import OpenAI
        self.client = OpenAI(timeout=AGENTIC_TIMEOUT_SECONDS, max_retries=0)

    def plan(self, query: str) -> AgenticPlan:
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": "Refine a job search query only. Do not invent skills, job facts, or scores. Return concise structured data."},
                {"role": "user", "content": f"Search query: {query[:MAX_QUERY_LENGTH]}"},
            ],
            text_format=AgenticPlan,
        )
        if response.output_parsed is None:
            raise RuntimeError("AGENTIC_PROVIDER_INVALID_RESPONSE")
        return response.output_parsed


class ReadToolFacade(Protocol):
    def search_jobs(self, query: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class AgenticSearchResult:
    mode: str
    trigger_reason: str
    original_query: str
    refined_query: str | None
    interpreted_intent: str | None
    explanation: str | None
    tool_calls: list[dict[str, str]]
    items: list[dict[str, Any]]
    error_code: str | None
    trace: dict[str, Any]


class AgenticSearchService:
    """Never accesses the ORM: all final job data comes from the read facade."""
    def __init__(self, tools: ReadToolFacade, planner: AgenticPlanner | None = None) -> None:
        self.tools, self.planner = tools, planner

    def search(self, query: str) -> AgenticSearchResult:
        started = monotonic(); original = query.strip()[:MAX_QUERY_LENGTH]
        deterministic = self.tools.search_jobs(original)
        if not original or deterministic["total"]:
            return self._result("deterministic", "NOT_TRIGGERED", original, None, None, None, [], deterministic["items"], None, started)
        if not agentic_search_enabled() or self.planner is None:
            return self._result("deterministic", "ZERO_RESULTS", original, None, None, None, [], [], None, started)
        try:
            plan = self.planner.plan(original)
            # A plan has no authority over returned entities. Its optional IDs must
            # resolve inside the persisted refined-search result or are rejected.
            refined = self.tools.search_jobs(plan.refined_query)
            items = refined["items"][:MAX_RESULTS]
            resolved = {item["id"] for item in items}
            if plan.job_ids and not set(plan.job_ids).issubset(resolved):
                return self._result("agentic_fallback", "ZERO_RESULTS", original, plan.refined_query, plan.interpreted_intent, plan.explanation, [{"name": "search_jobs"}], [], "INVALID_AGENT_JOB_IDS", started)
            return self._result("agentic_fallback", "ZERO_RESULTS", original, plan.refined_query, plan.interpreted_intent, plan.explanation, [{"name": "search_jobs"}], items, None, started)
        except Exception:
            return self._result("deterministic", "ZERO_RESULTS", original, None, None, None, [], [], "AGENTIC_PROVIDER_UNAVAILABLE", started)

    def _result(self, mode: str, reason: str, original: str, refined: str | None, intent: str | None, explanation: str | None, calls: list[dict[str, str]], items: list[dict[str, Any]], error: str | None, started: float) -> AgenticSearchResult:
        return AgenticSearchResult(mode, reason, original, refined, intent, explanation, calls[:MAX_TOOL_CALLS], items[:MAX_RESULTS], error, {"fallback_triggered": mode == "agentic_fallback", "model": getattr(self.planner, "model", None), "tool_names": [call["name"] for call in calls[:MAX_TOOL_CALLS]], "tool_call_count": len(calls[:MAX_TOOL_CALLS]), "duration_ms": round((monotonic() - started) * 1000), "result_count": len(items[:MAX_RESULTS]), "error_code": error})
