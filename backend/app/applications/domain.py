from __future__ import annotations

from enum import StrEnum


class ApplicationState(StrEnum):
    DISCOVERED = "DISCOVERED"
    INTERESTED = "INTERESTING"
    TO_APPLY = "TO_APPLY"
    APPLIED = "APPLIED"
    INTERVIEW = "INTERVIEW"
    REJECTED = "REJECTED"
    OFFER = "OFFER"
    CLOSED = "CLOSED"
    IGNORED = "IGNORED"


VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "DISCOVERED": frozenset({"INTERESTING", "TO_APPLY", "IGNORED"}),
    "INTERESTING": frozenset({"TO_APPLY", "IGNORED"}),
    "TO_APPLY": frozenset({"APPLIED", "IGNORED"}),
    "APPLIED": frozenset({"INTERVIEW", "REJECTED", "CLOSED"}),
    "INTERVIEW": frozenset({"OFFER", "REJECTED", "CLOSED"}),
    "REJECTED": frozenset({"CLOSED"}),
    "OFFER": frozenset({"CLOSED"}),
    "CLOSED": frozenset(),
    "IGNORED": frozenset({"DISCOVERED"}),
}


class ApplicationTransitionError(ValueError):
    pass


def validate_transition(current: str, target: str) -> None:
    if target not in ApplicationState._value2member_map_:
        raise ApplicationTransitionError(f"unsupported application state: {target}")
    if current == target:
        return
    if target not in VALID_TRANSITIONS.get(current, frozenset()):
        raise ApplicationTransitionError(f"invalid transition: {current} -> {target}")
