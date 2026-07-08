from datetime import datetime, timedelta

import pytest

from flightdeck.metrics import build_report, minutes_saved
from flightdeck.schemas import Feedback, Run
from tests.conftest import NOW

BASELINE = 12  # support-reply baseline minutes in the fixture org


def _run(run_id: str, when: datetime, **overrides) -> Run:
    fields = {
        "id": run_id,
        "workflow_id": "support-reply",
        "user": "ana",
        "started_at": when,
        "finished_at": when,
        "status": "completed",
        "model_id": "mock-fast-eu",
        "provider": "mock",
        "tokens_in": 1000,
        "tokens_out": 300,
        "cost": 0.02,
        "redactions": 1,
    }
    fields.update(overrides)
    return Run(**fields)


def _feedback(run_id: str, outcome: str, minutes: float | None) -> Feedback:
    return Feedback(run_id=run_id, outcome=outcome, human_minutes=minutes, at=NOW)


class TestMinutesSaved:
    """The savings matrix from docs/metrics.md, rule by rule."""

    def test_hitl_unreviewed_earns_nothing(self, org):
        workflow = org.workflows["support-reply"]
        assert minutes_saved(workflow, _run("r", NOW), None, 2.0) == 0.0

    def test_accepted_with_recorded_minutes(self, org):
        workflow = org.workflows["support-reply"]
        saved = minutes_saved(workflow, _run("r", NOW), _feedback("r", "accepted", 3.0), 2.0)
        assert saved == BASELINE - 3.0

    def test_accepted_without_minutes_assumes_default_review(self, org):
        workflow = org.workflows["support-reply"]
        saved = minutes_saved(workflow, _run("r", NOW), _feedback("r", "accepted", None), 2.0)
        assert saved == BASELINE - 2.0

    def test_rejected_earns_negative_savings(self, org):
        workflow = org.workflows["support-reply"]
        saved = minutes_saved(workflow, _run("r", NOW), _feedback("r", "rejected", 4.0), 2.0)
        assert saved == -4.0

    def test_edit_longer_than_baseline_goes_negative(self, org):
        # Fixing the output took longer than doing the task by hand: the run
        # DESTROYED value and the number must say so.
        workflow = org.workflows["support-reply"]
        saved = minutes_saved(workflow, _run("r", NOW), _feedback("r", "edited", 20.0), 2.0)
        assert saved == BASELINE - 20.0 < 0

    def test_no_run_earns_more_than_its_baseline(self, org):
        workflow = org.workflows["support-reply"]
        saved = minutes_saved(workflow, _run("r", NOW), _feedback("r", "accepted", 0.0), 2.0)
        assert saved == BASELINE

    def test_review_free_workflow_earns_baseline_unreviewed(self, org):
        workflow = org.workflows["support-reply"].model_copy(update={"review": "none"})
        assert minutes_saved(workflow, _run("r", NOW), None, 2.0) == BASELINE

    def test_spot_check_unreviewed_assumes_default_review(self, org):
        workflow = org.workflows["support-reply"].model_copy(update={"review": "spot_check"})
        assert minutes_saved(workflow, _run("r", NOW), None, 2.0) == BASELINE - 2.0

    def test_blocked_and_failed_earn_nothing(self, org):
        workflow = org.workflows["support-reply"]
        blocked = _run("r", NOW, status="blocked", reason="budget", model_id="", provider="")
        assert minutes_saved(workflow, blocked, None, 2.0) == 0.0


class TestBuildReport:
    @pytest.fixture
    def seeded(self, org, store, ledger):
        week1 = NOW - timedelta(days=9)  # complete ISO week before NOW
        week2 = NOW - timedelta(days=2)
        store.add_run(_run("acc", week1, user="ana"))
        store.add_feedback(_feedback("acc", "accepted", 2.0))  # +10 min
        store.add_run(_run("edi", week1, user="bea"))
        store.add_feedback(_feedback("edi", "edited", 6.0))  # +6 min
        store.add_run(_run("rej", week2, user="ana"))
        store.add_feedback(_feedback("rej", "rejected", 3.0))  # −3 min
        store.add_run(_run("pending", week2, user="carl"))  # unreviewed → 0 min
        store.add_run(
            _run("blk", week2, status="blocked", reason="monthly budget exhausted", model_id="", cost=0)
        )
        store.add_run(
            _run("pol", week2, status="blocked", reason="no policy-compliant model", model_id="", cost=0)
        )
        store.add_run(_run("fail", week2, status="failed", reason="provider: timeout", cost=0))
        ledger.append("run_completed", {"run_id": "acc"})
        return build_report(org, store, ledger, days=30, now=NOW)

    def test_hours_value_and_net(self, seeded):
        entry = next(e for e in seeded.workflows if e.workflow_id == "support-reply")
        assert entry.hours_saved == pytest.approx(13 / 60)
        assert entry.value == pytest.approx(13 / 60 * 40)  # org default hourly cost
        assert entry.ai_cost == pytest.approx(0.02 * 4)
        assert entry.net_value == pytest.approx(entry.value - entry.ai_cost)

    def test_review_and_user_counts(self, seeded):
        entry = next(e for e in seeded.workflows if e.workflow_id == "support-reply")
        assert entry.runs_completed == 4
        assert entry.reviewed == 3
        assert (entry.accepted, entry.edited, entry.rejected) == (1, 1, 1)
        assert entry.acceptance_rate == pytest.approx(2 / 3)
        assert entry.active_users == 3

    def test_governance_rollup(self, seeded):
        gov = seeded.governance
        assert gov.blocked_budget == 1
        assert gov.blocked_policy == 1
        assert gov.failed == 1
        assert gov.region_mix == {"eu": 4}
        assert gov.no_training_share == 1.0
        assert gov.ledger_ok and gov.ledger_entries == 1

    def test_health_flags_the_underperformer(self, seeded):
        # acceptance 0.67 vs target 0.80 and ~1.5 weekly actives vs target 6 → worst
        # ratio far below 0.75.
        entry = next(e for e in seeded.workflows if e.workflow_id == "support-reply")
        assert entry.health == "underperforming"

    def test_org_totals_are_sums(self, seeded):
        assert seeded.total_hours_saved == pytest.approx(
            sum(e.hours_saved for e in seeded.workflows)
        )
        assert seeded.total_runs_completed == 4
        assert seeded.active_users == 3

    def test_window_excludes_old_runs(self, org, store, ledger):
        store.add_run(_run("ancient", NOW - timedelta(days=90)))
        report = build_report(org, store, ledger, days=30, now=NOW)
        entry = next(e for e in report.workflows if e.workflow_id == "support-reply")
        assert entry.runs_completed == 0
        assert len(report.weekly) == 1  # …but the trend still sees full history


class TestMonthlyCap:
    def test_runaway_duplicates_cannot_inflate_savings(self, org):
        from flightdeck.metrics import earned_minutes

        # Declared volume: 12 min × 640 tasks/month = 7680 claimable minutes.
        workflow = org.workflows["support-reply"].model_copy(update={"review": "none"})
        org.workflows["support-reply"] = workflow
        runs = [_run(f"r{i}", NOW.replace(day=1) + timedelta(minutes=i)) for i in range(700)]
        earned = earned_minutes(org, runs, {}, 2.0)
        assert sum(earned.values()) == pytest.approx(12 * 640)  # capped at declared volume

    def test_negative_minutes_bypass_the_cap(self, org):
        from flightdeck.metrics import earned_minutes

        runs = [_run("good", NOW), _run("bad", NOW + timedelta(minutes=1))]
        feedback = {
            "good": _feedback("good", "accepted", 2.0),
            "bad": _feedback("bad", "rejected", 5.0),
        }
        earned = earned_minutes(org, runs, feedback, 2.0)
        assert earned["good"] == 10.0
        assert earned["bad"] == -5.0  # wasted review time always counts against
