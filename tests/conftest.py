"""Shared fixtures: a small but complete org directory, loaded for real.

Tests exercise the same loading path users hit (YAML on disk → load_org), so a
schema regression that would break a real org breaks the suite too.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from flightdeck.config import Org, load_org
from flightdeck.ledger import Ledger
from flightdeck.store import Store

NOW = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)

MODELS = [
    {
        "id": "mock-fast-eu",
        "provider": "mock",
        "model": "mock-small",
        "tier": "fast",
        "input_cost_per_mtok": 0.5,
        "output_cost_per_mtok": 1.5,
        "region": "eu",
        "trains_on_data": False,
    },
    {
        "id": "mock-balanced-eu",
        "provider": "mock",
        "model": "mock-mid",
        "tier": "balanced",
        "input_cost_per_mtok": 3.0,
        "output_cost_per_mtok": 15.0,
        "region": "eu",
        "trains_on_data": False,
    },
    {
        "id": "mock-frontier-eu",
        "provider": "mock",
        "model": "mock-big",
        "tier": "frontier",
        "input_cost_per_mtok": 5.0,
        "output_cost_per_mtok": 25.0,
        "region": "eu",
        "trains_on_data": False,
    },
    {
        # Cheapest fast model on paper — but the vendor trains on the data, so the
        # default policy must keep internal+ workflows away from it.
        "id": "mock-trainer-us",
        "provider": "mock",
        "model": "mock-trainer",
        "tier": "fast",
        "input_cost_per_mtok": 0.1,
        "output_cost_per_mtok": 0.2,
        "region": "us",
        "trains_on_data": True,
    },
]

SUPPORT_WORKFLOW = {
    "id": "support-reply",
    "name": "Support reply drafting",
    "department": "Support",
    "owner": "ana",
    "data_classification": "internal",
    "tier": "fast",
    "review": "human_in_the_loop",
    "baseline": {"minutes_per_task": 12, "tasks_per_month": 640},
    "steps": [
        {
            "id": "draft",
            "prompt": "Draft a reply to this ticket:\n{{ticket}}",
            "vars": ["ticket"],
            "max_output_tokens": 400,
        }
    ],
    "guardrails": {"redact_pii": True, "monthly_budget": 50},
    "success": {"acceptance_target": 0.8, "weekly_active_users_target": 6},
}

RESTRICTED_WORKFLOW = {
    "id": "board-brief",
    "name": "Board brief drafting",
    "department": "Finance",
    "data_classification": "restricted",
    "tier": "frontier",
    "baseline": {"minutes_per_task": 90, "tasks_per_month": 8},
    "steps": [{"id": "draft", "prompt": "Draft: {{topic}}", "vars": ["topic"]}],
}

ORG = {
    "name": "TestCo",
    "currency": "EUR",
    "default_hourly_cost": 40.0,
    "default_review_minutes": 2.0,
    "departments": [
        {"name": "Support", "headcount": 12},
        {"name": "Finance", "headcount": 5},
    ],
}


def write_org(
    root: Path,
    org: dict | None = None,
    models: list | None = None,
    workflows: list | None = None,
    directory: dict | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "flightdeck.yaml").write_text(yaml.safe_dump(org or ORG), encoding="utf-8")
    (root / "models.yaml").write_text(yaml.safe_dump({"models": models or MODELS}), encoding="utf-8")
    workflows = workflows if workflows is not None else [SUPPORT_WORKFLOW, RESTRICTED_WORKFLOW]
    if workflows:
        (root / "workflows").mkdir(exist_ok=True)
        for workflow in workflows:
            path = root / "workflows" / f"{workflow['id']}.yaml"
            path.write_text(yaml.safe_dump(workflow), encoding="utf-8")
    # Opt-in only: without a `directory` argument no directory.yaml is written, so
    # the default org is byte-for-byte what it was before the feature existed.
    if directory is not None:
        (root / "directory.yaml").write_text(yaml.safe_dump(directory), encoding="utf-8")
    return root


@pytest.fixture
def org(tmp_path: Path) -> Org:
    return load_org(write_org(tmp_path / "org"))


@pytest.fixture
def store(org) -> Store:
    with Store(org.db_path) as opened:
        yield opened


@pytest.fixture
def ledger(org) -> Ledger:
    return Ledger(org.ledger_path)
