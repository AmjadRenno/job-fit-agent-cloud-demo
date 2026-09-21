from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import UUID, uuid4

from dotenv import load_dotenv

# Allow both `python -m scripts.run_daily_search` and direct execution
# `python scripts/run_daily_search.py` from the repository root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def load_environment(root: Path = _REPO_ROOT) -> None:
    load_dotenv(root / ".env")


load_environment()

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.analysis.llm import OpenAIAnalyzer
from backend.app.db.import_json import SOURCE_DEFAULTS
from backend.app.db.models import AgentRun, ExecutionEvent, Job, JobSource
from backend.app.db.repository import ensure_source
from backend.app.matching.service import MATCHING_VERSION
from backend.app.trace_safety import sanitize_error_text, sanitize_job_url
from backend.app.workflow.processing import JobProcessingResult, JobProcessingService
from scripts.source_registry import UnsupportedSourceRunnerError, resolve_source_runner

ANALYSIS_VERSION = "phase3-analysis-v1"


class OverlappingRunError(RuntimeError):
    pass


class DailyRun:
    def __init__(
        self,
        session: Session,
        root: Path,
        analysis_factory: Callable[[], Any] | None = None,
        source_runners: dict[str, Callable[[Path], dict[str, Any]]] | None = None,
    ) -> None:
        self.session = session
        self.root = root
        self.analysis_factory = analysis_factory or OpenAIAnalyzer
        self.source_runners = source_runners
        self.run: AgentRun | None = None
        self.summary: dict[str, Any] = {
            "run_id": None,
            "started_at": None,
            "ended_at": None,
            "sources_attempted": [],
            "jobs_discovered": 0,
            "jobs_newly_persisted": 0,
            "analyses_performed": 0,
            "matches_performed": 0,
            "skipped_jobs": 0,
            "jobs_failed": 0,
            "failures": [],
            "retries": [],
            "status": "RUNNING",
        }

    def execute(self) -> dict[str, Any]:
        from backend.app.mode import require_mutable_mode
        require_mutable_mode()
        source_runners = self._resolve_source_runners()
        self._start_run(len(source_runners))
        try:
            for source_key, runner in source_runners.items():
                self._run_source(source_key, runner)
            statuses = [item["status"] for item in self.summary["sources_attempted"]]
            if statuses and all(status == "FAILED" for status in statuses):
                final_status = "FAILED"
            elif self.summary["failures"] or "BLOCKED" in statuses:
                final_status = "PARTIAL_SUCCESS"
            else:
                final_status = "SUCCESS"
            self._complete(final_status)
        except Exception as error:
            self._record_event("run_failed", status="FAILURE", error_code=type(error).__name__)
            self._complete("FAILED", error)
        finally:
            self._write_summary()
        return self.summary

    def _resolve_source_runners(self) -> dict[str, Callable[[Path], dict[str, Any]]]:
        if self.source_runners is not None:
            return self.source_runners

        sources = self.session.scalars(
            select(JobSource)
            .where(JobSource.lifecycle_status == "ACTIVE", JobSource.enabled.is_(True))
            .order_by(JobSource.source_id)
        ).all()
        runners: dict[str, Callable[[Path], dict[str, Any]]] = {}
        for source in sources:
            runner = resolve_source_runner(source.source_id)
            if runner is None:
                def unsupported(_: Path, source_id: str = source.source_id) -> dict[str, Any]:
                    raise UnsupportedSourceRunnerError(
                        f"no registered DailyRun runner for active source {source_id!r}"
                    )
                runners[source.source_id] = unsupported
            else:
                runners[source.source_id] = runner
        return runners

    def _start_run(self, sources_total: int) -> None:
        active = self.session.scalar(select(AgentRun).where(AgentRun.status == "RUNNING").limit(1))
        if active is not None:
            raise OverlappingRunError(f"daily run already active: {active.id}")
        now = datetime.now(timezone.utc)
        self.run = AgentRun(id=uuid4(), started_at=now, status="RUNNING", sources_total=sources_total)
        self.session.add(self.run)
        self.session.flush()
        self.summary["run_id"] = str(self.run.id)
        self.summary["started_at"] = now.isoformat()
        self._record_event("run_started")

    def _run_source(self, source_key: str, runner: Callable[[Path], dict[str, Any]]) -> None:
        source_summary = {"source": source_key, "status": "RUNNING", "jobs": 0}
        self.summary["sources_attempted"].append(source_summary)
        self._record_event("source_started", source_id=source_key)
        try:
            artifact_dir = self.root / "data" / "runs" / str(date.today()) / source_key.removesuffix("_dk")
            payload = self._run_source_with_retry(runner, artifact_dir, source_key)
            jobs = payload.get("jobs", [])
            source_summary["jobs"] = len(jobs)
            self.summary["jobs_discovered"] += len(jobs)
            if jobs:
                before = self.session.query(Job).count()
                ensure_source(self.session, source_id=source_key, **SOURCE_DEFAULTS[source_key])
                processor = JobProcessingService(
                    self.session,
                    analyzer_factory=self.analysis_factory,
                    profile_path=self.root / "data" / "candidate" / "profile.md",
                )
                for record in jobs:
                    try:
                        with self.session.begin_nested():
                            result = processor.process_record(
                                record,
                                run_id=self.run.id,
                                analysis_version=ANALYSIS_VERSION,
                                matching_version=MATCHING_VERSION,
                            )
                    except Exception as error:
                        self._record_job_failure(source_key, record, error)
                        continue
                    self._record_event("job_persisted", source_id=source_key, job_id=result.job.id)
                    if record.get("suspicious_content"):
                        self._record_event(
                            "job_suspicious_content",
                            source_id=source_key,
                            job_id=result.job.id,
                            status="WARNING",
                        )
                    self._record_processing_events(source_key, result)
                self.summary["jobs_newly_persisted"] += max(self.session.query(Job).count() - before, 0)
            if payload.get("blocker"):
                source_summary["status"] = "BLOCKED"
                source_summary["blocker"] = payload["blocker"]
                self._record_event("source_completed", source_id=source_key, status="SKIPPED")
                self._set_source_health(source_key, "BLOCKED")
            else:
                source_summary["status"] = "SUCCESS"
                self._record_event("source_completed", source_id=source_key)
                self._set_source_health(source_key, "HEALTHY", successful=True)
            self._update_source_counts()
        except Exception as error:
            source_summary["status"] = "FAILED"
            self.summary["failures"].append({"source": source_key, "code": type(error).__name__})
            self._record_event("source_failed", source_id=source_key, status="FAILURE", error_code=type(error).__name__)
            self._set_source_health(source_key, "UNHEALTHY")
            self._update_source_counts()

    def _set_source_health(self, source_key: str, health: str, *, successful: bool = False) -> None:
        source = self.session.scalar(select(JobSource).where(JobSource.source_id == source_key))
        if source is None:
            return
        source.health_status = health
        if successful:
            source.last_successful_run = datetime.now(timezone.utc)

    def _update_source_counts(self) -> None:
        if self.run is None:
            return
        statuses = [item["status"] for item in self.summary["sources_attempted"]]
        self.run.sources_success = statuses.count("SUCCESS")
        self.run.sources_partial = statuses.count("BLOCKED")
        self.run.sources_failed = statuses.count("FAILED")

    def _run_source_with_retry(
        self,
        runner: Callable[[Path], dict[str, Any]],
        artifact_dir: Path,
        source_key: str,
    ) -> dict[str, Any]:
        for attempt in range(2):
            try:
                return runner(artifact_dir)
            except (TimeoutError, ConnectionError) as error:
                if attempt == 1:
                    raise
                self.summary["retries"].append({"source": source_key, "error": type(error).__name__})
        raise RuntimeError("source retry loop did not return")

    def _record_processing_events(self, source_key: str, result: JobProcessingResult) -> None:
        if result.job.availability_status != "ACTIVE":
            self.summary["skipped_jobs"] += 1
            return
        if result.analysis_performed:
            self._record_event("analysis_started", source_id=source_key, job_id=result.job.id)
            self.summary["analyses_performed"] += 1
            self._record_event("analysis_completed", source_id=source_key, job_id=result.job.id)
        else:
            self.summary["skipped_jobs"] += 1
        if result.match_performed:
            self._record_event("matching_started", source_id=source_key, job_id=result.job.id)
            self.summary["matches_performed"] += 1
            self._record_event("matching_completed", source_id=source_key, job_id=result.job.id)
        else:
            self.summary["skipped_jobs"] += 1

    def _complete(self, status: str, error: Exception | None = None) -> None:
        if self.run is None:
            return
        self.run.status = status
        self.run.ended_at = datetime.now(timezone.utc)
        self.run.jobs_discovered = self.summary["jobs_discovered"]
        self.run.jobs_new = self.summary["jobs_newly_persisted"]
        self.run.jobs_analyzed = self.summary["analyses_performed"]
        self.run.jobs_matched = self.summary["matches_performed"]
        if error:
            self.run.error_summary = type(error).__name__
        self.summary["status"] = status
        self.summary["ended_at"] = self.run.ended_at.isoformat()
        self._record_event("run_completed" if status != "FAILED" else "run_failed", status="SUCCESS" if status != "FAILED" else "FAILURE")
        self.session.commit()

    def _record_job_failure(self, source_key: str, record: dict[str, Any], error: Exception) -> None:
        job_url = sanitize_job_url(str(record.get("url") or "unknown"))
        self.summary["failures"].append({"source": source_key, "job_url": job_url, "code": type(error).__name__})
        self.summary["jobs_failed"] += 1
        self._record_event(
            "job_failed",
            status="FAILURE",
            source_id=source_key,
            error_code=type(error).__name__,
            error_message=sanitize_error_text(str(error))[:200],
            metadata_json={"job_url": job_url},
        )

    def _record_event(
        self,
        event_type: str,
        status: str = "SUCCESS",
        source_id: str | None = None,
        job_id: UUID | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> None:
        if self.run is None:
            return
        metadata = metadata_json
        if metadata and "job_url" in metadata:
            metadata = {**metadata, "job_url": sanitize_job_url(str(metadata["job_url"]))}
        self.session.add(
            ExecutionEvent(
                run_id=self.run.id,
                source_id=source_id,
                job_id=job_id,
                event_type=event_type,
                status=status,
                error_code=error_code,
                error_message=sanitize_error_text(error_message),
                metadata_json=metadata,
            )
        )
        self.session.flush()

    def _write_summary(self) -> None:
        path = self.root / "data" / "runs" / str(date.today()) / "daily-summary.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.summary, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the approved daily job-search orchestration.")
    parser.parse_args()
    from backend.app.db.session import create_session_factory
    try:
        with create_session_factory()() as session:
            summary = DailyRun(session, _REPO_ROOT).execute()
    except OverlappingRunError as error:
        print(json.dumps({"status": "REFUSED", "reason": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] in {"SUCCESS", "PARTIAL_SUCCESS"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
