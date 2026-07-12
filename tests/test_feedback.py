"""The shared feedback path — one function, one store row, one ledger event.

Every entry point (the CLI, the Slack adapter) funnels through
``record_feedback``; these tests pin its contract so the promise "same feedback
API and ledger events" is enforced at the source.
"""

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
