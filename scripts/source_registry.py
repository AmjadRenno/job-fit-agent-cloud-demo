"""Explicit allowlisted DailyRun source runner registry."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Callable

from backend.app.sources.phase1_trial import run_solita, run_vestas
from backend.app.workflow.sources import APPROVED_HANDLERS

SourceRunner = Callable[[Path], dict[str, Any]]

REGISTERED_SOURCE_RUNNERS: dict[str, SourceRunner] = {
    "solita_dk": run_solita,
    "vestas_dk": run_vestas,
}


class UnsupportedSourceRunnerError(RuntimeError):
    """Raised when an ACTIVE/enabled persisted source has no registered runner."""


def _generic_handler_runner(handler: Any) -> SourceRunner:
    def runner(output_dir: Path) -> dict[str, Any]:
        careers_url = getattr(handler.contract, "careers_url", None) if hasattr(handler, "contract") else ""
        jobs = handler.discover_jobs(careers_url)
        result = {"source": handler.source_id, "run_date": str(date.today()), "jobs": jobs}
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "jobs.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    return runner


def resolve_source_runner(source_id: str) -> SourceRunner | None:
    """Resolve known, explicitly registered or approved handler source runners."""
    if source_id in REGISTERED_SOURCE_RUNNERS:
        return REGISTERED_SOURCE_RUNNERS[source_id]
    for handler in APPROVED_HANDLERS:
        if getattr(handler, "source_id", None) == source_id:
            return _generic_handler_runner(handler)
    return None
