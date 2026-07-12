from datetime import UTC, datetime, timedelta

import pytest

from flightdeck.metrics import build_report, minutes_saved, monthly_statement
from flightdeck.schemas import Feedback, Run, SuccessCriteria
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

    def test_window_governance_counts_orphaned_workflow_incidents(self, org, store, ledger):
        # A workflow deleted from config leaves its historical runs behind. In-window
        # governance incidents for it still belong in the window rollup — the all-time
        # counters already include them, so the window counters must too.
        when = NOW - timedelta(days=1)
        store.add_run(_run("gb", when, workflow_id="deleted-wf", status="blocked",
                           reason="monthly budget exhausted", model_id="", cost=0))
        store.add_run(_run("gp", when, workflow_id="deleted-wf", status="blocked",
                           reason="no policy-compliant model", model_id="", cost=0))
        store.add_run(_run("gf", when, workflow_id="deleted-wf", status="failed",
                           reason="provider: timeout", model_id="", cost=0))
        gov = build_report(org, store, ledger, days=30, now=NOW).governance
        # Window counters now match the all-time counters for these in-window incidents.
        assert gov.blocked_budget == gov.blocked_budget_all == 1
        assert gov.blocked_policy == gov.blocked_policy_all == 1
        assert gov.failed == 1

    def test_policy_block_mentioning_budget_is_not_a_budget_block(self, org, store, ledger):
        # Classification keys on the budget gate's message PREFIX, not on the word
        # "budget" appearing anywhere — a policy refusal that happens to mention a
        # budget-ish workflow or model name must not shift the governance counters.
        when = NOW - timedelta(days=1)
        store.add_run(_run(
            "pol-b", when, status="blocked", model_id="", cost=0,
            reason="no policy-compliant model available in tier 'fast' or above "
                   "(0 model(s) cleared by data policy) for 'budget-forecasting'",
        ))
        gov = build_report(org, store, ledger, days=30, now=NOW).governance
        assert gov.blocked_policy == gov.blocked_policy_all == 1
        assert gov.blocked_budget == gov.blocked_budget_all == 0

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

    def test_zero_acceptance_target_is_met_not_a_crash(self, org, store, ledger):
        # acceptance_target: 0 is schema-legal (ge=0). A reviewed run must not crash
        # build_report with a ZeroDivisionError — a 0 bar is cleared by any rate.
        wf = org.workflows["support-reply"].model_copy(
            update={"success": SuccessCriteria(acceptance_target=0.0)}
        )
        org.workflows["support-reply"] = wf
        store.add_run(_run("r", NOW - timedelta(days=2)))
        store.add_feedback(_feedback("r", "accepted", 2.0))
        report = build_report(org, store, ledger, days=30, now=NOW)  # raised before the fix
        entry = next(e for e in report.workflows if e.workflow_id == "support-reply")
        assert entry.acceptance_rate == 1.0
        assert entry.health == "healthy"

    def test_zero_acceptance_target_does_not_mask_a_failing_second_target(self, org, store, ledger):
        # A 0 acceptance bar is "met", but must not hide a failing weekly-actives target.
        wf = org.workflows["support-reply"].model_copy(
            update={"success": SuccessCriteria(acceptance_target=0.0, weekly_active_users_target=6)}
        )
        org.workflows["support-reply"] = wf
        store.add_run(_run("r", NOW - timedelta(days=9), user="ana"))  # ~1 weekly active vs target 6
        store.add_feedback(_feedback("r", "accepted", 2.0))
        report = build_report(org, store, ledger, days=30, now=NOW)
        entry = next(e for e in report.workflows if e.workflow_id == "support-reply")
        assert entry.health == "underperforming"


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


class TestMonthlyStatement:
    """The finance export: one row per (workflow, month), same formulas as the dashboard."""

    def test_one_row_per_workflow_month_ties_out_to_build_report(self, org, store, ledger):
        # Two calendar months of activity for support-reply; board-brief has none.
        jun = datetime(2026, 6, 15, 12, tzinfo=UTC)
        jul_a = datetime(2026, 7, 5, 9, tzinfo=UTC)
        jul_b = datetime(2026, 7, 5, 10, tzinfo=UTC)
        store.add_run(_run("jun", jun))
        store.add_feedback(_feedback("jun", "accepted", 2.0))  # +10 min in June
        store.add_run(_run("jula", jul_a))
        store.add_feedback(_feedback("jula", "accepted", 3.0))  # +9 min in July
        store.add_run(_run("julb", jul_b))  # completed but unreviewed → 0 min

        rows = monthly_statement(org, store)
        # Only months with runs, sorted by (workflow_id, month); no board-brief row.
        assert [(r.workflow_id, r.month) for r in rows] == [
            ("support-reply", "2026-06"),
            ("support-reply", "2026-07"),
        ]
        july = next(r for r in rows if r.month == "2026-07")
        assert july.workflow_name == "Support reply drafting"
        assert july.department == "Support"
        assert july.currency == "EUR"
        assert july.runs_completed == 2
        assert july.reviewed == 1
        assert july.reviewed_pct == pytest.approx(0.5)

        # A window covering ONLY July must reproduce July's hours/value/net exactly —
        # same earned_minutes, same hourly cost, so the CSV ties out to the dashboard.
        window = build_report(org, store, ledger, days=19, now=datetime(2026, 7, 20, 12, tzinfo=UTC))
        entry = next(e for e in window.workflows if e.workflow_id == "support-reply")
        assert entry.runs_completed == 2  # only the two July runs fall in this window
        assert july.hours_saved == pytest.approx(entry.hours_saved)
        assert july.value == pytest.approx(entry.value)
        assert july.net == pytest.approx(entry.net_value)

    def test_reviewed_pct_is_zero_without_completed_runs(self, org, store):
        # A month with only a blocked run: it exists (there was a run) but earns and
        # reviews nothing, so reviewed_pct must be 0, not a division by zero.
        when = datetime(2026, 3, 4, 8, tzinfo=UTC)
        store.add_run(_run("blk", when, status="blocked", reason="budget", model_id="", cost=0))
        rows = monthly_statement(org, store)
        row = next(r for r in rows if r.month == "2026-03")
        assert row.runs_completed == 0
        assert row.reviewed_pct == 0.0
        assert row.hours_saved == 0.0
        assert row.ai_cost == 0.0

    def test_hours_saved_capped_at_declared_monthly_volume(self, org, store):
        # Many completed runs in ONE month, far past the declared task volume:
        # hours_saved caps at minutes_per_task × tasks_per_month / 60 (see earned_minutes).
        workflow = org.workflows["support-reply"].model_copy(update={"review": "none"})
        org.workflows["support-reply"] = workflow
        base = datetime(2026, 5, 1, 8, tzinfo=UTC)
        for i in range(700):  # 700 × 12 = 8400 raw min, but the cap is 12 × 640 = 7680
            store.add_run(_run(f"cap{i}", base + timedelta(minutes=i)))
        row = next(r for r in monthly_statement(org, store) if r.month == "2026-05")
        assert row.runs_completed == 700
        assert row.hours_saved == pytest.approx(12 * 640 / 60)  # capped at declared volume
        assert row.value == pytest.approx(12 * 640 / 60 * 40)  # org default hourly cost
        assert row.net == pytest.approx(row.value - row.ai_cost)
