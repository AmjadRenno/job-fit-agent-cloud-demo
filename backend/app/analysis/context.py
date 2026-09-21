from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.app.db.models import Job

REQUIRED_PROFILE_HEADINGS = ("## Professional Identity", "## Evidence Classification")

# Deterministic eligibility gate (Phase 12 fix #4): the candidate operates from
# Denmark; only these persisted country codes may reach analysis/matching.
# This is an explicit pre-analysis gate in addition to (never instead of) the
# source-specific DK filtering already provided by the Solita/Vestas adapters.
# Reuses the existing Job.country_code representation; no LLM judgment.
ELIGIBLE_COUNTRY_CODES = frozenset({"DK"})

# Sources deliberately approved to enter the analysis pipeline. New sources
# are added here ONLY after a per-source gate review (human approval +
# execution-ready handler) and a deliberate evaluation coverage decision. This
# is an explicit allowlist; unapproved sources stay blocked. Source eligibility
# is independent of, and never relaxes, the availability gate below. Energinet
# is archived and has no approved execution handler, so historical jobs from
# that source cannot enter analysis even if a record says ACTIVE.
ANALYSIS_ELIGIBLE_SOURCES = frozenset({"solita_dk", "vestas_dk", "trifork_dk", "demo_synthetic"})

# Deterministic cap applied before LLM context construction so hostile or
# pathological pages cannot blow up the prompt with unbounded text.
MAX_LLM_DESCRIPTION_CHARS = 12000


class AnalysisPreconditionError(ValueError):
    pass


@dataclass(frozen=True)
class CandidateProfile:
    path: Path
    text: str

    @classmethod
    def load(cls, path: Path) -> "CandidateProfile":
        if not path.is_file():
            raise AnalysisPreconditionError(f"candidate profile not found: {path}")
        text = path.read_text(encoding="utf-8")
        missing = [heading for heading in REQUIRED_PROFILE_HEADINGS if heading not in text]
        if missing:
            raise AnalysisPreconditionError(f"candidate profile is missing sections: {missing}")
        text = text.split("## Source Note", 1)[0].strip()
        text = "\n".join(line for line in text.splitlines() if "data/candidate/source/" not in line)
        return cls(path=path, text=text)


@dataclass(frozen=True)
class AnalysisContext:
    job_id: str
    source: str
    job_title: str
    job_description: str
    job_location: str | None
    country_code: str | None
    candidate_profile: str


def build_context(job: Job, profile: CandidateProfile) -> AnalysisContext:
    if job.source.source_id not in ANALYSIS_ELIGIBLE_SOURCES:
        raise AnalysisPreconditionError(
            f"source {job.source.source_id!r} is not approved for analysis; "
            f"approved sources: {sorted(ANALYSIS_ELIGIBLE_SOURCES)}"
        )
    if job.source.source_id == "trifork_dk" and (
        job.source.lifecycle_status != "ACTIVE" or not job.source.enabled
    ):
        raise AnalysisPreconditionError("Trifork source requires human approval before analysis")
    if job.availability_status != "ACTIVE":
        raise AnalysisPreconditionError(
            f"job {job.id} is not explicitly available: {job.availability_status}"
        )
    # Explicit deterministic location eligibility (Phase 12 fix #4): reject
    # configured ineligible countries before any LLM work, independent of the
    # source adapters' own DK filtering. An unknown country (None) is not a
    # configured ineligible location - it is passed through so that source-
    # level DK filtering continues to govern, and no eligibility is fabricated.
    if job.country_code is not None and job.country_code not in ELIGIBLE_COUNTRY_CODES:
        raise AnalysisPreconditionError(
            f"job country {job.country_code!r} is outside the configured "
            f"eligible locations: {sorted(ELIGIBLE_COUNTRY_CODES)}"
        )
    if not job.title or not job.description:
        raise AnalysisPreconditionError("job title and description are required")
    return AnalysisContext(
        job_id=str(job.id),
        source=job.source.source_id,
        job_title=job.title,
        job_description=job.description[:MAX_LLM_DESCRIPTION_CHARS],
        job_location=job.location_raw,
        country_code=job.country_code,
        candidate_profile=profile.text,
    )
