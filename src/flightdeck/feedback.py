"""The one place feedback becomes evidence — shared by every entry point.

Recording what a human did with a run's output is the measurement the whole ROI
story rests on, so it must happen the SAME way no matter who triggers it: the
``flightdeck feedback`` command, a Slack Accept/Edited/Reject button, or any
future adapter. This module is that single path — validate the outcome, refuse
unknown runs, write the one store row, and append the one ``feedback_recorded``
ledger event every reader (metrics, dashboard, audit) already expects.

Keeping this in one function is what makes the issue's promise true: a Slack
click lands the identical store row and ledger event as the CLI, because they
call the very same code.
"""

import math
from datetime import datetime

from flightdeck.ledger import Ledger
from flightdeck.schemas import Feedback, Outcome
from flightdeck.store import Store

#: The only legal outcomes — mirrors ``schemas.Outcome`` so a caller can validate
#: before construction without importing pydantic machinery.
VALID_OUTCOMES: tuple[Outcome, ...] = ("accepted", "edited", "rejected")


class FeedbackError(Exception):
    """A recordable-feedback problem a caller can map to an exit code or a message:
    an unknown outcome, or a run that isn't in the store. Distinct from a schema
    ValidationError — these are the two business checks feedback has always made."""


def record_feedback(
    store: Store,
    ledger: Ledger,
    run_id: str,
    outcome: str,
    human_minutes: float | None = None,
    by: str = "",
    note: str = "",
    at: datetime | None = None,
) -> Feedback:
    """Validate, persist, and seal one piece of feedback.

    Raises ``FeedbackError`` when the outcome is not accepted/edited/rejected or
    the run is unknown — the same two guards the CLI has always applied. On
    success the store gets one feedback row and the ledger gets the identical
    ``feedback_recorded`` event the reports read: ``{run_id, outcome,
    human_minutes, by}``. ``human_minutes`` left ``None`` means "not timed" and
    the metrics fall back to the org's conservative ``default_review_minutes``.
    """
    if outcome not in VALID_OUTCOMES:
        raise FeedbackError(f"outcome must be one of: {', '.join(VALID_OUTCOMES)}")
    if store.run(run_id) is None:
        raise FeedbackError(f"unknown run: {run_id}")
    if human_minutes is not None and (not math.isfinite(human_minutes) or human_minutes < 0):
        # Review time can't be negative or non-finite. Catch it here as a business
        # error so every caller (CLI, Slack) gets a clean FeedbackError rather than
        # a pydantic ValidationError leaking past its exit-code handling.
        raise FeedbackError(f"human_minutes must be a non-negative number, got {human_minutes!r}")
    entry = Feedback(
        run_id=run_id,
        outcome=outcome,  # type: ignore[arg-type]
        human_minutes=human_minutes,
        by=by,
        note=note,
        at=at or datetime.now().astimezone(),
    )
    store.add_feedback(entry)
    ledger.append(
        "feedback_recorded",
        {"run_id": run_id, "outcome": outcome, "human_minutes": human_minutes, "by": entry.by},
    )
    return entry
