import hashlib
from datetime import UTC, datetime

import pytest

from flightdeck.providers import Completion, ProviderError
from flightdeck.runner import VariableError, execute, render
from flightdeck.schemas import ModelSpec, Run, Workflow
from tests.conftest import NOW, SUPPORT_WORKFLOW


class FailingProvider:
    def complete(self, spec, prompt, max_output_tokens):
        raise ProviderError("anthropic: 529 overloaded")


class EchoProvider:
    """Returns the prompt itself — lets tests assert on redaction and chaining."""

    def complete(self, spec: ModelSpec, prompt: str, max_output_tokens: int) -> Completion:
        return Completion(text=f"ECHO::{prompt}", tokens_in=100, tokens_out=50)


def test_completed_run_records_evidence_and_seals_output(org, store, ledger):
    workflow = org.workflows["support-reply"]
    run = execute(org, workflow, {"ticket": "Player cannot log in"}, "ana", store, ledger, now=NOW)

    assert run.status == "completed"
    assert run.model_id == "mock-fast-eu"
    assert run.tokens_in > 0 and run.tokens_out > 0 and run.cost > 0
    assert store.run(run.id) == run

    entries = ledger.entries()
    assert entries[-1]["event"] == "run_completed"
    sealed = entries[-1]["data"]["output_sha256"]
    assert sealed == hashlib.sha256(run.output.encode()).hexdigest()
    assert ledger.verify().ok


def test_variables_are_redacted_before_leaving(org, store, ledger):
    workflow = org.workflows["support-reply"]
    ticket = "Refund maria@example.com, card 4111 1111 1111 1111"
    run = execute(org, workflow, {"ticket": ticket}, "ana", store, ledger, provider=EchoProvider(), now=NOW)

    assert run.redactions == 2
    assert "maria@example.com" not in run.output
    assert "4111 1111 1111 1111" not in run.output
    assert "[REDACTED:email]" in run.output


def test_prompt_scaffolding_is_not_redacted(org, store, ledger):
    # The scaffolding may legitimately mention e.g. a support address; only pasted
    # variables are scrubbed.
    workflow = org.workflows["support-reply"].model_copy(deep=True)
    workflow.steps[0].prompt = "Sign as help@testco.example. Ticket: {{ticket}}"
    run = execute(org, workflow, {"ticket": "hello"}, "ana", store, ledger, provider=EchoProvider(), now=NOW)
    assert "help@testco.example" in run.output


def test_budget_exhaustion_blocks_and_lands_in_both_records(org, store, ledger):
    workflow = org.workflows["support-reply"]  # cap 50
    store.add_run(
        Run(
            id="prior",
            workflow_id=workflow.id,
            user="ana",
            started_at=NOW,
            finished_at=NOW,
            status="completed",
            cost=55.0,
        )
    )
    run = execute(org, workflow, {"ticket": "hi"}, "ana", store, ledger, now=NOW)
    assert run.status == "blocked"
    assert "budget" in run.reason
    assert ledger.entries()[-1]["event"] == "run_blocked"


def test_restricted_workflow_fails_closed(org, store, ledger):
    workflow = org.workflows["board-brief"]  # restricted: default allowlist is empty
    run = execute(org, workflow, {"topic": "H1 results"}, "cfo", store, ledger, now=NOW)
    assert run.status == "blocked"
    assert "no policy-compliant model" in run.reason


def test_provider_failure_is_a_failed_run_not_a_crash(org, store, ledger):
    workflow = org.workflows["support-reply"]
    run = execute(org, workflow, {"ticket": "hi"}, "ana", store, ledger, provider=FailingProvider(), now=NOW)
    assert run.status == "failed"
    assert "529" in run.reason
    assert ledger.entries()[-1]["event"] == "run_failed"


def test_unknown_provider_is_a_failed_run_not_a_crash(tmp_path):
    # A model whose provider names no registered adapter is valid, routable config
    # (ModelSpec.provider is an unconstrained str). Resolving it must fail like any
    # vendor error — recorded in the store AND the ledger — not crash execute().
    from flightdeck.config import load_org
    from flightdeck.ledger import Ledger
    from flightdeck.store import Store
    from tests.conftest import write_org

    models = [
        {
            "id": "rogue-fast-eu", "provider": "cohere", "model": "command",
            "tier": "fast", "input_cost_per_mtok": 0.01, "output_cost_per_mtok": 0.02,
            "region": "eu", "trains_on_data": False,
        }
    ]
    org = load_org(write_org(tmp_path / "org", models=models, workflows=[SUPPORT_WORKFLOW]))
    with Store(org.db_path) as store:
        ledger = Ledger(org.ledger_path)
        run = execute(org, org.workflows["support-reply"], {"ticket": "hi"}, "ana", store, ledger, now=NOW)

        assert run.status == "failed"  # currently RAISES ProviderError before the fix
        assert "cohere" in run.reason
        assert run.model_id == "rogue-fast-eu"
        assert store.run(run.id).status == "failed"  # recorded in the store …
    assert ledger.entries()[-1]["event"] == "run_failed"  # … and the ledger


def test_missing_variable_is_a_user_error(org, store, ledger):
    workflow = org.workflows["support-reply"]
    with pytest.raises(VariableError, match="ticket"):
        execute(org, workflow, {}, "ana", store, ledger, now=NOW)
    assert store.runs() == []  # user errors are not governance events


def test_steps_chain_through_context(org, store, ledger):
    workflow = Workflow.model_validate(
        {
            **SUPPORT_WORKFLOW,
            "id": "chained",
            "steps": [
                {"id": "draft", "prompt": "Draft: {{ticket}}", "vars": ["ticket"]},
                {"id": "polish", "prompt": "Polish this: {{steps.draft}}", "vars": []},
            ],
        }
    )
    run = execute(org, workflow, {"ticket": "hello"}, "ana", store, ledger, provider=EchoProvider(), now=NOW)
    assert run.output.startswith("ECHO::Polish this: ECHO::Draft: hello")
    assert run.tokens_in == 200  # both steps metered


def test_render_rejects_unknown_placeholder():
    with pytest.raises(VariableError, match="nope"):
        render("Hello {{nope}}", {})


def test_run_timestamps_are_timezone_aware(org, store, ledger):
    workflow = org.workflows["support-reply"]
    run = execute(org, workflow, {"ticket": "hi"}, "ana", store, ledger)
    assert run.started_at.tzinfo is not None
    assert run.started_at.utcoffset().total_seconds() == 0
    assert isinstance(run.started_at, datetime) and run.started_at.tzinfo == UTC
