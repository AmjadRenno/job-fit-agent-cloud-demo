from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


# Sections that describe how evidence may be used (guidance/meta), not the
# candidate's skills. Their claims must never be returned as matching evidence
# and must never be classified as hands-on skill evidence.
META_SECTIONS = frozenset(
    {
        "Retrieval and Claim Rules",
        "Source Note",
        "Evidence Classification",
        # These headings may remain in older canonical files for readability,
        # but are user preferences, never professional evidence.
        "Target Roles",
        "Location / Work Preferences",
    }
)


@dataclass(frozen=True)
class CandidateEvidence:
    evidence_id: str
    source: str
    section: str
    claim: str
    classification: str
    confidence: str


class CandidateEvidenceIndex:
    def __init__(self, evidence: list[CandidateEvidence]) -> None:
        self.evidence = tuple(evidence)

    @classmethod
    def from_profile(cls, path: Path) -> "CandidateEvidenceIndex":
        text = path.read_text(encoding="utf-8")
        current_section = "Profile"
        current_level = ""
        evidence: list[CandidateEvidence] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("### "):
                current_level = stripped.removeprefix("### ").strip()
                continue
            if stripped.startswith("## "):
                current_section = stripped.removeprefix("## ").strip()
                current_level = ""
                continue
            if not stripped.startswith("- "):
                continue
            claim = stripped.removeprefix("- ").strip()
            if not claim or claim.startswith("**"):
                continue
            classification = cls._classification(current_section, claim, current_level)
            digest = hashlib.sha256(f"{current_section}\n{claim}".encode()).hexdigest()[:16]
            evidence.append(CandidateEvidence(
                evidence_id=f"profile:{digest}",
                source="candidate_profile",
                section=current_section,
                claim=claim,
                classification=classification,
                confidence="HIGH" if classification in {"strong_hands_on", "significant_project"} else "MEDIUM",
            ))
        return cls(evidence)

    def find(self, requirement: str) -> tuple[CandidateEvidence, ...]:
        requirement_terms = _terms(requirement)
        if not requirement_terms:
            return ()
        return tuple(
            item
            for item in self.evidence
            if item.section not in META_SECTIONS and requirement_terms & _terms(item.claim)
        )

    @staticmethod
    def _classification(section: str, claim: str, level: str = "") -> str:
        if section == "Education":
            return "coursework"
        if section == "Languages" or section in META_SECTIONS:
            return "familiarity"
        level_lowered = level.casefold()
        if "coursework" in level_lowered or "studied exposure" in level_lowered:
            return "coursework"
        if "developing" in level_lowered or "recently learned" in level_lowered:
            return "developing"
        if "significant project" in level_lowered:
            return "significant_project"
        if "strong hands-on" in level_lowered:
            return "strong_hands_on"
        lowered = f"{section} {claim}".casefold()
        if "course" in lowered or "studied exposure" in lowered:
            return "coursework"
        if "developing" in lowered or "recently learned" in lowered:
            return "developing"
        if "familiarity" in lowered:
            return "familiarity"
        if "project" in lowered:
            return "significant_project"
        return "strong_hands_on"


def _terms(value: str) -> set[str]:
    return {
        term for term in re.findall(r"[a-z0-9]+(?:[+#.-][a-z0-9]+)*", value.casefold())
        if term not in {"and", "or", "the", "with", "for", "in", "of", "to", "a", "an"}
    }
