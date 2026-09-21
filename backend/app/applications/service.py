from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import Application, ApplicationEvent, CoverLetter, Job

from .domain import validate_transition


class ApplicationAuthorizationError(PermissionError):
    pass


# Application states that mean the human has actually applied (or is past the
# application step). Merely tracked states (DISCOVERED, INTERESTING, TO_APPLY,
# IGNORED) do not count as applied.
APPLIED_APPLICATION_STATUSES = frozenset(
    {"APPLIED", "INTERVIEW", "OFFER", "REJECTED", "CLOSED"}
)


def has_applied_application(session: Session, job_id: Any) -> bool:
    """Deterministically report whether the job has been applied for already."""
    existing = session.scalar(
        select(Application.id).where(
            Application.job_id == job_id,
            Application.status.in_(sorted(APPLIED_APPLICATION_STATUSES)),
        )
    )
    return existing is not None


class CoverLetterAssociationError(ValueError):
    pass


class ApplicationService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_for_human(self, job_id: UUID, *, human_authorized: bool, notes: str | None = None) -> Application:
        self._require_human(human_authorized)
        job = self.session.get(Job, job_id)
        if job is None:
            raise LookupError("job not found")
        existing = self.session.scalar(select(Application).where(Application.job_id == job_id))
        if existing is not None:
            return existing
        application = Application(job_id=job_id, status="INTERESTING", notes=notes)
        self.session.add(application)
        self.session.flush()
        self.session.add(ApplicationEvent(
            application_id=application.id,
            from_status=None,
            to_status="INTERESTING",
            triggered_by="HUMAN",
            notes=notes,
        ))
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return application

    def transition(self, application_id: UUID, target: str, *, human_authorized: bool, notes: str | None = None) -> Application:
        self._require_human(human_authorized)
        application = self.session.get(Application, application_id)
        if application is None:
            raise LookupError("application not found")
        validate_transition(application.status, target)
        if application.status == target:
            return application
        previous = application.status
        application.status = target
        if notes is not None:
            application.notes = notes
        if target == "APPLIED" and application.applied_at is None:
            application.applied_at = datetime.now(timezone.utc)
        self.session.add(ApplicationEvent(
            application_id=application.id,
            from_status=previous,
            to_status=target,
            triggered_by="HUMAN",
            notes=notes,
        ))
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return application

    def attach_cover_letter(self, application_id: UUID, cover_letter_id: UUID, *, human_authorized: bool) -> Application:
        self._require_human(human_authorized)
        application = self.session.get(Application, application_id)
        if application is None:
            raise LookupError("application not found")
        letter = self.session.get(CoverLetter, cover_letter_id)
        if letter is None:
            raise LookupError("cover letter not found")
        if letter.job_id != application.job_id:
            raise CoverLetterAssociationError("cover letter does not belong to the application job")
        if letter.application_id == application.id:
            return application
        if letter.application_id is not None:
            raise CoverLetterAssociationError("cover letter is already associated with another application")
        letter.application_id = application.id
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return application

    def history(self, application_id: UUID) -> list[ApplicationEvent]:
        return list(self.session.scalars(select(ApplicationEvent).where(ApplicationEvent.application_id == application_id).order_by(ApplicationEvent.created_at)))

    @staticmethod
    def _require_human(human_authorized: bool) -> None:
        if not human_authorized:
            raise ApplicationAuthorizationError("human authorization is required")
