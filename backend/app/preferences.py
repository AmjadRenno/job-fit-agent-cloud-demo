"""Small read boundary for product preferences.

Candidate evidence remains owned by the evidence/profile path.  Until the
candidate-profile consolidation stage, this module reads only the UI
preference payload already persisted alongside profile versions.
"""

from __future__ import annotations

from sqlalchemy.orm import Session
from backend.app.candidate_profile.service import CandidateProfileService


DEFAULT_MINIMUM_FIT = 60


def minimum_fit_preference(session: Session) -> int:
    """Return the saved display threshold without changing match scores."""
    value = CandidateProfileService(session).preferences().get("minimum_fit")
    return value if isinstance(value, int) else DEFAULT_MINIMUM_FIT
