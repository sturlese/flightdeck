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

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

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


def _period_start(cadence: Cadence, moment: datetime) -> datetime:
    """The first instant of the calendar period ``moment`` falls in — the SQL
    bound that keeps the due-check proportional to this period's runs rather than
    to the whole history."""
    day = _to_utc(moment).replace(hour=0, minute=0, second=0, microsecond=0)
    if cadence == "daily":
        return day
    if cadence == "weekly":
        return day - timedelta(days=day.isoweekday() - 1)  # back to Monday
    return day.replace(day=1)  # monthly


def is_due(cadence: Cadence, started_ats: Iterable[datetime], now: datetime) -> bool:
    """Is a workflow with this cadence due at ``now``, given the start times of
    its runs? Due unless SOME run already started in now's calendar period.

    That is the rule docs/governance.md states, and asking it of the run SET
    rather than of the newest row alone is what makes it hold in both directions.
    Consulting only the newest row leaves two failures, because a run can carry a
    timestamp ahead of now — clock skew, an imported store, a backfill: comparing
    the periods for inequality reports the workflow due on every tick until that
    period arrives (the storm this module exists to prevent), and comparing them
    for order reports it due on none of them, starving the workflow silently for
    as long as the bad stamp is in the future. A run in some other period, past or
    future, simply is not a run in this one.
    """
    key = _period_key(cadence, now)
    return not any(_period_key(cadence, started_at) == key for started_at in started_ats)


def runs_started_this_period(store: Store, workflow_id: str, cadence: Cadence, now: datetime) -> list[datetime]:
    """The start times ``is_due`` needs: this workflow's runs that started in
    ``now``'s calendar period — completed, blocked or failed, all of which count
    as "already ticked this period". The SQL bound trims the history to the
    period's first instant; the key check then drops anything stamped beyond its
    last one, which a skewed or imported row can be."""
    key = _period_key(cadence, now)
    since = _period_start(cadence, now)
    return [
        run.started_at
        for run in store.runs(since=since, workflow_id=workflow_id)
        if _period_key(cadence, run.started_at) == key
    ]
