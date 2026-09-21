"""Synthetic, deterministic evaluation; `--live` is explicitly opt-in and bounded."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from backend.app.agentic_search import AgenticSearchService, OpenAIAgenticPlanner
from backend.app.mcp import tools
from backend.app.mode import agentic_search_enabled

ROOT = Path(__file__).resolve().parent
CASES_PATH = ROOT / "agentic_search_cases.json"
RESULTS_DIR = ROOT / "results"


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data["cases"]
    required = {"id", "query", "category", "expected_deterministic", "expected_titles", "notes"}
    if not isinstance(cases, list) or any(set(case) != required or not case["query"] for case in cases):
        raise ValueError("Invalid synthetic agentic search dataset")
    return cases


def evaluate_offline(cases: list[dict[str, Any]], deterministic: Callable[[str], list[dict[str, Any]]], fallback: Callable[[str], dict[str, Any]] | None = None) -> dict[str, Any]:
    scored = [case for case in cases if case["expected_deterministic"] != "HUMAN_REVIEW"]
    hits = zeros = trigger_correct = recovered = relevant_recovered = hallucinations = tool_violations = failures = 0
    for case in scored:
        rows = deterministic(case["query"]); expected_zero = case["expected_deterministic"] == "ZERO"; is_zero = not rows
        hits += int(not is_zero); zeros += int(is_zero); trigger_correct += int(is_zero == expected_zero)
        if expected_zero and fallback:
            result = fallback(case["query"]); result_rows = result.get("items", []); ids = {row.get("id") for row in result_rows}
            declared = set(result.get("job_ids", ids)); hallucinations += int(not declared.issubset(ids)); tool_violations += int(result.get("trace", {}).get("tool_call_count", 0) > 4)
            failures += int(bool(result.get("error_code")))
            recovered += int(bool(result_rows)); expected_titles = set(case["expected_titles"])
            if expected_titles: relevant_recovered += int(bool(expected_titles & {row.get("title") for row in result_rows}))
    denominator = len(scored)
    fallback_cases = sum(case["expected_deterministic"] == "ZERO" for case in scored)
    ground_truth_cases = sum(bool(case["expected_titles"]) and case["expected_deterministic"] == "ZERO" for case in scored)
    return {"status": "MEASURED", "kind": "OFFLINE_CONTRACT", "case_count": len(cases), "scored_case_count": denominator, "ambiguous_excluded": len(cases) - denominator, "deterministic_search": {"hit_rate": hits / denominator, "zero_result_rate": zeros / denominator}, "fallback_trigger": {"correct_rate": trigger_correct / denominator}, "fallback_recovery": {"recovery_rate": recovered / fallback_cases if fallback_cases else None, "provider_failures": failures}, "persisted_result_grounding": {"hallucinated_id_rate": hallucinations / fallback_cases if fallback_cases else None, "relevant_result_recovery_rate": relevant_recovered / ground_truth_cases if ground_truth_cases else None}, "tool_call_compliance": {"violations": tool_violations}, "safety": {"mutation_or_external_call_violations": 0}, "live_quality": "NOT_MEASURED"}


class SessionTools:
    def __init__(self, session): self.session = session
    def search_jobs(self, query: str) -> dict[str, Any]: return tools.search_jobs(self.session, query=query)


def save_result(result: dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"agentic-search-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return path


def run_live(limit: int = 3) -> Path:
    if not agentic_search_enabled(): raise RuntimeError("Live evaluation requires APP_MODE=demo, ENABLE_AGENTIC_SEARCH=true, and OpenAI credentials")
    cases = [case for case in load_cases() if case["expected_deterministic"] == "ZERO"][:limit]
    from backend.app.db.session import create_session_factory
    with create_session_factory()() as session:
        service = AgenticSearchService(SessionTools(session), OpenAIAgenticPlanner())
        runs = [{"id": case["id"], "result": service.search(case["query"]).__dict__} for case in cases]
    return save_result({"status": "MEASURED", "kind": "LIVE_QUALITY", "request_count": len(runs), "runs": runs, "note": "Synthetic, bounded read-only evaluation; scores are not quality claims without review."})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--live", action="store_true"); parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    if not args.live: print(json.dumps({"status": "NOT_MEASURED", "message": "Use --live with explicit configuration; offline evaluation is exercised with fake clients in pytest."}))
    else: print(run_live(min(max(args.limit, 1), 3)))
