from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Requirement:
    requirement: str
    importance: str = "unknown"
    requirement_type: str = "other"


@dataclass(frozen=True)
class MatchWeights:
    required_match: float = 1.0
    required_partial: float = 0.5
    required_missing: float = 0.0
    required_unknown: float = 0.0
    preferred_match: float = 0.5
    preferred_partial: float = 0.25
    preferred_missing: float = 0.0
    preferred_unknown: float = 0.0


def extract_requirements(job_description: str) -> list[Requirement]:
    text = " ".join(job_description.split())
    if not text:
        return []
    candidates: list[str] = []
    for chunk in re.split(r"\.(?=\s|$)|[;\n]", text):
        parts = [part.strip() for part in re.split(r",| and | or |/", chunk.strip()) if part.strip()]
        candidates.extend(part for part in parts if len(part) > 2)
    requirements: list[Requirement] = []
    for candidate in candidates:
        normalized = normalize_requirement(candidate)
        if not normalized or normalized.casefold() in {item.requirement.casefold() for item in requirements}:
            continue
        requirements.append(Requirement(normalized, classify_importance(normalized), classify_requirement_type(normalized)))
    return requirements or [Requirement(text[:120])]


def normalize_requirement(requirement: str) -> str:
    return re.sub(r"\s+", " ", requirement).strip(" -:;,. ")


def classify_importance(requirement: str) -> str:
    lowered = requirement.lower()
    if any(token in lowered for token in ("preferred", "nice to have", "nice-to-have", "desirable", "bonus", "plus")):
        return "preferred"
    if any(token in lowered for token in ("must have", "must", "required", "mandatory", "essential")) or "experience" in lowered:
        return "required"
    return "unknown"


def classify_requirement_type(requirement: str) -> str:
    lowered = requirement.lower()
    if any(token in lowered for token in ("degree", "bachelor", "master", "education")):
        return "education"
    if "experience" in lowered:
        return "experience"
    if any(token in lowered for token in ("certification", "certified")):
        return "certification"
    if any(token in lowered for token in ("api", "rest", "sql", "docker", "kubernetes", "react", "typescript", "javascript", "python", "c#", ".net", "asp.net", "rag", "llm", "agent", "openai", "mcp", "langgraph", "azure", "terraform")):
        return "technology"
    if any(token in lowered for token in ("build", "maintain", "develop", "design", "support", "deliver")):
        return "responsibility"
    return "other"


def score_requirement(requirement: Requirement, status: str, weights: MatchWeights | None = None) -> float:
    weights = weights or MatchWeights()
    mapping = {
        "required": {"matched": weights.required_match, "partially_matched": weights.required_partial, "missing": weights.required_missing, "unknown": weights.required_unknown},
        "preferred": {"matched": weights.preferred_match, "partially_matched": weights.preferred_partial, "missing": weights.preferred_missing, "unknown": weights.preferred_unknown},
        "unknown": {"matched": 0.5, "partially_matched": 0.25, "missing": 0.0, "unknown": 0.0},
    }
    return mapping.get(requirement.importance, mapping["unknown"]).get(status, 0.0)
