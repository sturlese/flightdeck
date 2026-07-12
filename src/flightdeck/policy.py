"""The policy engine — governance as code, decided before anything is sent.

Three gates run before a workflow touches a provider, all deterministic:

1. DATA gate  — the workflow's data classification selects which registry models
   may receive its payload (residency, no-training vendors, explicit allowlists).
2. BUDGET gate — the workflow's monthly spend cap, checked against committed
   cost in the store. A capped workflow fails closed, and the block is a ledger
   event — an over-budget pilot is a governance signal, not an outage.
3. REDACTION gate — PII scrubbing on every templated variable (the org data),
   not on the prompt scaffolding. See redact.py.

The engine never mutates state and never calls a model. It returns decisions
with reasons; the runner enforces them and the ledger records them. Keeping
decide/enforce/record separated is what makes each piece testable and the whole
thing explainable to an auditor in one sitting.
"""

from dataclasses import dataclass

from flightdeck.config import Org
from flightdeck.schemas import ModelSpec, Workflow
from flightdeck.store import Store

#: Every budget-gate refusal starts with this prefix. It is the one string the
#: rest of the system may rely on to classify a blocked run (see is_budget_block) —
#: metrics and the demo seeder import it instead of pattern-matching prose.
BUDGET_BLOCK_PREFIX = "monthly budget exhausted"


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str = ""


def is_budget_block(reason: str | None) -> bool:
    """Classify a blocked run's recorded reason: budget gate vs. data-policy gate.
    Lives next to check_budget so the message and its classifier can only change
    together."""
    return bool(reason) and reason.startswith(BUDGET_BLOCK_PREFIX)


def allowed_models(org: Org, workflow: Workflow) -> list[ModelSpec]:
    """Registry models that may receive this workflow's data, before any tier or
    cost consideration. Empty result means the org's policy currently has no
    legal placement for this data class — a configuration fact worth surfacing
    exactly as it is."""
    rule = org.config.policy.data_rules[workflow.data_classification]
    return [spec for spec in org.models.values() if rule.allows(spec)]


def check_budget(org: Org, workflow: Workflow, store: Store, year: int, month: int) -> PolicyDecision:
    cap = workflow.guardrails.monthly_budget or org.config.policy.default_monthly_budget
    if cap is None:
        return PolicyDecision(True)
    spent = store.month_cost(workflow.id, year, month)
    if spent >= cap:
        return PolicyDecision(
            False,
            f"{BUDGET_BLOCK_PREFIX}: {spent:.2f} of {cap:.2f} {org.config.currency} committed",
        )
    return PolicyDecision(True)


def should_redact(org: Org, workflow: Workflow) -> bool:
    if workflow.guardrails.redact_pii is not None:
        return workflow.guardrails.redact_pii
    return org.config.policy.redact_pii_default
