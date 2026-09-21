from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from backend.app.db.models import AgentRun, Job, JobAnalysis

from .context import CandidateProfile, build_context
from .domain import JobAnalysis as AnalysisContract
from .llm import PROMPT_VERSION, StructuredAnalyzer


class JobAnalysisService:
    def __init__(self, session: Session, analyzer: StructuredAnalyzer) -> None:
        self.session = session
        self.analyzer = analyzer

    def analyze_job(self, job_id: Any, profile_path: Path, run_id: Any = None) -> AnalysisContract:
        job = self.session.get(Job, job_id)
        if job is None:
            raise LookupError(f"job not found: {job_id}")
        profile = CandidateProfile.load(profile_path)
        context = build_context(job, profile)
        result = self.analyzer.analyze({
            "job_id": context.job_id,
            "source": context.source,
            "job_title": context.job_title,
            "job_description": context.job_description,
            "job_location": context.job_location,
            "country_code": context.country_code,
            "candidate_profile": context.candidate_profile,
        })
        validated = AnalysisContract.model_validate(result).model_copy(
            update={"analysis_version": PROMPT_VERSION}
        )
        if run_id is not None and self.session.get(AgentRun, run_id) is None:
            self.session.add(
                AgentRun(
                    id=run_id,
                    started_at=datetime.now(timezone.utc),
                    status="RUNNING",
                )
            )
            self.session.flush()
        row = JobAnalysis(
            job_id=job.id,
            run_id=run_id,
            responsibilities=None,
            must_have=None,
            nice_to_have=None,
            technologies=None,
            seniority_level=None,
            language_requirement=None,
            key_constraints=None,
            analysis_notes=json.dumps({
                "analysis_version": validated.analysis_version,
                "confidence": validated.confidence,
                "job_content_hash": job.content_hash,
            }, default=str),
            model=self.analyzer.model,
            prompt_version=PROMPT_VERSION,
            schema_valid=True,
            validation_errors=None,
            analysis_result=validated.model_dump(mode="json"),
            created_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        self.session.flush()
        # Persistent analysis guard (Phase 12 fix #6): mark the job as analyzed
        # so daily repeated runs skip this job until its content changes.
        job.analysis_status = "COMPLETED"
        return validated
