from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.analysis.llm import OpenAIAnalyzer, StructuredAnalyzer
from backend.app.analysis.service import JobAnalysisService
from backend.app.applications.service import has_applied_application
from backend.app.db.models import CandidateProfile, Job, JobAnalysis as JobAnalysisRow, MatchResult as MatchResultRow
from backend.app.db.repository import JobRepository
from backend.app.matching.service import MATCHING_VERSION, JobMatchingService


@dataclass(frozen=True)
class JobProcessingResult:
    job: Job
    analysis: JobAnalysisRow | None
    match: MatchResultRow | None
    analysis_performed: bool
    match_performed: bool


class JobProcessingService:
    def __init__(
        self,
        session: Session,
        *,
        analyzer: StructuredAnalyzer | None = None,
        analyzer_factory: Callable[[], StructuredAnalyzer] | None = None,
        profile_path: Path,
    ) -> None:
        self.session = session
        self.analyzer = analyzer
        self.analyzer_factory = analyzer_factory
        self.profile_path = profile_path

    def process_record(
        self,
        record: Mapping[str, Any],
        *,
        run_id: UUID | None = None,
        allow_reanalysis: bool = True,
        analysis_version: str | None = None,
        matching_version: str | None = None,
    ) -> JobProcessingResult:
        job = JobRepository(self.session).upsert_job(record)
        self.session.flush()
        return self.process_job(
            job,
            run_id=run_id,
            allow_reanalysis=allow_reanalysis,
            analysis_version=analysis_version,
            matching_version=matching_version,
        )

    def process_job(
        self,
        job: Job,
        *,
        run_id: UUID | None = None,
        allow_reanalysis: bool = True,
        analysis_version: str | None = None,
        matching_version: str | None = None,
    ) -> JobProcessingResult:
        analysis_row, match_row = self._latest_rows(job)
        if not allow_reanalysis or job.availability_status != "ACTIVE":
            return JobProcessingResult(job, analysis_row, match_row, False, False)

        # Persistent analysis guard (Phase 12 fix #6): a COMPLETED analysis for
        # the current content hash is never repeated. A content change changes
        # the hash and legitimately triggers re-analysis.
        has_valid_analysis = job.analysis_status == "COMPLETED" and self._analysis_matches_current_hash(
            job, analysis_row, analysis_version
        )
        has_valid_match = self._match_matches_current_hash(job, match_row, matching_version or MATCHING_VERSION)
        analysis_performed = False
        match_performed = False

        if not has_valid_analysis:
            analyzer = self.analyzer or (self.analyzer_factory or OpenAIAnalyzer)()
            JobAnalysisService(self.session, analyzer).analyze_job(
                job.id,
                self.profile_path,
                run_id=run_id,
            )
            analysis_performed = True

        # Applied-job exclusion (Phase 12 fix #12): a job the human has already
        # applied for (or any later application state) is never re-matched or
        # re-recommended. Merely tracked/interesting jobs stay eligible.
        if not has_applied_application(self.session, job.id) and not has_valid_match:
            JobMatchingService(self.session).match_job(
                job.id,
                self.profile_path,
                run_id=run_id,
            )
            match_performed = True

        self.session.flush()
        analysis_row, match_row = self._latest_rows(job)
        return JobProcessingResult(job, analysis_row, match_row, analysis_performed, match_performed)

    def _latest_rows(self, job: Job) -> tuple[JobAnalysisRow | None, MatchResultRow | None]:
        analysis = self.session.scalar(
            select(JobAnalysisRow)
            .where(JobAnalysisRow.job_id == job.id)
            .order_by(JobAnalysisRow.created_at.desc())
        )
        match = self.session.scalar(
            select(MatchResultRow)
            .where(MatchResultRow.job_id == job.id)
            .order_by(MatchResultRow.created_at.desc())
        )
        return analysis, match

    @staticmethod
    def _analysis_matches_current_hash(
        job: Job,
        analysis_row: JobAnalysisRow | None,
        expected_version: str | None,
    ) -> bool:
        if analysis_row is None:
            return False
        if expected_version is not None and analysis_row.prompt_version != expected_version:
            return False
        try:
            payload = json.loads(analysis_row.analysis_notes or "{}")
        except Exception:
            return False
        return payload.get("job_content_hash") == job.content_hash

    def _match_matches_current_hash(
        self,
        job: Job,
        match_row: MatchResultRow | None,
        expected_version: str | None,
    ) -> bool:
        if match_row is None or not isinstance(match_row.evidence, dict):
            return False
        if expected_version is not None and match_row.evidence.get("matching_version") != expected_version:
            return False
        if match_row.evidence.get("job_content_hash") != job.content_hash:
            return False
        # JobAnalysis is job-only. A changed canonical candidate profile makes
        # only the match stale, so the next processing pass reuses the analysis
        # and writes an append-only refreshed MatchResult.
        profile = self.session.get(CandidateProfile, match_row.candidate_profile_id) if match_row.candidate_profile_id else None
        from backend.app.candidate_profile.service import profile_version
        return profile is not None and profile.version == profile_version(self.profile_path)
