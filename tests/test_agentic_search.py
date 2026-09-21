from __future__ import annotations

from backend.app.agentic_search import AgenticPlan, AgenticSearchService, MAX_TOOL_CALLS


class Tools:
    def __init__(self, mapping): self.mapping, self.calls = mapping, []
    def search_jobs(self, query): self.calls.append(query); return self.mapping.get(query, {"items": [], "total": 0})


class Planner:
    model = "fake"
    def __init__(self, plan): self.plan_value, self.calls = plan, 0
    def plan(self, query): self.calls += 1; return self.plan_value


def enabled(monkeypatch):
    monkeypatch.setenv("APP_MODE", "demo"); monkeypatch.setenv("ENABLE_AGENTIC_SEARCH", "true"); monkeypatch.setenv("OPENAI_API_KEY", "fake")


def test_successful_or_empty_search_never_calls_agent(monkeypatch):
    enabled(monkeypatch); planner = Planner(AgenticPlan(interpreted_intent="x", refined_query="refined", explanation="x")); tools = Tools({"found": {"items": [{"id": "1"}], "total": 1}, "": {"items": [], "total": 0}})
    assert AgenticSearchService(tools, planner).search("found").mode == "deterministic"
    assert AgenticSearchService(tools, planner).search("").mode == "deterministic"
    assert planner.calls == 0


def test_zero_result_disabled_never_calls_agent(monkeypatch):
    monkeypatch.setenv("ENABLE_AGENTIC_SEARCH", "false"); tools = Tools({}); planner = Planner(AgenticPlan(interpreted_intent="x", refined_query="refined", explanation="x"))
    result = AgenticSearchService(tools, planner).search("missing")
    assert result.trigger_reason == "ZERO_RESULTS" and result.mode == "deterministic" and planner.calls == 0


def test_enabled_agent_returns_only_persisted_result_ids(monkeypatch):
    enabled(monkeypatch); tools = Tools({"missing": {"items": [], "total": 0}, "dotnet": {"items": [{"id": "job-1", "match_score": 88, "recommendation": "APPLY"}], "total": 1}}); planner = Planner(AgenticPlan(interpreted_intent=".NET", refined_query="dotnet", explanation="refined", job_ids=["job-1"]))
    result = AgenticSearchService(tools, planner).search("missing")
    assert result.mode == "agentic_fallback" and result.items[0]["match_score"] == 88
    assert result.trace["tool_call_count"] <= MAX_TOOL_CALLS and tools.calls == ["missing", "dotnet"]


def test_hallucinated_job_id_is_rejected_and_provider_failure_is_safe(monkeypatch):
    enabled(monkeypatch); tools = Tools({"missing": {"items": [], "total": 0}, "dotnet": {"items": [{"id": "real"}], "total": 1}})
    invalid = AgenticSearchService(tools, Planner(AgenticPlan(interpreted_intent="x", refined_query="dotnet", explanation="x", job_ids=["fake"]))).search("missing")
    assert invalid.error_code == "INVALID_AGENT_JOB_IDS" and not invalid.items
    class Broken:
        model = "fake"
        def plan(self, query): raise TimeoutError()
    assert AgenticSearchService(tools, Broken()).search("missing").error_code == "AGENTIC_PROVIDER_UNAVAILABLE"
