from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from backend.app.applications.api import human_command
from backend.app.candidate_profile.service import CandidateProfileService
from backend.app.dashboard.api import AuthDep, get_session

router = APIRouter(prefix="/api/profile", tags=["profile"])
SessionDep = Annotated[Session, Depends(get_session)]
HumanDep = Annotated[None, Depends(human_command)]


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=500)
    level: Literal["Beginner", "Intermediate", "Advanced", "Expert"] = "Intermediate"


class PreferencesData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_roles: list[str] = Field(default_factory=list, max_length=50)
    locations: list[str] = Field(default_factory=list, max_length=50)
    work_modes: list[str] = Field(default_factory=list, max_length=10)
    job_languages: list[str] = Field(default_factory=list, max_length=20)
    cover_letter_tone: str = Field(default="Direct & professional", max_length=80)
    minimum_fit: int = Field(default=60, ge=0, le=100)


class FactsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # User-confirmed additions are deliberately classified as developing by the
    # canonical Markdown section, so a form chip cannot create direct evidence.
    confirmed_additions: list[str] = Field(default_factory=list, max_length=100)


class ProfileData(BaseModel):
    """Legacy compatibility view. Professional fields are read-only projections."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Candidate", min_length=1, max_length=120)
    headline: str = Field(default="", max_length=2000)
    summary: str = Field(default="", max_length=5000)
    email: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=80)
    seniority: str = Field(default="Unspecified", max_length=40)
    years_experience: int = Field(default=0, ge=0, le=60)
    skills: list[Skill] = Field(default_factory=list, max_length=100)
    target_roles: list[str] = Field(default_factory=list, max_length=50)
    locations: list[str] = Field(default_factory=list, max_length=50)
    languages: list[str] = Field(default_factory=list, max_length=20)
    work_modes: list[str] = Field(default_factory=list, max_length=10)
    cover_letter_tone: str = Field(default="Direct & professional", max_length=80)
    minimum_fit: int = Field(default=60, ge=0, le=100)

    @field_validator("skills")
    @classmethod
    def unique_skills(cls, skills: list[Skill]) -> list[Skill]:
        names = [item.name.strip().casefold() for item in skills]
        if len(names) != len(set(names)):
            raise ValueError("skill names must be unique")
        return skills


class ProfileCommand(ProfileData):
    facts: FactsPatch | None = None
    preferences: PreferencesData | None = None


class FactsRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    evidence_count: int
    items: list[dict]


class ProfileRead(ProfileData):
    facts: FactsRead
    preferences: PreferencesData
    version: str
    recalculated_matches: int = 0
    stale_matches: int = 0
    # Retained for the existing client while Stage 4 changes its presentation.
    rescored_jobs: int = 0


def profile_service(session: SessionDep) -> CandidateProfileService:
    return CandidateProfileService(session)


def _read(service: CandidateProfileService, *, recalculated_matches: int = 0) -> ProfileRead:
    facts = service.facts_view()
    preferences = service.preferences()
    legacy = service.legacy_view()
    return ProfileRead(
        **legacy,
        facts=facts,
        preferences=preferences,
        version=facts["version"],
        recalculated_matches=recalculated_matches,
        stale_matches=service.stale_match_count(),
        rescored_jobs=recalculated_matches,
    )


@router.get("", response_model=ProfileRead)
def read_profile(_: AuthDep, service: Annotated[CandidateProfileService, Depends(profile_service)]) -> ProfileRead:
    return _read(service)


@router.put("", response_model=ProfileRead)
def save_profile(command: ProfileCommand, _: AuthDep, __: HumanDep, service: Annotated[CandidateProfileService, Depends(profile_service)]) -> ProfileRead:
    try:
        recalculated = 0
        if command.facts is not None:
            _, recalculated = service.update_confirmed_facts(command.facts.confirmed_additions)
        if command.preferences is not None:
            service.save_preferences(command.preferences.model_dump())
        elif {"target_roles", "locations", "work_modes", "cover_letter_tone", "minimum_fit"} & command.model_fields_set:
            # Existing clients may save their whole legacy form. Only its actual
            # preferences are retained; stale or demo professional fields are ignored.
            service.save_preferences({
                "target_roles": command.target_roles,
                "locations": command.locations,
                "work_modes": command.work_modes,
                "cover_letter_tone": command.cover_letter_tone,
                "minimum_fit": command.minimum_fit,
            })
        return _read(service, recalculated_matches=recalculated)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
