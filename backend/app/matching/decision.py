from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from backend.app.analysis.domain import JobAnalysis
from backend.app.db.models import Job

from .domain import MatchClassification, RequirementMatch


class ApplicationDecision(StrEnum):
    APPLY = "APPLY"
    CONSIDER = "CONSIDER"
    LOW_PRIORITY = "LOW_PRIORITY"
    SKIP = "SKIP"


@dataclass(frozen=True)
class DecisionResult:
    recommendation: ApplicationDecision
    reasons: tuple[str, ...]
    critical_gaps: tuple[str, ...]
    next_step: str

    def as_dict(self) -> dict[str, object]:
        return {
            "recommendation": self.recommendation.value,
            "reasons": list(self.reasons),
            "critical_gaps": list(self.critical_gaps),
            "next_step": self.next_step,
        }


_LANGUAGE_REQUIREMENT = re.compile(r"\b(?:fluent|proficient|professional|native)\b.*\b(?:danish|english)\b", re.I)
_SENIORITY_REQUIREMENT = re.compile(r"\b(?:senior|lead|principal|staff|architect|\d+\s*\+?\s*years?)\b", re.I)
_UNSOLICITED_APPLICATION = re.compile(
    r"\b(?:uopfordrede?\s+ans(?:ø|o)gninger?|unsolicited\s+applications?|open\s+applications?)\b",
    re.I,
)
_ORDER = {
    ApplicationDecision.SKIP: 0,
    ApplicationDecision.LOW_PRIORITY: 1,
    ApplicationDecision.CONSIDER: 2,
    ApplicationDecision.APPLY: 3,
}


def derive_application_decision(
    *,
    job: Job,
    analysis: JobAnalysis,
    requirements: list[RequirementMatch],
    score: float,
    confidence: str,
    evidence_completeness: str,
) -> DecisionResult:
    """Convert the evidence match into a conservative, explainable action."""
    if _UNSOLICITED_APPLICATION.search(job.title):
        return DecisionResult(
            ApplicationDecision.SKIP,
            ("The posting is an unsolicited application rather than a specific open role.",),
            ("No specific role or bounded requirements to evaluate",),
            "Skip this general application and focus on a concrete vacancy.",
        )
    if job.country_code != "DK":
        return DecisionResult(
            ApplicationDecision.SKIP,
            ("Location is outside the configured Denmark search boundary.",),
            (f"Ineligible location: {job.country_code or 'unknown'}",),
            "Skip this job unless the location requirement changes.",
        )

    if score >= 75:
        decision = ApplicationDecision.APPLY
    elif score >= 60:
        decision = ApplicationDecision.CONSIDER
    elif score >= 40:
        decision = ApplicationDecision.LOW_PRIORITY
    else:
        decision = ApplicationDecision.SKIP

    missing = [item for item in requirements if item.classification == MatchClassification.MISSING]
    critical = [
        item.requirement
        for item in missing
        if item.importance == "required"
        or _SENIORITY_REQUIREMENT.search(item.requirement)
        or _LANGUAGE_REQUIREMENT.search(item.requirement)
    ]
    if critical:
        decision = _cap(decision, ApplicationDecision.LOW_PRIORITY)
    if confidence == "LOW" or evidence_completeness == "LOW":
        decision = _cap(decision, ApplicationDecision.CONSIDER)

    direct = sum(item.classification == MatchClassification.DIRECT for item in requirements)
    transferable = sum(item.classification == MatchClassification.TRANSFERABLE for item in requirements)
    reasons = [f"Evidence-based match is {round(score)}%." ]
    if direct or transferable:
        reasons.append(f"Profile evidence directly supports {direct} requirement(s) and transfers to {transferable}.")
    if critical:
        reasons.append(f"A critical requirement is not evidenced: {critical[0]}.")
    elif missing:
        reasons.append(f"{len(missing)} requirement(s) lack profile evidence.")
    else:
        reasons.append("No matched requirement is currently classified as missing.")
    if len(reasons) < 3:
        reasons.append(f"Decision confidence is {confidence.lower()} with {evidence_completeness.lower()} evidence completeness.")

    next_steps = {
        ApplicationDecision.APPLY: "Start an application and prepare a grounded cover-letter draft.",
        ApplicationDecision.CONSIDER: "Track as interesting and review the remaining gaps before applying.",
        ApplicationDecision.LOW_PRIORITY: "Apply only if the role is especially attractive or the critical gap can be addressed.",
        ApplicationDecision.SKIP: "Do not spend application time on this role now.",
    }
    return DecisionResult(decision, tuple(reasons[:3]), tuple(critical), next_steps[decision])


def _cap(value: ApplicationDecision, maximum: ApplicationDecision) -> ApplicationDecision:
    return value if _ORDER[value] <= _ORDER[maximum] else maximum
