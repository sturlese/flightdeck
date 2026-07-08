"""Backlog scoring — where to point the AI program next, with arithmetic you can argue with.

The score is monthly value discounted by feasibility and risk, divided by
effort. Each factor is a business conversation frozen into a number:

    monthly_value  = task_minutes/60 × tasks_per_month × hourly_cost × automation_potential
    feasibility    = (data_readiness + process_stability) / 10          → 0.2 … 1.0
    risk_discount  = 1 − (risk − 1) × 0.15                              → 1.0 … 0.4
    score          = monthly_value × feasibility × risk_discount / max(effort_weeks, 0.5)

This is a prioritization aid, not an oracle: its job is to make the ranking
DEBATE explicit (why is data_readiness a 2? says who?) and to keep pet projects
honest. The inputs live in usecases.yaml under version control, so every score
change has an author and a diff.
"""

from dataclasses import dataclass

from flightdeck.config import Org
from flightdeck.schemas import UseCase


@dataclass
class ScoredUseCase:
    case: UseCase
    monthly_value: float
    feasibility: float
    risk_discount: float
    score: float


def score(org: Org, case: UseCase) -> ScoredUseCase:
    hourly = case.hourly_cost or org.config.default_hourly_cost
    monthly_value = case.task_minutes / 60 * case.tasks_per_month * hourly * case.automation_potential
    feasibility = (case.data_readiness + case.process_stability) / 10
    risk_discount = 1 - (case.risk - 1) * 0.15
    return ScoredUseCase(
        case=case,
        monthly_value=monthly_value,
        feasibility=feasibility,
        risk_discount=risk_discount,
        score=monthly_value * feasibility * risk_discount / max(case.effort_weeks, 0.5),
    )


def ranked(org: Org, include_done: bool = False) -> list[ScoredUseCase]:
    """Backlog by descending score. Live and killed use cases are excluded by
    default — they are outcomes, not options."""
    cases = [
        case
        for case in org.usecases.values()
        if include_done or case.status in ("candidate", "piloting")
    ]
    return sorted((score(org, case) for case in cases), key=lambda item: item.score, reverse=True)
