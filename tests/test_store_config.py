from datetime import UTC, datetime

import pytest
import yaml

from flightdeck.config import ConfigError, load_org
from flightdeck.schemas import Feedback, Run
from tests.conftest import ORG, SUPPORT_WORKFLOW, write_org


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
        "tokens_in": 900,
        "tokens_out": 220,
        "cost": 0.012,
        "latency_ms": 840,
        "redactions": 2,
        "output": "draft text",
    }
    fields.update(overrides)
    return Run(**fields)


def test_run_roundtrip_preserves_every_field(store):
    when = datetime(2026, 7, 1, 9, 30, tzinfo=UTC)
    original = _run("abc123", when, reason=None)
    store.add_run(original)
    assert store.run("abc123") == original


def test_feedback_latest_verdict_wins(store):
    when = datetime(2026, 7, 1, 9, 30, tzinfo=UTC)
    store.add_run(_run("abc123", when))
    store.add_feedback(Feedback(run_id="abc123", outcome="edited", human_minutes=6, at=when))
    store.add_feedback(Feedback(run_id="abc123", outcome="accepted", human_minutes=1, at=when))
    feedback = store.feedback_map()["abc123"]
    assert feedback.outcome == "accepted"
    assert feedback.human_minutes == 1


def test_runs_filters_by_window_and_workflow(store):
    june = datetime(2026, 6, 1, tzinfo=UTC)
    july = datetime(2026, 7, 1, tzinfo=UTC)
    store.add_run(_run("old", june))
    store.add_run(_run("new", july))
    store.add_run(_run("other", july, workflow_id="board-brief"))

    assert {run.id for run in store.runs(since=july)} == {"new", "other"}
    assert [run.id for run in store.runs(workflow_id="support-reply")] == ["old", "new"]


def test_month_cost_sums_only_that_month(store):
    store.add_run(_run("a", datetime(2026, 7, 2, tzinfo=UTC), cost=1.5))
    store.add_run(_run("b", datetime(2026, 7, 20, tzinfo=UTC), cost=2.5))
    store.add_run(_run("c", datetime(2026, 6, 20, tzinfo=UTC), cost=99.0))
    assert store.month_cost("support-reply", 2026, 7) == pytest.approx(4.0)


# ------------------------------------------------------------------ config loading


def test_missing_org_file_suggests_init(tmp_path):
    with pytest.raises(ConfigError, match="flightdeck init"):
        load_org(tmp_path)


def test_unknown_keys_fail_loudly(tmp_path):
    org = dict(ORG)
    org["polcy"] = {}  # typo'd governance block must not be silently ignored
    with pytest.raises(ConfigError, match="polcy"):
        load_org(write_org(tmp_path / "org", org=org))


def test_dangling_use_case_reference_fails(tmp_path):
    workflow = dict(SUPPORT_WORKFLOW)
    workflow["use_case"] = "does-not-exist"
    with pytest.raises(ConfigError, match="does-not-exist"):
        load_org(write_org(tmp_path / "org", workflows=[workflow]))


def test_empty_model_registry_fails(tmp_path):
    root = write_org(tmp_path / "org")
    (root / "models.yaml").write_text(yaml.safe_dump({"models": []}), encoding="utf-8")
    with pytest.raises(ConfigError, match="registry is empty"):
        load_org(root)


def test_invalid_redact_pattern_is_a_loud_config_error(tmp_path):
    # A bad regex must fail at LOAD, naming the org file and the pattern — never
    # at run time, inside the redactor, mid-run.
    org = dict(ORG)
    org["policy"] = {"redact_patterns": ["[unclosed"]}
    with pytest.raises(ConfigError, match=r"flightdeck\.yaml") as excinfo:
        load_org(write_org(tmp_path / "org", org=org))
    assert "[unclosed" in str(excinfo.value)


def test_redact_patterns_load_and_default_empty(tmp_path):
    org = dict(ORG)
    org["policy"] = {"redact_patterns": [r"\bEMP-\d{5}\b"]}
    loaded = load_org(write_org(tmp_path / "org", org=org))
    assert loaded.config.policy.redact_patterns == [r"\bEMP-\d{5}\b"]
    # Absent block → empty list, so redaction behavior is unchanged for old orgs.
    plain = load_org(write_org(tmp_path / "plain"))
    assert plain.config.policy.redact_patterns == []


@pytest.mark.parametrize("bad", [0, -5])
def test_non_positive_default_monthly_budget_fails_loudly(tmp_path, bad):
    # A 0/negative org default is not a looser cap — check_budget's `spent >= cap`
    # is already true at 0 spend, so it fail-closes EVERY uncapped workflow. Like
    # the per-workflow monthly_budget (gt=0), it must be rejected at load, naming
    # the file, never surface as silent org-wide blocking at run time.
    org = dict(ORG)
    org["policy"] = {"default_monthly_budget": bad}
    with pytest.raises(ConfigError, match=r"flightdeck\.yaml"):
        load_org(write_org(tmp_path / "org", org=org))


def test_partial_data_rules_keep_conservative_defaults(tmp_path):
    org = dict(ORG)
    org["policy"] = {"data_rules": {"restricted": {"models": ["mock-frontier-eu"]}}}
    loaded = load_org(write_org(tmp_path / "org", org=org))
    rules = loaded.config.policy.data_rules
    assert rules["restricted"].models == ["mock-frontier-eu"]  # the override took
    assert rules["internal"].forbid_training_vendors  # the default survived
    # …and the OVERRIDDEN class keeps its own conservative guards too: adding an
    # allowlist to 'restricted' must not silently drop its no-training-vendor rule.
    assert rules["restricted"].forbid_training_vendors


def test_partial_override_keeps_the_same_class_training_guard(tmp_path):
    # Tightening one axis of a class (pin a region for 'internal') must not silently
    # drop that class's OTHER conservative guards. Otherwise an override meant to
    # TIGHTEN policy would quietly let internal data reach a training vendor.
    org = dict(ORG)
    org["policy"] = {"data_rules": {"internal": {"regions": ["eu"]}}}
    loaded = load_org(write_org(tmp_path / "org", org=org))
    rule = loaded.config.policy.data_rules["internal"]
    assert rule.regions == ["eu"]  # the override took
    assert rule.forbid_training_vendors is True  # the conservative guard survived


def test_data_rule_ungoverning_must_be_explicit(tmp_path):
    # Un-governing is allowed, but only when written out loud in the org file —
    # an explicit forbid_training_vendors: false is honored (and stays a visible,
    # authored diff), unlike the silent loosening a bare partial override used to do.
    org = dict(ORG)
    org["policy"] = {"data_rules": {"internal": {"forbid_training_vendors": False}}}
    loaded = load_org(write_org(tmp_path / "org", org=org))
    assert loaded.config.policy.data_rules["internal"].forbid_training_vendors is False


def test_eligible_users_falls_back_to_department_headcount(org):
    workflow = org.workflows["support-reply"]
    assert org.eligible_users(workflow) == 12  # Support headcount
    explicit = workflow.model_copy(deep=True, update={"eligible_users": 4})
    assert org.eligible_users(explicit) == 4
