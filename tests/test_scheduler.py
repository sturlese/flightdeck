"""Scheduled review-free runs: the due-logic, the schema guard, and the
storm-proof `flightdeck tick` command.

The whole point of this feature is idempotency per cadence period — an external
scheduler may call `tick` any number of times, but a due workflow runs AT MOST
ONCE per period, and even a budget-blocked attempt spends the period. These
tests pin that property down offline and deterministically.
"""

from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from flightdeck.cli import app
from flightdeck.config import ConfigError, load_org
from flightdeck.ledger import Ledger
from flightdeck.scheduler import is_due, runs_started_this_period
from flightdeck.schemas import Run
from flightdeck.store import Store
from tests.conftest import NOW, write_org

runner = CliRunner()


def invoke(*args: str):
    return runner.invoke(app, list(args), env={"COLUMNS": "220"})


# A review-free digest that declares its own inputs: no human passes --var.
DIGEST_WORKFLOW = {
    "id": "daily-digest",
    "name": "Daily community digest",
    "department": "Support",
    "data_classification": "public",
    "tier": "fast",
    "review": "none",
    "baseline": {"minutes_per_task": 20, "tasks_per_month": 30},
    "steps": [{"id": "digest", "prompt": "Summarize:\n{{source}}", "vars": ["source"]}],
    "schedule": {"cadence": "daily", "vars": {"source": "yesterday's channel log"}},
    "guardrails": {"redact_pii": False, "monthly_budget": 50},
}


def _freeze(monkeypatch, when: datetime = NOW) -> None:
    """Pin the CLI's wall clock so its real-time tick is deterministic under test
    (the command itself takes no injectable now, by design)."""

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return when.astimezone(tz) if tz else when

    monkeypatch.setattr("flightdeck.cli.datetime", Clock)


def _blocked_run(workflow_id: str, when: datetime) -> Run:
    return Run(
        id=f"blk-{when.isoformat()}", workflow_id=workflow_id, user="scheduler",
        started_at=when, finished_at=when, status="blocked", reason="test-blocked",
    )


# --------------------------------------------------------------- schema / validator


def test_schedule_on_reviewed_workflow_fails_to_load(tmp_path):
    reviewed = {**DIGEST_WORKFLOW, "id": "bad", "review": "human_in_the_loop"}
    with pytest.raises(ConfigError, match="schedule requires review: none"):
        load_org(write_org(tmp_path / "org", workflows=[reviewed]))


def test_schedule_on_review_none_loads(tmp_path):
    org = load_org(write_org(tmp_path / "org", workflows=[DIGEST_WORKFLOW]))
    schedule = org.workflows["daily-digest"].schedule
    assert schedule is not None
    assert schedule.cadence == "daily"
    assert schedule.vars == {"source": "yesterday's channel log"}


def test_workflow_without_schedule_defaults_to_none(org):
    assert org.workflows["support-reply"].schedule is None


# ------------------------------------------------------------------- due-logic


@pytest.mark.parametrize("cadence", ["daily", "weekly", "monthly"])
def test_never_run_is_always_due(cadence):
    assert is_due(cadence, [], NOW) is True


def test_daily_due_across_day_boundary():
    assert is_due("daily", [NOW - timedelta(days=1)], NOW) is True  # yesterday → due
    assert is_due("daily", [NOW - timedelta(hours=6)], NOW) is False  # earlier today → not due


def test_weekly_due_across_iso_week_boundary():
    # NOW is 2026-07-08 (Wed, ISO week 28). Monday of that week is 2026-07-06.
    assert is_due("weekly", [datetime(2026, 7, 6, tzinfo=UTC)], NOW) is False  # same ISO week
    assert is_due("weekly", [datetime(2026, 7, 5, tzinfo=UTC)], NOW) is True  # previous ISO week


def test_monthly_due_across_month_boundary():
    assert is_due("monthly", [datetime(2026, 7, 1, tzinfo=UTC)], NOW) is False  # same month
    assert is_due("monthly", [datetime(2026, 6, 30, tzinfo=UTC)], NOW) is True  # previous month


def test_naive_timestamp_is_treated_as_utc():
    assert is_due("daily", [NOW.replace(tzinfo=None)], NOW) is False  # same day, tz-normalized


def test_blocked_run_in_period_counts_as_already_ticked(store):
    # Idempotency: a budget-blocked attempt still spends the period, so a storm
    # that only ever blocks cannot keep retrying.
    store.add_run(_blocked_run("daily-digest", NOW))
    assert runs_started_this_period(store, "daily-digest", "daily", NOW) == [NOW]
    assert is_due("daily", runs_started_this_period(store, "daily-digest", "daily", NOW), NOW) is False


def test_a_run_in_another_period_does_not_count_as_this_period(store):
    # A run stamped ahead of now -- clock skew, an imported store, a backfill --
    # is not a run in THIS period, so it must neither spend the period (starving
    # the workflow until that date arrives) nor keep re-triggering it.
    ahead_by = (("daily", timedelta(days=1)), ("weekly", timedelta(weeks=1)), ("monthly", timedelta(days=40)))
    for cadence, ahead in ahead_by:
        assert is_due(cadence, [NOW + ahead], NOW) is True
        assert is_due(cadence, [NOW + ahead, NOW], NOW) is False  # ...but today's run still spends it


def test_a_future_dated_run_neither_storms_nor_starves(store):
    # The trap the newest-row-only reading falls into either way: last row by
    # started_at is the future one, so comparing periods for inequality fires on
    # every tick, and comparing them for order fires on none. Asking the run SET
    # is what makes both answers right.
    store.add_run(_blocked_run("daily-digest", NOW + timedelta(days=1)))

    assert runs_started_this_period(store, "daily-digest", "daily", NOW) == []
    assert is_due("daily", runs_started_this_period(store, "daily-digest", "daily", NOW), NOW) is True

    store.add_run(_blocked_run("daily-digest", NOW))  # now today's period is spent
    assert is_due("daily", runs_started_this_period(store, "daily-digest", "daily", NOW), NOW) is False


def test_runs_started_this_period_is_empty_when_no_runs(store):
    assert runs_started_this_period(store, "daily-digest", "daily", NOW) == []


def test_runs_started_this_period_excludes_earlier_periods(store):
    store.add_run(_blocked_run("daily-digest", NOW - timedelta(days=2)))
    store.add_run(_blocked_run("daily-digest", NOW - timedelta(days=1)))
    assert runs_started_this_period(store, "daily-digest", "daily", NOW) == []
    # ...and the same history one cadence up IS this week's, so the week is spent.
    assert runs_started_this_period(store, "daily-digest", "weekly", NOW) == [
        NOW - timedelta(days=2),
        NOW - timedelta(days=1),
    ]


# ------------------------------------------------------------------- tick command


def test_tick_runs_due_workflow_once_then_is_idempotent(tmp_path, monkeypatch):
    _freeze(monkeypatch)
    root = write_org(tmp_path / "org", workflows=[DIGEST_WORKFLOW])

    first = invoke("tick", "--dir", str(root))
    assert first.exit_code == 0, first.output
    assert "daily-digest: ran" in first.output

    org = load_org(root)
    with Store(org.db_path) as store:
        after_first = store.runs(workflow_id="daily-digest")
    assert len(after_first) == 1
    assert after_first[0].status == "completed"
    assert after_first[0].user == "scheduler"
    assert Ledger(org.ledger_path).entries()[-1]["event"] == "run_completed"

    # The storm-proofing: a second tick in the same period runs it zero more times.
    second = invoke("tick", "--dir", str(root))
    assert second.exit_code == 0, second.output
    assert "skipped (not due this daily)" in second.output
    with Store(org.db_path) as store:
        assert len(store.runs(workflow_id="daily-digest")) == 1


def test_tick_broken_workflow_does_not_starve_later_ones(tmp_path, monkeypatch):
    _freeze(monkeypatch)
    # `aaa-broken` sorts before `daily-digest` and is misconfigured (its schedule
    # omits the required `source` var), so with the old mid-loop abort the healthy
    # digest would never run — its execution depended on an unrelated id's ordering.
    broken = {**DIGEST_WORKFLOW, "id": "aaa-broken", "schedule": {"cadence": "daily", "vars": {}}}
    root = write_org(tmp_path / "org", workflows=[broken, DIGEST_WORKFLOW])

    result = invoke("tick", "--dir", str(root))
    assert result.exit_code == 2  # the config error is still signalled to cron…
    assert "aaa-broken" in result.output

    # …but the healthy, due workflow sorted AFTER the broken one still ran.
    org = load_org(root)
    with Store(org.db_path) as store:
        assert len(store.runs(workflow_id="daily-digest")) == 1


def test_tick_budget_exhausted_records_blocked_and_exits_zero(tmp_path, monkeypatch):
    _freeze(monkeypatch)
    capped = {**DIGEST_WORKFLOW, "guardrails": {"redact_pii": False, "monthly_budget": 1}}
    root = write_org(tmp_path / "org", workflows=[capped])
    org = load_org(root)
    # Exhaust this month's cap on a PRIOR day (same month → still due today).
    with Store(org.db_path) as store:
        store.add_run(
            Run(
                id="seed-cost", workflow_id="daily-digest", user="scheduler",
                started_at=NOW - timedelta(days=1), finished_at=NOW - timedelta(days=1),
                status="completed", model_id="mock-fast-eu", provider="mock", cost=5.0,
            )
        )

    result = invoke("tick", "--dir", str(root))
    assert result.exit_code == 0  # a budget block is a governance signal, not a batch failure
    assert "daily-digest: blocked" in result.output
    assert "budget" in result.output

    with Store(org.db_path) as store:
        latest = store.runs(workflow_id="daily-digest")[-1]
    assert latest.status == "blocked"
    assert Ledger(org.ledger_path).entries()[-1]["event"] == "run_blocked"


def test_tick_dry_run_records_nothing(tmp_path, monkeypatch):
    _freeze(monkeypatch)
    root = write_org(tmp_path / "org", workflows=[DIGEST_WORKFLOW])

    result = invoke("tick", "--dir", str(root), "--dry-run")
    assert result.exit_code == 0
    assert "would run" in result.output

    org = load_org(root)
    with Store(org.db_path) as store:
        assert store.runs(workflow_id="daily-digest") == []


def test_tick_with_no_scheduled_workflows_is_friendly(tmp_path):
    root = write_org(tmp_path / "org")  # default org: no schedules
    result = invoke("tick", "--dir", str(root))
    assert result.exit_code == 0
    assert "no scheduled workflows" in result.output


def test_tick_missing_scheduled_var_is_a_config_error(tmp_path):
    # schedule.vars omits the var the step needs → a loud config error, exit 2.
    broken = {
        **DIGEST_WORKFLOW,
        "id": "broken-digest",
        "schedule": {"cadence": "daily", "vars": {}},
    }
    root = write_org(tmp_path / "org", workflows=[broken])
    result = invoke("tick", "--dir", str(root))
    assert result.exit_code == 2
    assert "config error" in result.output
    assert "source" in result.output  # names the missing var


def test_tick_skips_workflow_already_run_this_period(tmp_path, monkeypatch):
    _freeze(monkeypatch)
    root = write_org(tmp_path / "org", workflows=[DIGEST_WORKFLOW])
    org = load_org(root)
    # A blocked attempt earlier today already spent the period.
    with Store(org.db_path) as store:
        store.add_run(_blocked_run("daily-digest", NOW - timedelta(hours=3)))

    result = invoke("tick", "--dir", str(root))
    assert result.exit_code == 0
    assert "skipped (not due this daily)" in result.output
    with Store(org.db_path) as store:
        assert len(store.runs(workflow_id="daily-digest")) == 1  # no new run added


def test_tick_does_not_storm_on_a_future_dated_run(tmp_path, monkeypatch):
    # The end-to-end promise, with a run stamped tomorrow already in the store: it
    # runs EXACTLY ONCE -- not on every tick (the storm), and not never (silent
    # starvation until tomorrow's stamp comes round).
    _freeze(monkeypatch)
    root = write_org(tmp_path / "org", workflows=[DIGEST_WORKFLOW])
    org = load_org(root)
    with Store(org.db_path) as store:
        store.add_run(_blocked_run("daily-digest", NOW + timedelta(days=1)))

    outputs = []
    for _ in range(3):
        result = invoke("tick", "--dir", str(root))
        assert result.exit_code == 0, result.output
        outputs.append(result.output)

    assert "daily-digest: ran" in outputs[0]  # not starved by the future stamp
    assert all("skipped (not due this daily)" in out for out in outputs[1:])  # and not a storm
    with Store(org.db_path) as store:
        assert len(store.runs(workflow_id="daily-digest")) == 2  # the seeded row + exactly one run
