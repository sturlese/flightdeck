"""The runner — one governed execution, recorded whatever happens.

Order of operations is the whole point:

    budget gate → data-policy routing → PII redaction → step calls → evidence

Every path lands in the store AND the ledger: completed runs with cost and
usage, blocked runs with the gate that stopped them, failed runs with the
vendor error. A transformation program learns as much from its blocks and
failures as from its successes — the evidence layer must see all three.

Prompts are templates with ``{{var}}`` placeholders, filled from user-supplied
variables and prior step outputs (``{{steps.<id>}}``). No conditionals, no
loops: a workflow here is a REPEATABLE business task, and repeatability is what
makes its runs comparable and its baseline meaningful. Anything needing real
orchestration belongs in an agent framework — instrumented through an adapter,
not reimplemented here.

Only templated VARIABLES are redacted, never the prompt scaffolding: the org
authored the scaffolding, while the variables carry whatever a user pasted in —
tickets, contracts, CVs — which is exactly where PII lives.
"""

import re
import time
import uuid
from datetime import UTC, datetime

from flightdeck.config import Org
from flightdeck.ledger import Ledger
from flightdeck.policy import allowed_models, check_budget, should_redact
from flightdeck.providers import Provider, ProviderError, get_provider
from flightdeck.redact import redact
from flightdeck.router import NoRouteError, pick
from flightdeck.schemas import Run, Workflow
from flightdeck.store import Store

_PLACEHOLDER = re.compile(r"\{\{\s*([\w.-]+)\s*\}\}")


class VariableError(Exception):
    """User error (missing/unknown variable), distinct from a governance block."""


def render(template: str, context: dict[str, str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            raise VariableError(f"prompt references '{{{{{key}}}}}' but no such variable was provided")
        return context[key]

    return _PLACEHOLDER.sub(_replace, template)


def required_vars(workflow: Workflow) -> list[str]:
    names: list[str] = []
    for step in workflow.steps:
        for var in step.vars:
            if var not in names:
                names.append(var)
    return names


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record(store: Store, ledger: Ledger, run: Run) -> Run:
    store.add_run(run)
    event = {"completed": "run_completed", "blocked": "run_blocked", "failed": "run_failed"}[run.status]
    data = {
        "run_id": run.id,
        "workflow": run.workflow_id,
        "user": run.user,
        "model": run.model_id,
        "tokens_in": run.tokens_in,
        "tokens_out": run.tokens_out,
        "cost": round(run.cost, 6),
        "redactions": run.redactions,
    }
    if run.reason:
        data["reason"] = run.reason
    if run.output is not None:
        # The ledger seals the CONTENT without storing it: the store keeps the
        # output, the chain keeps its fingerprint.
        data["output_sha256"] = _sha256(run.output)
    ledger.append(event, data, at=run.finished_at)
    return run


def execute(
    org: Org,
    workflow: Workflow,
    variables: dict[str, str],
    user: str,
    store: Store,
    ledger: Ledger,
    provider: Provider | None = None,
    now: datetime | None = None,
) -> Run:
    """Run a workflow under policy and record the evidence. ``provider`` and
    ``now`` are injectable for tests and the demo seeder; production callers
    pass neither."""
    started = now or datetime.now(UTC)
    run_id = uuid.uuid4().hex[:12]

    missing = [name for name in required_vars(workflow) if name not in variables]
    if missing:
        raise VariableError(f"missing variable(s): {', '.join(missing)} (pass with --var name=value)")

    def _terminal(status: str, reason: str, **fields: object) -> Run:
        run = Run(
            id=run_id,
            workflow_id=workflow.id,
            user=user,
            started_at=started,
            finished_at=now or datetime.now(UTC),
            status=status,  # type: ignore[arg-type]
            reason=reason,
            **fields,  # type: ignore[arg-type]
        )
        return record(store, ledger, run)

    budget = check_budget(org, workflow, store, started.year, started.month)
    if not budget.allowed:
        return _terminal("blocked", budget.reason)

    try:
        route = pick(allowed_models(org, workflow), workflow.tier)
    except NoRouteError as exc:
        return _terminal("blocked", str(exc))
    spec = route.spec

    redactions = 0
    context = dict(variables)
    if should_redact(org, workflow):
        for key, value in context.items():
            result = redact(value)
            context[key] = result.text
            redactions += result.hits

    tokens_in = tokens_out = 0
    latency_ms = 0
    output = ""
    clock = time.perf_counter()
    try:
        # Resolving the adapter is part of the vendor call: an unknown provider
        # name is a failed run recorded like any other, never an uncaught crash.
        active_provider = provider or get_provider(spec.provider)
        for step in workflow.steps:
            prompt = render(step.prompt, context)
            completion = active_provider.complete(spec, prompt, step.max_output_tokens)
            tokens_in += completion.tokens_in
            tokens_out += completion.tokens_out
            output = completion.text
            context[f"steps.{step.id}"] = completion.text
    except ProviderError as exc:
        latency_ms = int((time.perf_counter() - clock) * 1000)
        return _terminal(
            "failed",
            str(exc),
            model_id=spec.id,
            provider=spec.provider,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=spec.cost(tokens_in, tokens_out),
            latency_ms=latency_ms,
            redactions=redactions,
        )
    latency_ms = int((time.perf_counter() - clock) * 1000)

    run = Run(
        id=run_id,
        workflow_id=workflow.id,
        user=user,
        started_at=started,
        finished_at=now or datetime.now(UTC),
        status="completed",
        model_id=spec.id,
        provider=spec.provider,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=spec.cost(tokens_in, tokens_out),
        latency_ms=latency_ms,
        redactions=redactions,
        output=output,
    )
    return record(store, ledger, run)
