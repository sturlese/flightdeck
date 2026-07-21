"""The domain model — every business decision in the system, typed.

flightdeck's core bet is that an AI transformation is manageable only if its
units are DECLARED rather than implied. So each unit is a schema with the
business facts attached:

- A ``UseCase`` is a candidate: what the task costs humans today, how automatable
  it is, how risky. Enough to score a backlog before writing a single prompt.
- A ``Workflow`` is a promoted use case: prompt steps plus the three things most
  AI pilots forget to write down — the human BASELINE it must beat, the DATA
  CLASSIFICATION that gates where it may run, and the SUCCESS criteria that
  decide whether it scales or dies.
- A ``ModelSpec`` carries the governance facts about a model (residency, whether
  the vendor trains on your data, price) — the policy engine reasons over these,
  never over vibes about vendors.
- ``Run`` and ``Feedback`` are the evidence: what actually executed, what it
  cost, and what a human did with the output. Every KPI in the reports is
  computed from these two tables and nothing else.

Validation is strict on identity (ids are slugs, referenced everywhere) and
lenient on economics (any positive number is a legal baseline — realism is the
operator's job, arithmetic is ours).
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DataClass = Literal["public", "internal", "confidential", "restricted"]
Tier = Literal["fast", "balanced", "frontier"]
Outcome = Literal["accepted", "edited", "rejected"]
ReviewMode = Literal["human_in_the_loop", "spot_check", "none"]
RunStatus = Literal["completed", "blocked", "failed"]
#: How often a review-free workflow is due to run under `flightdeck tick`. A
#: calendar period, not a cron expression: flightdeck decides due-ness, an
#: external scheduler decides how often to ask (see scheduler.py).
Cadence = Literal["daily", "weekly", "monthly"]

SLUG = r"^[a-z0-9][a-z0-9_-]*$"

#: Data classifications ordered from least to most sensitive. Policy rules may rely on
#: this order (e.g. "internal and above must not train the vendor's models").
DATA_CLASS_ORDER: tuple[DataClass, ...] = ("public", "internal", "confidential", "restricted")


class StrictModel(BaseModel):
    """Reject unknown keys everywhere: a typo in a governance file must be a loud
    error, not a silently ignored rule."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- registry


class ModelSpec(StrictModel):
    """One row of the model registry: capability tier plus the governance facts
    the policy engine needs. ``id`` is the routing key; ``model`` is whatever the
    provider calls it."""

    id: str = Field(pattern=SLUG)
    provider: str  # "anthropic" | "openai" | "mock" | any registered adapter
    model: str
    tier: Tier
    input_cost_per_mtok: float = Field(ge=0)
    output_cost_per_mtok: float = Field(ge=0)
    region: str = "global"  # data residency: "eu", "us", "uae", "global", ...
    trains_on_data: bool = False  # does the vendor train on data sent to this endpoint?
    base_url: str | None = None  # override for Azure OpenAI / gateways / proxies
    notes: str = ""

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        return tokens_in / 1e6 * self.input_cost_per_mtok + tokens_out / 1e6 * self.output_cost_per_mtok


# --------------------------------------------------------------------------- policy


class DataRule(StrictModel):
    """What a model must satisfy to receive data of a given classification.
    ``None`` means "no constraint on this axis" — absence of a rule is visible,
    not implicit."""

    regions: list[str] | None = None  # model.region must be one of these
    providers: list[str] | None = None  # model.provider must be one of these
    models: list[str] | None = None  # explicit allowlist of registry ids
    forbid_training_vendors: bool = False  # require model.trains_on_data == False

    def allows(self, spec: ModelSpec) -> bool:
        if self.forbid_training_vendors and spec.trains_on_data:
            return False
        if self.regions is not None and spec.region not in self.regions:
            return False
        if self.providers is not None and spec.provider not in self.providers:
            return False
        return self.models is None or spec.id in self.models


def default_data_rules() -> dict[str, DataRule]:
    """Conservative defaults: anything beyond public data never reaches a vendor
    that trains on it; restricted data additionally requires an explicit model
    allowlist (empty by default — restricted workflows fail closed until the org
    decides)."""
    return {
        "public": DataRule(),
        "internal": DataRule(forbid_training_vendors=True),
        "confidential": DataRule(forbid_training_vendors=True),
        "restricted": DataRule(forbid_training_vendors=True, models=[]),
    }


class PolicyConfig(StrictModel):
    data_rules: dict[DataClass, DataRule] = Field(default_factory=default_data_rules)
    redact_pii_default: bool = True
    #: Org-specific redaction regexes (employee ids, customer codes …), applied on
    #: top of the built-in PII patterns whenever a workflow redacts. Hits are
    #: counted on the run like every built-in pattern's.
    redact_patterns: list[str] = Field(default_factory=list)
    #: Applied to every workflow that does not set its own cap. None = uncapped;
    #: a real cap is positive (gt=0), same as the per-workflow ``monthly_budget``
    #: it stands in for — 0/negative isn't a looser cap, it fail-closes every
    #: uncapped workflow (``check_budget``: ``spent >= cap`` is true at 0 spend).
    default_monthly_budget: float | None = Field(default=None, gt=0)

    @field_validator("redact_patterns")
    @classmethod
    def _patterns_must_compile(cls, patterns: list[str]) -> list[str]:
        # Validated at load so a bad regex is a loud config error naming the org
        # file — never a runtime crash inside the redactor, mid-run.
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex {pattern!r}: {exc}") from None
        return patterns

    @model_validator(mode="after")
    def _fill_missing_classes(self) -> "PolicyConfig":
        # A partial data_rules block in YAML falls back to the conservative default
        # per class, so overriding "restricted" never silently un-governs "internal".
        defaults = default_data_rules()
        for cls, rule in defaults.items():
            self.data_rules.setdefault(cls, rule)  # type: ignore[arg-type]
        return self


# --------------------------------------------------------------------------- org


class Department(StrictModel):
    name: str
    headcount: int | None = Field(default=None, ge=1)


class OrgConfig(StrictModel):
    """The organization file (``flightdeck.yaml``): identity, economics defaults,
    and the policy block. One currency org-wide — model prices in the registry
    are expected in this same currency (see docs/metrics.md for the rationale)."""

    name: str
    currency: str = "EUR"
    default_hourly_cost: float = Field(default=45.0, gt=0)
    #: Minutes a reviewer spends on an output when feedback doesn't say otherwise.
    #: Deliberately conservative: accepted-without-timing still costs review time.
    default_review_minutes: float = Field(default=2.0, ge=0)
    departments: list[Department] = Field(default_factory=list)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)


# --------------------------------------------------------------------------- backlog


class UseCase(StrictModel):
    """A candidate for automation, captured with enough business facts to be
    scored: what it costs today, how much of it AI can take, and how risky it is.
    See ``backlog.py`` for the scoring formula (documented in docs/metrics.md)."""

    id: str = Field(pattern=SLUG)
    name: str
    department: str
    description: str = ""
    task_minutes: float = Field(gt=0)  # human minutes per task today
    tasks_per_month: float = Field(gt=0)
    hourly_cost: float | None = Field(default=None, gt=0)  # None → org default
    automation_potential: float = Field(ge=0, le=1)  # share of the task AI can take
    data_readiness: int = Field(ge=1, le=5)  # 5 = inputs are clean and reachable
    process_stability: int = Field(ge=1, le=5)  # 5 = same steps every time
    risk: int = Field(ge=1, le=5)  # 5 = sensitive data or high error blast radius
    effort_weeks: float = Field(gt=0)
    status: Literal["candidate", "piloting", "live", "killed"] = "candidate"


# --------------------------------------------------------------------------- workflow


class Baseline(StrictModel):
    """The human cost the workflow must beat. No baseline, no ROI claim — this
    block is mandatory by design."""

    minutes_per_task: float = Field(gt=0)
    tasks_per_month: float = Field(gt=0)
    hourly_cost: float | None = Field(default=None, gt=0)  # None → org default


class Guardrails(StrictModel):
    redact_pii: bool | None = None  # None → policy default
    monthly_budget: float | None = Field(default=None, gt=0)  # None → policy default


class SuccessCriteria(StrictModel):
    """The scale-or-kill thresholds, declared before the pilot starts. A scorecard
    that can say "kill this" is the point of the exercise."""

    weekly_active_users_target: int | None = Field(default=None, ge=1)
    #: Minimum share of reviewed outputs that are accepted or lightly edited.
    acceptance_target: float | None = Field(default=None, ge=0, le=1)


class Step(StrictModel):
    id: str = Field(pattern=SLUG)
    prompt: str  # template; {{var}} placeholders are filled from --var / vars
    vars: list[str] = Field(default_factory=list)
    max_output_tokens: int = Field(default=1024, gt=0)


class Schedule(StrictModel):
    """Declares that a review-free workflow should run on a cadence, driven by
    `flightdeck tick`. A digest bot has no human to pass `--var`, so the inputs
    it needs are declared here in the spec — governance in version control, like
    everything else. Deliberately NOT a cron parser: the cadence is a calendar
    period and idempotency does the rest (see scheduler.py)."""

    cadence: Cadence
    #: The variable values the scheduled run feeds its steps (name → value).
    vars: dict[str, str] = Field(default_factory=dict)


class Workflow(StrictModel):
    """A promoted use case: executable steps plus the governance and measurement
    facts that make the runs comparable and the value claim auditable."""

    id: str = Field(pattern=SLUG)
    name: str
    department: str
    owner: str = ""
    description: str = ""
    use_case: str | None = None  # backlog lineage (usecases.yaml id)
    data_classification: DataClass = "internal"
    tier: Tier = "balanced"
    #: Denominator for adoption. None → department headcount → unknown.
    eligible_users: int | None = Field(default=None, ge=1)
    review: ReviewMode = "human_in_the_loop"
    baseline: Baseline
    steps: list[Step] = Field(min_length=1)
    guardrails: Guardrails = Field(default_factory=Guardrails)
    success: SuccessCriteria = Field(default_factory=SuccessCriteria)
    #: Unattended cadence for review-free workflows only (see the validator).
    schedule: Schedule | None = None

    @field_validator("steps")
    @classmethod
    def _unique_step_ids(cls, steps: list[Step]) -> list[Step]:
        ids = [s.id for s in steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique within a workflow")
        return steps

    @model_validator(mode="after")
    def _schedule_requires_review_none(self) -> "Workflow":
        # Scheduling means running with no human in the loop. Putting a schedule on
        # a human-reviewed workflow would silently drop the review — a governance
        # typo must fail loudly, not un-govern the workflow.
        if self.schedule is not None and self.review != "none":
            raise ValueError(
                "schedule requires review: none — a scheduled workflow runs unattended, "
                f"but '{self.id}' declares review: {self.review}"
            )
        return self


# --------------------------------------------------------------------------- evidence


class Run(StrictModel):
    """One governed execution. Everything the KPIs need is captured at run time —
    a report never has to call a provider to explain the past."""

    id: str
    workflow_id: str
    user: str
    started_at: datetime
    finished_at: datetime
    status: RunStatus
    model_id: str = ""  # registry id actually used ("" when blocked before routing)
    provider: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    latency_ms: int = 0
    redactions: int = 0  # PII spans redacted before the payload left the org
    reason: str | None = None  # why blocked (policy gate) or failed (provider error)
    output: str | None = None  # final step output, kept for human review


class Feedback(StrictModel):
    """What a human did with a run's output. This is where "productivity gain"
    stops being a slide and becomes a measurement: outcome plus the minutes the
    human actually spent reviewing or fixing it."""

    run_id: str
    outcome: Outcome
    human_minutes: float | None = Field(default=None, ge=0)  # None → org default_review_minutes
    by: str = ""
    note: str = ""
    at: datetime


# --------------------------------------------------------------------------- directory


#: Where a directory snapshot came from. Metadata only — the core reads ``users``
#: and never calls the provider's API (the live pull is a documented sync adapter).
DirectorySource = Literal["file", "azure_ad", "google_workspace"]


class DirectoryUser(StrictModel):
    """One person from the org's SSO directory (Azure AD / Google Workspace / HRIS).

    ``id`` is the STABLE identifier runs attribute to — an Azure objectId, a
    Google id, an employee number. Deliberately NOT a slug: real directory ids
    carry uppercase, dots and @. ``aliases`` are other handles that resolve to
    this same person (old usernames, sam-account-names), so a run typed as a
    legacy login still lands on the right stable id. Privacy: the stable id is
    what gets stored on a run; the display name is rendered only at report time."""

    id: str = Field(min_length=1)  # stable external id — free non-empty string, not a slug
    display_name: str = Field(min_length=1)
    email: str | None = None  # a UPN, not necessarily RFC-strict; matched case-insensitively
    department: str | None = None
    active: bool = True  # inactive members never count toward an adoption denominator
    aliases: list[str] = Field(default_factory=list)


class DirectorySnapshot(StrictModel):
    """The on-disk ``directory.yaml``: a synced snapshot of the org directory.

    ``provider`` records where the snapshot was pulled from; the offline core
    only ever reads ``users``. A real Azure AD / Google Workspace sync writes
    this file out-of-band (see ``directory.py`` and docs/governance.md)."""

    provider: DirectorySource = "file"
    users: list[DirectoryUser] = Field(default_factory=list)
