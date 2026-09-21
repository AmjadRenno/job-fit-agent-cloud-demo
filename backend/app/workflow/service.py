from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import select

from backend.app.analysis.llm import StructuredAnalyzer
from backend.app.dashboard.schemas import AnalysisRead, MatchRead
from backend.app.dashboard.service import DashboardService
from backend.app.db.import_json import SOURCE_DEFAULTS
from backend.app.db.models import Job, JobSource
from backend.app.db.repository import ensure_source
from backend.app.trace_safety import sanitize_error_text, strip_query_string

from .processing import JobProcessingService
from .schemas import WorkflowDiscoveryItem, WorkflowResult
from .sources import resolve_source_handler

DEFAULT_PROFILE_PATH = Path("data/candidate/profile.md")


class WorkflowError(Exception):
    pass


class UnsupportedSourceError(WorkflowError):
    pass


class ExtractionError(WorkflowError):
    pass


class JobFitWorkflowService:
    def __init__(
        self,
        session: Session,
        analyzer: StructuredAnalyzer | None = None,
        profile_path: Path | None = None,
        transport: Any | None = None,
    ) -> None:
        self.session = session
        self.analyzer = analyzer
        self.profile_path = profile_path or DEFAULT_PROFILE_PATH
        self.transport = transport
        # Additive per-batch capture of per-item discovery failures (Phase 11).
        self.discovery_failures: list[dict[str, Any]] = []

    def _require_trifork_approval(self, source_id: str) -> None:
        # A newly supported handler is previewable before its persisted source
        # is approved. Keep the direct discovery endpoint behind that boundary.
        if source_id != "trifork_dk":
            return
        source = self.session.scalar(select(JobSource).where(JobSource.source_id == source_id))
        if source is None or not source.enabled or source.lifecycle_status != "ACTIVE":
            raise UnsupportedSourceError("Trifork requires a source preview and human approval before discovery.")

    def _persist_and_evaluate_job(
        self,
        record: dict[str, Any],
        *,
        run_id: UUID | None = None,
        allow_reanalysis: bool = True,
    ) -> tuple[Job, Any | None, Any | None]:
        result = JobProcessingService(
            self.session,
            analyzer=self.analyzer,
            profile_path=self.profile_path,
        ).process_record(record, run_id=run_id, allow_reanalysis=allow_reanalysis)
        return result.job, result.analysis, result.match

    def process_url(
        self,
        url: str,
        *,
        html_content: str | None = None,
        run_id: UUID | None = None,
    ) -> WorkflowResult:
        handler = resolve_source_handler(url)
        if handler is None:
            raise UnsupportedSourceError(
                f"URL is not from an approved job source: {url}"
            )
        self._require_trifork_approval(handler.source_id)

        defaults = SOURCE_DEFAULTS.get(handler.source_id)
        if defaults:
            ensure_source(self.session, source_id=handler.source_id, **defaults)
            self.session.flush()

        try:
            record = handler.extract_record(
                url,
                html_content=html_content,
                transport=self.transport,
            )
        except Exception as error:
            raise ExtractionError(
                f"Failed to extract job from {strip_query_string(url)}: {sanitize_error_text(str(error))}"
            ) from error

        job, analysis_row, match_row = self._persist_and_evaluate_job(record, run_id=run_id)
        result = WorkflowResult(
            job=DashboardService(self.session)._job(job),
            analysis=AnalysisRead.model_validate(analysis_row, from_attributes=True),
            match=MatchRead.model_validate(match_row, from_attributes=True),
        )

        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

        return result

    def discover_and_match(self, careers_url: str) -> list[WorkflowDiscoveryItem]:
        handler = resolve_source_handler(careers_url)
        if handler is None:
            raise UnsupportedSourceError(f"URL is not from an approved job source: {careers_url}")
        self._require_trifork_approval(handler.source_id)

        defaults = SOURCE_DEFAULTS.get(handler.source_id)
        if defaults:
            ensure_source(self.session, source_id=handler.source_id, **defaults)
            self.session.flush()

        discovery = getattr(handler, "discover_jobs", None)
        if discovery is None:
            raise ExtractionError(f"Discovery is not supported for {handler.source_id}")

        jobs = discovery(careers_url, transport=self.transport)
        self.discovery_failures = []
        ranked: list[WorkflowDiscoveryItem] = []
        for item in jobs:
            record = item
            if not isinstance(item, dict):
                self.discovery_failures.append({
                    "url": "unknown",
                    "code": "INVALID_ITEM",
                    "error": "discovery returned a non-dictionary item",
                })
                continue
            if record.get("source") is None:
                record = {**record, "source": handler.source_id}
            try:
                with self.session.begin_nested():
                    job, analysis_row, match_row = self._persist_and_evaluate_job(record)
            except Exception as error:
                # A single failing job must not abort the batch: capture the
                # failure additively and continue with the remaining jobs.
                self.discovery_failures.append({
                    "url": strip_query_string(str(record.get("url") or "unknown")),
                    "code": type(error).__name__,
                    "error": sanitize_error_text(str(error)),
                })
                continue

            ranked.append(
                WorkflowDiscoveryItem(
                    job=DashboardService(self.session)._job(job),
                    analysis=AnalysisRead.model_validate(analysis_row, from_attributes=True) if analysis_row else None,
                    match=MatchRead.model_validate(match_row, from_attributes=True) if match_row else None,
                )
            )

        ranked.sort(key=lambda entry: (entry.match.overall_score if entry.match else -1, entry.job.title.lower()), reverse=True)
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return ranked
