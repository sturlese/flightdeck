"""The shared feedback path — one function, one store row, one ledger event.

Every entry point (the CLI, the Slack adapter) funnels through
``record_feedback``; these tests pin its contract so the promise "same feedback
API and ledger events" is enforced at the source.
"""

from datetime import datetime, timedelta, timezone

import pytest

from flightdeck.feedback import FeedbackError, record_feedback
from flightdeck.runner import execute
from tests.conftest import NOW


def _seed_run(org, store, ledger):
    return execute(
        org, org.workflows["support-reply"], {"ticket": "cannot log in"}, "ana", store, ledger, now=NOW
    )


def test_record_feedback_writes_store_row_and_ledger_event(org, store, ledger):
    run = _seed_run(org, store, ledger)
    entry = record_feedback(store, ledger, run.id, "accepted", human_minutes=1.5, by="ana", note="ok")

    assert entry.run_id == run.id and entry.outcome == "accepted" and entry.human_minutes == 1.5

    stored = store.feedback_map()[run.id]
    assert stored.outcome == "accepted"
    assert stored.by == "ana"
    assert stored.human_minutes == 1.5
    assert stored.note == "ok"

    event = ledger.entries()[-1]
    assert event["event"] == "feedback_recorded"
    assert event["data"] == {"run_id": run.id, "outcome": "accepted", "human_minutes": 1.5, "by": "ana"}
    assert ledger.verify().ok


def test_record_feedback_defaults_minutes_to_none(org, store, ledger):
    run = _seed_run(org, store, ledger)
    entry = record_feedback(store, ledger, run.id, "edited")
    assert entry.human_minutes is None
    assert ledger.entries()[-1]["data"]["human_minutes"] is None


def test_record_feedback_rejects_unknown_outcome(org, store, ledger):
    with pytest.raises(FeedbackError, match="outcome must be one of"):
        record_feedback(store, ledger, "irrelevant", "loved-it")


def test_record_feedback_rejects_unknown_run(org, store, ledger):
    with pytest.raises(FeedbackError, match="unknown run"):
        record_feedback(store, ledger, "does-not-exist", "accepted")


@pytest.mark.parametrize("bad", [-5.0, float("nan"), float("inf")])
def test_record_feedback_rejects_negative_or_non_finite_minutes(org, store, ledger, bad):
    # Review time can't be negative or non-finite; the guard must be a clean
    # FeedbackError, not a pydantic ValidationError leaking past the caller's
    # exit-code handling.
    run = _seed_run(org, store, ledger)
    with pytest.raises(FeedbackError, match="non-negative number"):
        record_feedback(store, ledger, run.id, "accepted", human_minutes=bad)


def test_backdated_feedback_seals_the_event_time_in_the_ledger(org, store, ledger):
    # A review imported or backfilled from another system carries its own time.
    # The store row and the ledger entry describe the SAME event, so they must not
    # disagree about when it happened -- the ledger is the artifact an auditor
    # reads, and runner.record already keeps this contract (at=run.finished_at).
    run = _seed_run(org, store, ledger)
    when = NOW - timedelta(days=200)

    entry = record_feedback(store, ledger, run.id, "accepted", human_minutes=3, by="ana", at=when)
    sealed = [e for e in ledger.entries() if e["event"] == "feedback_recorded"][-1]

    assert entry.at == when
    assert sealed["at"] == when.isoformat()  # was the wall clock, months adrift
    assert store.feedback_map()[run.id].at.isoformat() == sealed["at"]


def test_feedback_without_an_explicit_time_still_seals_what_the_row_says(org, store, ledger):
    # The default path must stay consistent too: whatever "now" the row got is the
    # one the chain seals, not a second clock reading taken a moment later.
    run = _seed_run(org, store, ledger)

    entry = record_feedback(store, ledger, run.id, "edited", human_minutes=2, by="ana")
    sealed = [e for e in ledger.entries() if e["event"] == "feedback_recorded"][-1]

    # Compare instants, not strings: the row's default carries the local offset,
    # the ledger is normalized to UTC, and both name the same moment.
    assert datetime.fromisoformat(sealed["at"]) == entry.at
    assert ledger.verify().ok  # and the chain still walks clean


def test_ledger_stays_utc_whatever_offset_the_caller_hands_in(org, store, ledger):
    # `audit tail` renders entry["at"][:16] -- the offset is sliced off before the
    # reader sees it. So every entry in the file has to share one convention, or a
    # review shows up hours before the run it reviews. The runner and the demo
    # seeder both write UTC; feedback must not be the one exception.
    run = _seed_run(org, store, ledger)
    tokyo = timezone(timedelta(hours=9))

    record_feedback(store, ledger, run.id, "accepted", by="ana", at=NOW.astimezone(tokyo))
    stamps = [e["at"] for e in ledger.entries()]

    assert all(s.endswith("+00:00") for s in stamps), stamps
    # ...and the feedback still cannot predate the run it attests to.
    events = {e["event"]: e["at"] for e in ledger.entries()}
    assert events["feedback_recorded"] >= events["run_completed"]
