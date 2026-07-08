"""KPIs — pure functions from evidence to numbers, no model in the loop.

Everything here is computed from three inputs: declared baselines (workflow
files), recorded runs, and recorded human feedback. No estimates are invented
at report time; when a denominator is unknown the report says so instead of
guessing. The formulas are deliberately conservative — when in doubt they
UNDERSTATE savings — and every one of them is written down in docs/metrics.md,
because a number nobody can recompute is a number nobody should present to a
board.

The savings model in one paragraph: a completed run earns the workflow's
baseline minutes MINUS the human minutes actually spent on its output.
Reviewed-and-rejected outputs earn NEGATIVE savings (they consumed review time
and produced nothing), unreviewed outputs earn nothing unless the workflow is
declared review-free, and no run may earn more than its own baseline. Time is
the only claimed benefit; quality, speed-to-answer and morale are real but
they are not minutes, so they are not in these numbers.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from flightdeck.config import Org
from flightdeck.ledger import Ledger
from flightdeck.schemas import Feedback, Run, Workflow
from flightdeck.store import Store

Health = str  # "healthy" | "watch" | "underperforming" | "no_data" | "no_target"


@dataclass
class WorkflowReport:
    workflow_id: str
    name: str
    department: str
    tier: str
    data_classification: str
    runs_completed: int = 0
    runs_blocked: int = 0
    runs_failed: int = 0
    reviewed: int = 0
    accepted: int = 0
    edited: int = 0
    rejected: int = 0
    acceptance_rate: float | None = None  # (accepted + edited) / reviewed
    active_users: int = 0
    weekly_active_avg: float | None = None  # mean over the last complete ISO weeks
    eligible_users: int | None = None
    adoption: float | None = None  # weekly_active_avg / eligible_users
    hours_saved: float = 0.0
    ai_cost: float = 0.0
    value: float = 0.0  # hours_saved × hourly cost
    net_value: float = 0.0
    redactions: int = 0
    health: Health = "no_data"


@dataclass
class WeeklyPoint:
    week: str  # ISO label, e.g. "2026-W23"
    start: date
    runs: int = 0
    active_users: int = 0
    hours_saved: float = 0.0
    cost: float = 0.0


@dataclass
class GovernanceReport:
    blocked_budget: int = 0  # in the KPI window …
    blocked_policy: int = 0
    blocked_budget_all: int = 0  # … and since the beginning, so old incidents stay visible
    blocked_policy_all: int = 0
    failed: int = 0
    redactions: int = 0
    region_mix: dict[str, int] = field(default_factory=dict)  # completed runs per model region
    no_training_share: float | None = None  # share of completed runs on non-training vendors
    ledger_entries: int = 0
    ledger_ok: bool = True


@dataclass
class OrgReport:
    org_name: str
    currency: str
    window_days: int
    since: datetime
    until: datetime
    workflows: list[WorkflowReport] = field(default_factory=list)
    weekly: list[WeeklyPoint] = field(default_factory=list)  # full history, for trends
    governance: GovernanceReport = field(default_factory=GovernanceReport)
    total_hours_saved: float = 0.0
    total_value: float = 0.0
    total_ai_cost: float = 0.0
    total_net_value: float = 0.0
    total_runs_completed: int = 0
    active_users: int = 0


# ------------------------------------------------------------------- savings


def minutes_saved(workflow: Workflow, run: Run, feedback: Feedback | None, default_review_minutes: float) -> float:
    """Minutes of human time one run earned, per the conservative model above.
    Blocked and failed runs earn 0 by definition (they produced nothing and the
    cost side already carries them)."""
    if run.status != "completed":
        return 0.0
    baseline = workflow.baseline.minutes_per_task
    if feedback is None:
        if workflow.review == "none":
            return baseline  # declared review-free: the task no longer takes human time
        if workflow.review == "spot_check":
            return baseline - default_review_minutes  # sampled review; assume the default
        return 0.0  # human-in-the-loop and not reviewed yet: unmeasured is not saved
    human = feedback.human_minutes if feedback.human_minutes is not None else default_review_minutes
    if feedback.outcome == "rejected":
        return -human  # the run consumed review time and produced nothing usable
    return min(baseline - human, baseline)


def earned_minutes(
    org: Org, runs: list[Run], feedback: dict[str, Feedback], default_review_minutes: float
) -> dict[str, float]:
    """Per-run earned minutes with the MONTHLY CAP applied: a workflow can never
    claim more positive minutes in a calendar month than the task volume it
    declared (baseline minutes × tasks per month). The cap is what keeps the
    savings number immune to runaway loops and duplicate runs — 300 copies of
    the same digest do not become 300 digests' worth of saved time. Credit is
    chronological, so duplicates crowd out nothing but themselves; negative
    minutes (rejected outputs) always count. Expects ``runs`` in time order."""
    used: dict[tuple[str, str], float] = {}
    earned: dict[str, float] = {}
    for run in runs:
        workflow = org.workflows.get(run.workflow_id)
        if workflow is None or run.status != "completed":
            continue
        minutes = minutes_saved(workflow, run, feedback.get(run.id), default_review_minutes)
        if minutes > 0:
            key = (run.workflow_id, run.started_at.strftime("%Y-%m"))
            cap = workflow.baseline.minutes_per_task * workflow.baseline.tasks_per_month
            minutes = min(minutes, max(cap - used.get(key, 0.0), 0.0))
            used[key] = used.get(key, 0.0) + minutes
        earned[run.id] = minutes
    return earned


def _iso_week(when: datetime) -> tuple[str, date]:
    year, week, _ = when.isocalendar()
    start = date.fromisocalendar(year, week, 1)
    return f"{year}-W{week:02d}", start


def _health(workflow: Workflow, report: WorkflowReport) -> Health:
    targets: list[float] = []
    if workflow.success.acceptance_target is not None:
        if report.acceptance_rate is None:
            return "no_data" if workflow.review != "none" else "no_target"
        targets.append(report.acceptance_rate / workflow.success.acceptance_target)
    if workflow.success.weekly_active_users_target is not None and report.weekly_active_avg is not None:
        targets.append(report.weekly_active_avg / workflow.success.weekly_active_users_target)
    if not targets:
        return "no_target"
    worst = min(targets)
    if worst >= 1.0:
        return "healthy"
    if worst >= 0.75:
        return "watch"
    return "underperforming"


# -------------------------------------------------------------------- report


def build_report(org: Org, store: Store, ledger: Ledger, days: int = 30, now: datetime | None = None) -> OrgReport:
    """KPIs over the last ``days`` (the decision window) plus full-history weekly
    trends (the context). One pass over the evidence, pure computation."""
    until = now or datetime.now(UTC)
    since = until - timedelta(days=days)
    default_review = org.config.default_review_minutes

    all_runs = store.runs()
    feedback = store.feedback_map()
    earned = earned_minutes(org, all_runs, feedback, default_review)
    verify = ledger.verify()

    report = OrgReport(
        org_name=org.config.name,
        currency=org.config.currency,
        window_days=days,
        since=since,
        until=until,
    )
    report.governance.ledger_entries = verify.entries
    report.governance.ledger_ok = verify.ok

    # Full-history weekly trend (org level).
    weekly: dict[str, WeeklyPoint] = {}
    weekly_users: dict[str, set[str]] = defaultdict(set)
    # Per-workflow weekly actives, for the adoption average inside the window.
    workflow_week_users: dict[tuple[str, str], set[str]] = defaultdict(set)

    for run in all_runs:
        workflow = org.workflows.get(run.workflow_id)
        week_label, week_start = _iso_week(run.started_at)
        point = weekly.setdefault(week_label, WeeklyPoint(week=week_label, start=week_start))
        point.cost += run.cost
        if run.status == "blocked":
            if "budget" in (run.reason or ""):
                report.governance.blocked_budget_all += 1
            else:
                report.governance.blocked_policy_all += 1
        if run.status == "completed":
            point.runs += 1
            weekly_users[week_label].add(run.user)
            if workflow is not None:
                point.hours_saved += earned.get(run.id, 0.0) / 60
                workflow_week_users[(run.workflow_id, week_label)].add(run.user)

    for label, users in weekly_users.items():
        weekly[label].active_users = len(users)
    report.weekly = sorted(weekly.values(), key=lambda point: point.start)

    # Window KPIs per workflow.
    window_runs = [run for run in all_runs if run.started_at >= since]
    window_users: set[str] = set()

    for workflow_id, workflow in org.workflows.items():
        entry = WorkflowReport(
            workflow_id=workflow_id,
            name=workflow.name,
            department=workflow.department,
            tier=workflow.tier,
            data_classification=workflow.data_classification,
            eligible_users=org.eligible_users(workflow),
        )
        hourly = org.hourly_cost(workflow)
        users: set[str] = set()
        minutes = 0.0

        for run in window_runs:
            if run.workflow_id != workflow_id:
                continue
            entry.ai_cost += run.cost
            entry.redactions += run.redactions
            if run.status == "blocked":
                entry.runs_blocked += 1
                if "budget" in (run.reason or ""):
                    report.governance.blocked_budget += 1
                else:
                    report.governance.blocked_policy += 1
                continue
            if run.status == "failed":
                entry.runs_failed += 1
                report.governance.failed += 1
                continue
            entry.runs_completed += 1
            users.add(run.user)
            window_users.add(run.user)
            verdict = feedback.get(run.id)
            if verdict is not None:
                entry.reviewed += 1
                if verdict.outcome == "accepted":
                    entry.accepted += 1
                elif verdict.outcome == "edited":
                    entry.edited += 1
                else:
                    entry.rejected += 1
            minutes += earned.get(run.id, 0.0)
            spec = org.models.get(run.model_id)
            if spec is not None:
                report.governance.region_mix[spec.region] = report.governance.region_mix.get(spec.region, 0) + 1

        entry.active_users = len(users)
        if entry.reviewed:
            entry.acceptance_rate = (entry.accepted + entry.edited) / entry.reviewed

        # Adoption: average actives over the last (up to) 4 COMPLETE ISO weeks in the window.
        complete_weeks = sorted(
            {
                label
                for (wf, label), _ in workflow_week_users.items()
                if wf == workflow_id and date.fromisocalendar(*_week_key(label), 1) + timedelta(days=7) <= until.date()
            }
        )[-4:]
        if complete_weeks:
            entry.weekly_active_avg = sum(
                len(workflow_week_users[(workflow_id, label)]) for label in complete_weeks
            ) / len(complete_weeks)
            if entry.eligible_users:
                entry.adoption = entry.weekly_active_avg / entry.eligible_users

        entry.hours_saved = minutes / 60
        entry.value = entry.hours_saved * hourly
        entry.net_value = entry.value - entry.ai_cost
        entry.health = _health(workflow, entry)
        report.workflows.append(entry)

    completed = [run for run in window_runs if run.status == "completed"]
    if completed:
        no_training = sum(
            1 for run in completed if (spec := org.models.get(run.model_id)) and not spec.trains_on_data
        )
        report.governance.no_training_share = no_training / len(completed)
    report.governance.redactions = sum(entry.redactions for entry in report.workflows)

    report.workflows.sort(key=lambda entry: entry.net_value, reverse=True)
    report.total_hours_saved = sum(entry.hours_saved for entry in report.workflows)
    report.total_value = sum(entry.value for entry in report.workflows)
    report.total_ai_cost = sum(entry.ai_cost for entry in report.workflows)
    report.total_net_value = sum(entry.net_value for entry in report.workflows)
    report.total_runs_completed = sum(entry.runs_completed for entry in report.workflows)
    report.active_users = len(window_users)
    return report


def _week_key(label: str) -> tuple[int, int]:
    year, week = label.split("-W")
    return int(year), int(week)
