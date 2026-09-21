from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.applications.service import has_applied_application
from backend.app.db.models import CandidateProfile, Job, JobAnalysis, JobSource, MatchResult
from backend.app.matching.evidence import CandidateEvidenceIndex
from backend.app.matching.service import JobMatchingService, MATCHING_ELIGIBLE_SOURCES


PREFERENCES_NOTE = "CANDIDATE_PREFERENCES_V1"
DEFAULT_PREFERENCES = {
    "target_roles": [],
    "locations": [],
    "work_modes": [],
    "job_languages": [],
    "minimum_fit": 60,
    "cover_letter_tone": "Direct & professional",
}
USER_UPDATE_HEADING = "## Candidate-confirmed updates"


@dataclass(frozen=True)
class CanonicalProfile:
    path: Path
    text: str
    version: str
    evidence: CandidateEvidenceIndex


class CandidateProfileService:
    """Single boundary for canonical facts, evidence, and non-evidence preferences.

    Markdown is an implementation detail contained here. Matching and grounding
    keep using CandidateEvidenceIndex, so submitted facts cannot bypass the
    existing evidence classifications.
    """

    def __init__(self, session: Session, profile_path: Path | None = None) -> None:
        self.session = session
        self.profile_path = profile_path or Path("data/candidate/profile.md")

    def canonical(self) -> CanonicalProfile:
        text = self.profile_path.read_text(encoding="utf-8")
        return CanonicalProfile(
            path=self.profile_path,
            text=text,
            version=profile_version(self.profile_path),
            evidence=CandidateEvidenceIndex.from_profile(self.profile_path),
        )

    def facts_view(self) -> dict[str, Any]:
        profile = self.canonical()
        return {
            "version": profile.version,
            "evidence_count": len(profile.evidence.evidence),
            "items": [
                {
                    "evidence_id": item.evidence_id,
                    "section": item.section,
                    "claim": item.claim,
                    "classification": item.classification,
                    "confidence": item.confidence,
                }
                for item in profile.evidence.evidence
            ],
        }

    def legacy_view(self) -> dict[str, Any]:
        """Temporary compatibility projection for the pre-Stage-4 form.

        Values are derived from canonical facts and preferences; no profile_data
        professional field can overwrite them.
        """
        profile = self.canonical()
        identity = self._section_bullets(profile.text, "Professional Identity")
        languages = self._section_bullets(profile.text, "Languages")
        skills = []
        seen = set()
        for item in profile.evidence.evidence:
            if item.section not in {"Technical Skills", "Architecture & Engineering", "Security", "AI / Agentic AI"}:
                continue
            if item.claim.casefold() in seen:
                continue
            seen.add(item.claim.casefold())
            level = {"strong_hands_on": "Advanced", "significant_project": "Intermediate", "developing": "Beginner", "coursework": "Beginner", "familiarity": "Beginner"}[item.classification]
            skills.append({"name": item.claim, "level": level})
        preferences = self.preferences()
        heading = identity[0] if identity else "Candidate"
        name = profile.text.splitlines()[0].removeprefix("# ").split(" - ", 1)[0].strip() or "Candidate"
        return {
            "name": name,
            "headline": heading,
            "summary": " ".join(identity),
            "email": "",
            "phone": "",
            "seniority": "Junior" if "junior" in heading.casefold() else "Unspecified",
            "years_experience": 0,
            "skills": skills,
            "target_roles": preferences["target_roles"],
            "locations": preferences["locations"],
            # This legacy field means candidate capabilities, never job-ad filtering.
            "languages": languages,
            "work_modes": preferences["work_modes"],
            "cover_letter_tone": preferences["cover_letter_tone"],
            "minimum_fit": preferences["minimum_fit"],
        }

    def preferences(self) -> dict[str, Any]:
        """Read only preference keys from new or legacy payloads.

        Legacy profile_data may contain old demo facts, but they are never read
        into canonical facts or evidence.  This preserves useful preferences
        while eliminating the old split-brain authority.
        """
        rows = self.session.scalars(
            select(CandidateProfile).where(CandidateProfile.notes == PREFERENCES_NOTE).order_by(CandidateProfile.created_at.desc(), CandidateProfile.id.desc())
        ).all()
        # One-way legacy compatibility: only if a new preference record has
        # never been saved do we read preference keys from a former mixed row.
        if not rows:
            rows = self.session.scalars(
                select(CandidateProfile).order_by(CandidateProfile.created_at.desc(), CandidateProfile.id.desc())
            ).all()
        for row in rows:
            payload = row.profile_data
            if not isinstance(payload, dict):
                continue
            candidate = payload.get("preferences") if row.notes == PREFERENCES_NOTE else payload
            if isinstance(candidate, dict):
                return self._normalise_preferences(candidate)
        return self._preferences_from_canonical()

    def save_preferences(self, values: dict[str, Any]) -> dict[str, Any]:
        payload = self._normalise_preferences(values)
        digest = hashlib.sha256(repr(sorted(payload.items())).encode()).hexdigest()
        row = CandidateProfile(
            version=f"preferences:{digest[:16]}",
            profile_hash=digest,
            is_active=False,
            notes=PREFERENCES_NOTE,
            profile_data={"preferences": payload},
        )
        self.session.add(row)
        self.session.commit()
        return payload

    def update_confirmed_facts(self, additions: list[str]) -> tuple[dict[str, Any], int]:
        """Safely replace only the user-confirmed/developing update block.

        These entries remain evidence, but the existing index classifies them
        as developing rather than direct professional experience.  The original
        researched canonical content is never overwritten by this operation.
        """
        cleaned = self._normalise_additions(additions)
        original = self.profile_path.read_text(encoding="utf-8")
        replacement = self._render_update_block(cleaned)
        pattern = re.compile(
            rf"\n{re.escape(USER_UPDATE_HEADING)}\n.*?(?=\n## (?!#)|\Z)", re.DOTALL
        )
        if pattern.search(original):
            updated = pattern.sub("\n" + replacement, original).rstrip() + "\n"
        else:
            anchor = "\n## Evidence Classification\n"
            if anchor not in original:
                raise ValueError("canonical profile is missing its evidence classification section")
            updated = original.replace(anchor, "\n" + replacement + anchor, 1)
        if updated != original:
            backup = self.profile_path.with_suffix(self.profile_path.suffix + ".bak")
            if not backup.exists():
                backup.write_text(original, encoding="utf-8")
            temporary = self.profile_path.with_suffix(self.profile_path.suffix + ".tmp")
            temporary.write_text(updated, encoding="utf-8")
            os.replace(temporary, self.profile_path)
        recalculated = self.recalculate_matches()
        return self.facts_view(), recalculated

    def recalculate_matches(self) -> int:
        """Refresh matches from stored JobAnalysis rows; no LLM analysis is run."""
        jobs = self.session.scalars(
            select(Job)
            .join(JobSource, Job.source_id == JobSource.id)
            .where(
                Job.availability_status == "ACTIVE",
                Job.analysis_status == "COMPLETED",
                JobSource.source_id.in_(sorted(MATCHING_ELIGIBLE_SOURCES)),
                JobSource.lifecycle_status == "ACTIVE",
                JobSource.enabled.is_(True),
            )
        ).all()
        count = 0
        matcher = JobMatchingService(self.session)
        for job in jobs:
            if has_applied_application(self.session, job.id):
                continue
            analysis = self.session.scalar(
                select(JobAnalysis).where(JobAnalysis.job_id == job.id).order_by(JobAnalysis.created_at.desc())
            )
            if analysis is None:
                continue
            matcher.match_job(job.id, self.profile_path)
            count += 1
        self.session.commit()
        return count

    def stale_match_count(self) -> int:
        current = self.canonical().version
        rows = self.session.scalars(select(MatchResult)).all()
        return sum(
            1
            for row in rows
            if row.candidate_profile_id is None
            or (profile := self.session.get(CandidateProfile, row.candidate_profile_id)) is None
            or profile.version != current
        )

    @staticmethod
    def _normalise_additions(values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            item = value.strip()
            if not item or item.startswith("-") or "\n" in item or len(item) > 500:
                raise ValueError("candidate fact additions must be single, non-empty statements")
            if item.casefold() not in {known.casefold() for known in cleaned}:
                cleaned.append(item)
        return cleaned

    @staticmethod
    def _render_update_block(additions: list[str]) -> str:
        lines = [USER_UPDATE_HEADING, "", "### Developing / recently learned", ""]
        lines.extend(f"- {item}" for item in additions)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _normalise_preferences(values: dict[str, Any]) -> dict[str, Any]:
        result = dict(DEFAULT_PREFERENCES)
        for field in ("target_roles", "locations", "work_modes", "job_languages"):
            value = values.get(field, result[field])
            if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                raise ValueError(f"{field} must be a list of non-empty strings")
            result[field] = list(dict.fromkeys(item.strip() for item in value))
        minimum_fit = values.get("minimum_fit", result["minimum_fit"])
        if not isinstance(minimum_fit, int) or not 0 <= minimum_fit <= 100:
            raise ValueError("minimum_fit must be an integer from 0 to 100")
        result["minimum_fit"] = minimum_fit
        tone = values.get("cover_letter_tone", result["cover_letter_tone"])
        if not isinstance(tone, str) or not tone.strip() or len(tone) > 80:
            raise ValueError("cover_letter_tone must be a non-empty string up to 80 characters")
        result["cover_letter_tone"] = tone.strip()
        return result

    def _preferences_from_canonical(self) -> dict[str, Any]:
        text = self.canonical().text
        roles = self._section_bullets(text, "Target Roles")
        location = self._section_bullets(text, "Location / Work Preferences")
        joined = " ".join(location).casefold()
        return self._normalise_preferences({
            "target_roles": roles,
            "locations": ["Denmark"] if "denmark" in joined else [],
            "work_modes": [mode for mode in ("On-site", "Hybrid", "Remote") if mode.casefold() in joined],
        })

    @staticmethod
    def _section_bullets(text: str, heading: str) -> list[str]:
        match = re.search(rf"^## {re.escape(heading)}\s*$\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
        if not match:
            return []
        return [line.removeprefix("- ").strip() for line in match.group(1).splitlines() if line.startswith("- ")]


def profile_version(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
