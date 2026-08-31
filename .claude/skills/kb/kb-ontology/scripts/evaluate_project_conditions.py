"""Evaluate explicit institutional conditions without predicting future events."""

from __future__ import annotations


_CONDITION_STATUSES = frozenset({"satisfied", "not_satisfied", "unknown"})


def evaluate_preconditions(required: list[str], states: dict[str, str]) -> dict:
    """Return availability from explicit condition states and omitted-state uncertainty."""
    for condition, status in states.items():
        if status not in _CONDITION_STATUSES:
            raise ValueError(f"unsupported condition status for {condition!r}: {status!r}")

    satisfied: list[str] = []
    not_satisfied: list[str] = []
    unknown: list[str] = []
    for condition in required:
        status = states.get(condition, "unknown")
        if status == "satisfied":
            satisfied.append(condition)
        elif status == "not_satisfied":
            not_satisfied.append(condition)
        else:
            unknown.append(condition)

    if not_satisfied:
        availability = "not_yet_available"
    elif unknown:
        availability = "status_unknown"
    else:
        availability = "legally_available"

    return {
        "availability": availability,
        "satisfied": sorted(satisfied),
        "notSatisfied": sorted(not_satisfied),
        "unknown": sorted(unknown),
    }


def derive_execution_status(
    event_types: set[str], reported_step_nm: str | None = None
) -> str:
    """Derive lifecycle status only from explicit completion event types."""
    del reported_step_nm
    if "completion" in event_types:
        return "completed"
    if "partialCompletion" in event_types:
        return "partiallyCompleted"
    return "statusUnknown"
