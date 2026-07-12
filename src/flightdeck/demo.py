"""The demo seeder — a 13-week synthetic history you can audit.

Seeds the fictional games publisher Meridian Interactive with a year-one-shaped
AI program: six workflows with different personalities (a support pilot that
ramps well, a localization pilot that underperforms and says so, an unattended
digest bot, restricted board material), plus the two governance incidents every
real program eventually meets:

- weeks 1–2: the restricted board-pack workflow runs BEFORE its model
  allowlist was approved — every attempt blocks, and the ledger keeps the
  receipts (fail-closed is a feature, and here is what it looks like);
- week 9: a scheduling bug sends the digest bot into a retry storm on a
  frontier model — the monthly budget cap catches it, later digests block
  until the month rolls, and the weekly cost chart wears the spike.

Everything is deterministic for a given seed and anchor date, generated with
plain `random.Random` — the point is a REALISTIC dataset for the reports, demos
and screenshots, not a simulation. Entry 0 of the ledger says exactly what this
data is.
"""

import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from random import Random

import yaml

from flightdeck.config import load_org
from flightdeck.ledger import Ledger
from flightdeck.policy import BUDGET_BLOCK_PREFIX
from flightdeck.runner import record  # the same evidence path real runs take
from flightdeck.schemas import Feedback, Run
from flightdeck.store import Store

SEED = 42
WEEKS = 13

_USERS = {
    "support-reply-drafting": [
        "aisha", "jonas", "priya", "lena", "tomas", "sofia", "kenji",
        "fatima", "diego", "nour", "eva", "liam", "zara", "omar",
    ],
    "community-digest": ["svc-digest-bot"],
    "localization-qa": ["yuki", "pierre", "carla", "hans", "mina", "pavel", "ines", "jorge"],
    "contract-triage": ["sara", "victor", "amal"],
    "board-pack-sections": ["elena", "raj"],
    "patch-notes-drafting": ["leo", "anna", "kofi", "marta", "ivan", "ruth", "alex", "noa", "ben"],
}


@dataclass
class _Profile:
    """One workflow's demo personality: volume ramp, verdict mix, review effort."""

    workflow_id: str
    model_id: str
    tokens_in: int
    tokens_out: int
    share_start: float  # share of the declared monthly task volume, week 1 …
    share_end: float  # … and week 13
    active_start: int  # distinct weekly users, week 1 …
    active_end: int  # … and week 13
    outcomes_start: tuple[float, float, float]  # accepted / edited / rejected, week 1
    outcomes_end: tuple[float, float, float]
    accept_minutes: tuple[float, float]
    edit_minutes: tuple[float, float]
    reject_minutes: tuple[float, float]
    review_coverage: float  # share of completed runs that received feedback
    redact_rate: float  # probability a run had PII scrubbed


_PROFILES = [
    _Profile(
        "support-reply-drafting", "haiku-eu", 1300, 380,
        0.10, 0.66, 3, 11,
        (0.50, 0.34, 0.16), (0.66, 0.27, 0.07),
        (1, 3.5), (4, 10), (2, 6), 0.85, 0.35,
    ),
    _Profile(
        "community-digest", "haiku-eu", 6000, 900,
        0.35, 0.95, 1, 1,
        (1, 0, 0), (1, 0, 0),  # review-free: no feedback is ever generated
        (0, 0), (0, 0), (0, 0), 0.0, 0.05,
    ),
    _Profile(
        # The honest failure: volume flat, almost half the outputs rejected. The
        # scorecard exists to make this workflow's kill-or-rework call in the open.
        "localization-qa", "sonnet-eu", 3200, 750,
        0.12, 0.17, 3, 3,
        (0.34, 0.24, 0.42), (0.37, 0.26, 0.37),
        (4, 9), (14, 30), (3, 8), 0.90, 0.02,
    ),
    _Profile(
        "contract-triage", "opus-eu", 9000, 1150,
        0.25, 0.82, 1, 3,
        (0.48, 0.36, 0.16), (0.58, 0.33, 0.09),
        (6, 15), (10, 25), (3, 8), 0.95, 0.50,
    ),
    _Profile(
        "board-pack-sections", "opus-eu", 12000, 1900,
        0.55, 0.90, 1, 2,
        (0.42, 0.42, 0.16), (0.55, 0.38, 0.07),
        (12, 30), (20, 50), (5, 15), 1.0, 0.10,
    ),
    _Profile(
        "patch-notes-drafting", "haiku-eu", 4300, 1100,
        0.15, 0.72, 2, 5,
        (0.55, 0.34, 0.11), (0.70, 0.25, 0.05),
        (1, 4), (5, 12), (2, 6), 0.80, 0.02,
    ),
]

_POLICY_BLOCK_REASON = (
    "no policy-compliant model available in tier 'frontier' or above "
    "(0 model(s) cleared by data policy)"
)


class DemoSeedError(Exception):
    """Seeding would destroy files that are not a previous flightdeck demo. The
    CLI maps this to a usage error (exit 2); callers that mean it pass a fresh
    directory."""


@dataclass
class DemoSummary:
    root: Path
    weeks: int
    runs_completed: int = 0
    runs_blocked: int = 0
    runs_failed: int = 0
    feedback: int = 0


def _lerp(start: float, end: float, progress: float) -> float:
    return start + (end - start) * progress


def _business_moment(rng: Random, week_start: date, max_day: int = 4) -> datetime:
    day = rng.randrange(max_day + 1)  # Mon–Fri, clipped to "today" in the current week
    hour = rng.randrange(9, 18)
    minute = rng.randrange(60)
    return datetime(week_start.year, week_start.month, week_start.day, tzinfo=UTC) + timedelta(
        days=day, hours=hour, minutes=minute
    )


def _refuse_to_destroy(root: Path, source) -> None:
    """Allow seeding only into a new/empty directory or a previous demo org,
    recognized by the demo org's name in ``flightdeck.yaml``. Re-seeding a demo
    in place is routine (the dir accumulates dashboards and runtime state);
    silently wiping a REAL org's workflows, store and audit ledger is not."""
    if not root.exists() or not any(root.iterdir()):
        return
    demo_name = yaml.safe_load((source / "flightdeck.yaml").read_text(encoding="utf-8"))["name"]
    org_file = root / "flightdeck.yaml"
    if org_file.is_file():
        try:
            existing = yaml.safe_load(org_file.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            existing = None
        if isinstance(existing, dict) and existing.get("name") == demo_name:
            return
    raise DemoSeedError(
        f"refusing to seed the demo into {root}: the directory is not empty and is not a "
        f"previous flightdeck demo — seeding would overwrite the org files and delete "
        f"workflows/, the run store and the audit ledger. Pass a new or empty --dir."
    )


def seed(target: Path | str, weeks: int = WEEKS, rng_seed: int = SEED) -> DemoSummary:
    """Create the demo org at ``target`` and seed its store and ledger. ``target``
    must be new, empty, or a previous demo: seeding overwrites the org files and
    deletes workflows/, the store and the ledger (see ``_refuse_to_destroy``)."""
    root = Path(target)
    source = files("flightdeck") / "demo_org"
    _refuse_to_destroy(root, source)
    root.mkdir(parents=True, exist_ok=True)
    for item in ("flightdeck.yaml", "models.yaml", "usecases.yaml"):
        (root / item).write_text((source / item).read_text(encoding="utf-8"), encoding="utf-8")
    workflows_dir = root / "workflows"
    if workflows_dir.exists():
        shutil.rmtree(workflows_dir)
    workflows_dir.mkdir()
    for entry in (source / "workflows").iterdir():
        (workflows_dir / entry.name).write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")

    org = load_org(root)
    if org.db_path.exists():
        org.db_path.unlink()
    if org.ledger_path.exists():
        org.ledger_path.unlink()

    rng = Random(rng_seed)
    today = date.today()
    current_monday = today - timedelta(days=today.weekday())
    first_monday = current_monday - timedelta(weeks=weeks)

    events: list[tuple[datetime, Run | Feedback]] = []
    month_cost: dict[tuple[str, str], float] = {}  # (workflow, "YYYY-MM") → committed cost
    counter = 0

    def _spend(workflow_id: str, when: datetime) -> float:
        return month_cost.get((workflow_id, when.strftime("%Y-%m")), 0.0)

    def _commit(workflow_id: str, when: datetime, cost: float) -> None:
        key = (workflow_id, when.strftime("%Y-%m"))
        month_cost[key] = month_cost.get(key, 0.0) + cost

    def _make_run(profile: _Profile, when: datetime, user: str, model_id: str | None = None) -> Run | None:
        """One completed/blocked/failed run, honoring the budget cap the way the
        real gate does. Returns the Run, or None when the org's own guardrail
        blocked it (the blocked Run is queued as an event either way)."""
        nonlocal counter
        counter += 1
        workflow = org.workflows[profile.workflow_id]
        spec = org.models[model_id or profile.model_id]
        cap = workflow.guardrails.monthly_budget
        run_id = f"{rng.getrandbits(48):012x}"

        if cap is not None and _spend(workflow.id, when) >= cap:
            run = Run(
                id=run_id, workflow_id=workflow.id, user=user, started_at=when,
                finished_at=when, status="blocked",
                reason=f"{BUDGET_BLOCK_PREFIX}: {_spend(workflow.id, when):.2f} "
                f"of {cap:.2f} {org.config.currency} committed",
            )
            events.append((when, run))
            return None

        if rng.random() < 0.004:  # the occasional vendor hiccup
            run = Run(
                id=run_id, workflow_id=workflow.id, user=user, started_at=when,
                finished_at=when + timedelta(seconds=30), status="failed",
                model_id=spec.id, provider=spec.provider,
                reason="provider: upstream timeout after 3 retries",
            )
            events.append((when, run))
            return None

        tokens_in = int(profile.tokens_in * rng.uniform(0.65, 1.35))
        tokens_out = int(profile.tokens_out * rng.uniform(0.65, 1.35))
        cost = spec.cost(tokens_in, tokens_out)
        _commit(workflow.id, when, cost)
        run = Run(
            id=run_id, workflow_id=workflow.id, user=user, started_at=when,
            finished_at=when + timedelta(seconds=rng.uniform(4, 40)),
            status="completed", model_id=spec.id, provider=spec.provider,
            tokens_in=tokens_in, tokens_out=tokens_out, cost=cost,
            latency_ms=int(rng.uniform(900, 4200)),
            redactions=rng.choice((1, 1, 2, 3)) if rng.random() < profile.redact_rate else 0,
            output=f"[demo] {workflow.name} — output {counter}",
        )
        events.append((when, run))
        return run

    now = datetime.now(UTC)

    def _maybe_feedback(profile: _Profile, run: Run, progress: float) -> None:
        if rng.random() >= profile.review_coverage:
            return
        acc, edit, _ = (
            _lerp(profile.outcomes_start[index], profile.outcomes_end[index], progress) for index in range(3)
        )
        roll = rng.random()
        if roll < acc:
            outcome, minutes = "accepted", rng.uniform(*profile.accept_minutes)
        elif roll < acc + edit:
            outcome, minutes = "edited", rng.uniform(*profile.edit_minutes)
        else:
            outcome, minutes = "rejected", rng.uniform(*profile.reject_minutes)
        at = min(run.finished_at + timedelta(hours=rng.uniform(0.2, 30)), now)  # reviews never post-date "now"
        events.append(
            (at, Feedback(run_id=run.id, outcome=outcome, human_minutes=round(minutes, 1), by=run.user, at=at))
        )

    for index in range(weeks + 1):  # +1: a partial current week, so "today" looks alive
        week_start = first_monday + timedelta(weeks=index)
        progress = min(index / max(weeks - 1, 1), 1.0)
        partial = index == weeks

        for profile in _PROFILES:
            workflow = org.workflows[profile.workflow_id]

            # Weeks 1–2 of the board-pack story: the restricted allowlist wasn't
            # approved yet; every attempt fails closed and is ledgered.
            if profile.workflow_id == "board-pack-sections" and index < 2:
                for _ in range(2 + index):
                    when = _business_moment(rng, week_start)
                    events.append(
                        (
                            when,
                            Run(
                                id=f"{rng.getrandbits(48):012x}", workflow_id=workflow.id,
                                user="elena", started_at=when, finished_at=when,
                                status="blocked", reason=_POLICY_BLOCK_REASON,
                            ),
                        )
                    )
                continue

            share = _lerp(profile.share_start, profile.share_end, progress)
            week_runs = workflow.baseline.tasks_per_month / 4.33 * share
            week_runs *= rng.uniform(0.85, 1.15)
            if partial:
                week_runs *= (today.weekday() + 1) / 7
            actives = max(1, round(_lerp(profile.active_start, profile.active_end, progress)))
            pool = _USERS[profile.workflow_id][:actives]

            for _ in range(round(week_runs)):
                # Power-law-ish usage: early adopters carry more of the volume.
                user = pool[min(int(rng.betavariate(1.2, 2.2) * len(pool)), len(pool) - 1)]
                when = _business_moment(rng, week_start, max_day=today.weekday() if partial else 4)
                run = _make_run(profile, when, user)
                if run is not None and profile.review_coverage > 0:
                    _maybe_feedback(profile, run, progress)

        # Week 9: the digest bot's retry storm on a frontier model. The budget
        # cap absorbs it; _make_run blocks everything past the cap.
        if index == 8:
            digest = next(p for p in _PROFILES if p.workflow_id == "community-digest")
            storm_day = week_start + timedelta(days=1)
            for attempt in range(300):
                when = datetime(storm_day.year, storm_day.month, storm_day.day, tzinfo=UTC) + timedelta(
                    hours=6 + (attempt * 7) // 60, minutes=(attempt * 7) % 60
                )
                _make_run(digest, when, "svc-digest-bot", model_id="opus-eu")

    events.sort(key=lambda pair: pair[0])

    summary = DemoSummary(root=root, weeks=weeks)
    with Store(org.db_path) as store:
        ledger = Ledger(org.ledger_path)
        ledger.append(
            "demo_seeded",
            {
                "org": org.config.name,
                "weeks": weeks,
                "seed": rng_seed,
                "note": "synthetic history generated by `flightdeck demo`; deterministic for a given seed and date",
            },
            at=datetime(first_monday.year, first_monday.month, first_monday.day, 8, 0, tzinfo=UTC),
        )
        for when, event in events:
            if isinstance(event, Run):
                record(store, ledger, event)
                if event.status == "completed":
                    summary.runs_completed += 1
                elif event.status == "blocked":
                    summary.runs_blocked += 1
                else:
                    summary.runs_failed += 1
            else:
                store.add_feedback(event)
                ledger.append(
                    "feedback_recorded",
                    {
                        "run_id": event.run_id,
                        "outcome": event.outcome,
                        "human_minutes": event.human_minutes,
                        "by": event.by,
                    },
                    at=when,
                )
                summary.feedback += 1
    return summary
