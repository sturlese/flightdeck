"""Due-logic for scheduled, review-free workflows — pure, and the reason a
retry storm is impossible by construction.

flightdeck does NOT reimplement cron. An external scheduler (cron, a CI job)
invokes `flightdeck tick` as often as it likes; `tick` runs each due workflow AT
MOST ONCE per cadence period. The safety property is idempotency per period:

    due-ness is defined by CALENDAR PERIOD, not a rolling window.

- daily   → due unless some run already started on the same calendar date,
- weekly  → due unless some run already started in the same ISO (year, week),
- monthly → due unless some run already started in the same (year, month).

"Some run" means ANY run in the period — completed, blocked OR failed. A
budget-blocked attempt still consumes the period, so a scheduler that calls
`tick` 300 times in an hour runs a daily digest exactly once that day and every
later call sees the period already spent and skips it. The period, not success,
is what gets consumed — that is what makes the demo's week-9 runaway impossible.
"""

from datetime import UTC, datetime

from flightdeck.schemas import Cadence
from flightdeck.store import Store


def _to_utc(moment: datetime) -> datetime:
    """Compare periods in one timezone. Runs are recorded UTC-aware; be defensive
    about a naive timestamp sneaking in from an imported store."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _period_key(cadence: Cadence, moment: datetime) -> tuple[int, ...]:
    """The calendar bucket a moment falls in. Two moments are in the same period
    iff their keys are equal."""
    moment = _to_utc(moment)
    if cadence == "daily":
        return (moment.year, moment.month, moment.day)
    if cadence == "weekly":
        iso = moment.isocalendar()
        return (iso.year, iso.week)
    return (moment.year, moment.month)  # monthly


def is_due(cadence: Cadence, last_started_at: datetime | None, now: datetime) -> bool:
    """Is a workflow with this cadence due at ``now``, given the start time of its
    most recent run (or None if it never ran)? Never-run is always due; otherwise
    due only when ``now`` falls in a later calendar period than the last run."""
    if last_started_at is None:
        return True
    return _period_key(cadence, last_started_at) != _period_key(cadence, now)


def last_run_started_at(store: Store, workflow_id: str) -> datetime | None:
    """The ``started_at`` of the workflow's most recent run, or None. ``runs`` is
    time-ordered, so the last row is the newest attempt — completed, blocked or
    failed, all of which count as "already ticked this period"."""
    runs = store.runs(workflow_id=workflow_id)
    return runs[-1].started_at if runs else None
