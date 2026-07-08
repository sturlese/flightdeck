"""Quality-tiered model routing — match task complexity to model cost, under policy.

A workflow declares a TIER (fast / balanced / frontier), never a model. The
router picks, deterministically, from the models the policy engine already
cleared for the workflow's data classification:

1. candidates in the requested tier → cheapest wins;
2. none there → escalate UPWARD (balanced, then frontier) — a compliant, more
   capable model beats a refusal, and quality is never silently downgraded;
3. nothing anywhere → fail closed with the reason spelled out. Governance gaps
   surface as blocked runs, not as quiet exceptions to the rules.

"Cheapest" is the per-Mtok input+output sum: crude, stable, and predictable —
routing you can explain to finance in one line. Orgs that outgrow it pin models
by editing the registry, not by adding cleverness here.
"""

from dataclasses import dataclass

from flightdeck.schemas import ModelSpec, Tier

TIER_ORDER: dict[Tier, int] = {"fast": 0, "balanced": 1, "frontier": 2}


@dataclass
class Route:
    spec: ModelSpec
    requested_tier: Tier
    escalated: bool  # True when policy forced a more capable (pricier) tier


class NoRouteError(Exception):
    """No policy-compliant model can serve this workflow. The message is written
    to be pasted into a governance conversation as-is."""


def _price_key(spec: ModelSpec) -> tuple[float, str]:
    return (spec.input_cost_per_mtok + spec.output_cost_per_mtok, spec.id)


def pick(candidates: list[ModelSpec], tier: Tier) -> Route:
    """Route among policy-cleared candidates. Raises NoRouteError when the org's
    registry and policy leave no legal option at the requested tier or above."""
    floor = TIER_ORDER[tier]
    for level in range(floor, max(TIER_ORDER.values()) + 1):
        in_tier = [spec for spec in candidates if TIER_ORDER[spec.tier] == level]
        if in_tier:
            spec = min(in_tier, key=_price_key)
            return Route(spec=spec, requested_tier=tier, escalated=level != floor)
    raise NoRouteError(
        f"no policy-compliant model available in tier '{tier}' or above "
        f"({len(candidates)} model(s) cleared by data policy)"
    )
