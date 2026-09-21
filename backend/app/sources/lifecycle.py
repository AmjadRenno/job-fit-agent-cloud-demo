"""Pure source lifecycle rules for Company Source Management."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.app.db.models import Company, JobSource

SOURCE_LIFECYCLE_STATES = frozenset({
    "DRAFT",
    "ONBOARDING",
    "PREVIEW_READY",
    "ACTIVE",
    "DISABLED",
    "REVALIDATION_REQUIRED",
    "ARCHIVED",
})

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"ONBOARDING", "DISABLED", "ARCHIVED"}),
    "ONBOARDING": frozenset({"PREVIEW_READY", "DISABLED", "ARCHIVED"}),
    "PREVIEW_READY": frozenset({"ACTIVE", "ONBOARDING", "DISABLED", "ARCHIVED"}),
    "ACTIVE": frozenset({"REVALIDATION_REQUIRED", "DISABLED", "ARCHIVED"}),
    "DISABLED": frozenset({"ONBOARDING", "ARCHIVED"}),
    "REVALIDATION_REQUIRED": frozenset({"ONBOARDING", "DISABLED", "ARCHIVED"}),
    "ARCHIVED": frozenset(),
}


class InvalidSourceTransition(ValueError):
    """Raised when a source lifecycle transition violates the state machine."""


def can_transition(current: str, target: str) -> bool:
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


def transition_source(
    source: JobSource,
    target: str,
    *,
    preview_valid: bool = False,
    human_approved: bool = False,
    preview_version: int | None = None,
) -> JobSource:
    """Apply one lifecycle transition without performing onboarding work."""
    current = source.lifecycle_status
    if target not in SOURCE_LIFECYCLE_STATES or not can_transition(current, target):
        raise InvalidSourceTransition(f"cannot transition source from {current!r} to {target!r}")
    if target == "ACTIVE" and not (preview_valid and human_approved and preview_version is not None):
        raise InvalidSourceTransition("ACTIVE requires a valid preview, preview version, and explicit human approval")

    source.lifecycle_status = target
    source.enabled = target == "ACTIVE"
    if target == "ARCHIVED":
        source.archived_at = datetime.now(timezone.utc)
    elif target == "ACTIVE":
        source.archived_at = None
        source.approved_preview_version = preview_version
        source.approved_at = datetime.now(timezone.utc)
    return source


def mark_boundary_changed(source: JobSource) -> JobSource:
    """Invalidate active approval after a source-boundary change."""
    if source.lifecycle_status == "ACTIVE":
        source.lifecycle_status = "REVALIDATION_REQUIRED"
        source.enabled = False
    return source


def archive_company(company: Company, sources: list[JobSource] | None = None) -> Company:
    """Archive a company and its sources while leaving historical jobs intact."""
    now = datetime.now(timezone.utc)
    company.lifecycle_status = "ARCHIVED"
    company.archived_at = now
    for source in sources if sources is not None else company.job_sources:
        source.lifecycle_status = "ARCHIVED"
        source.enabled = False
        source.archived_at = now
    return company


def preview_can_activate(
    source: JobSource,
    preview: Any,
    *,
    now: datetime | None = None,
) -> bool:
    """Check persisted preview identity/status before a future approval command."""
    current_time = now or datetime.now(timezone.utc)
    expires_at = preview.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return (
        source.lifecycle_status == "PREVIEW_READY"
        and source.readiness_status != "BLOCKED"
        and preview.source_id == source.id
        and preview.status == "READY"
        and preview.boundary_fingerprint == source.boundary_fingerprint
        and bool((preview.validation_summary or {}).get("execution", {}).get("ready", True))
        and (expires_at is None or expires_at > current_time)
    )
