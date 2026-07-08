from datetime import UTC, datetime

import pytest

from flightdeck.policy import allowed_models, check_budget, should_redact
from flightdeck.router import NoRouteError, pick
from flightdeck.schemas import Run
from tests.conftest import NOW


def test_internal_data_never_reaches_training_vendors(org):
    workflow = org.workflows["support-reply"]
    cleared = {spec.id for spec in allowed_models(org, workflow)}
    assert "mock-trainer-us" not in cleared  # cheapest model, but the vendor trains on data
    assert "mock-fast-eu" in cleared


def test_restricted_fails_closed_without_explicit_allowlist(org):
    workflow = org.workflows["board-brief"]
    assert allowed_models(org, workflow) == []  # default rule: explicit models only


def test_router_picks_cheapest_in_tier(org):
    workflow = org.workflows["support-reply"]
    route = pick(allowed_models(org, workflow), workflow.tier)
    assert route.spec.id == "mock-fast-eu"
    assert not route.escalated


def test_router_escalates_upward_when_tier_is_empty(org):
    workflow = org.workflows["support-reply"]
    candidates = [spec for spec in allowed_models(org, workflow) if spec.tier != "fast"]
    route = pick(candidates, "fast")
    assert route.spec.tier == "balanced"  # up, never down
    assert route.escalated


def test_router_fails_closed_with_actionable_message(org):
    with pytest.raises(NoRouteError, match="no policy-compliant model"):
        pick([], "frontier")


def _run(cost: float, when: datetime) -> Run:
    return Run(
        id=f"r{cost}",
        workflow_id="support-reply",
        user="ana",
        started_at=when,
        finished_at=when,
        status="completed",
        cost=cost,
    )


def test_budget_gate_blocks_once_cap_is_committed(org, store):
    workflow = org.workflows["support-reply"]  # cap: 50/month
    store.add_run(_run(49.0, NOW))
    assert check_budget(org, workflow, store, NOW.year, NOW.month).allowed

    store.add_run(_run(2.0, NOW))
    decision = check_budget(org, workflow, store, NOW.year, NOW.month)
    assert not decision.allowed
    assert "budget" in decision.reason


def test_budget_gate_ignores_other_months(org, store):
    workflow = org.workflows["support-reply"]
    store.add_run(_run(500.0, datetime(2026, 6, 30, tzinfo=UTC)))  # last month's spend
    assert check_budget(org, workflow, store, NOW.year, NOW.month).allowed


def test_workflow_redaction_override_beats_policy_default(org):
    workflow = org.workflows["support-reply"]
    assert should_redact(org, workflow)  # explicit true
    relaxed = workflow.model_copy(deep=True)
    relaxed.guardrails.redact_pii = False
    assert not should_redact(org, relaxed)
    unset = workflow.model_copy(deep=True)
    unset.guardrails.redact_pii = None
    assert should_redact(org, unset)  # falls back to policy default (true)
