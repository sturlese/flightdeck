"""The flightdeck CLI.

Command surface mirrors the operating loop of an AI program:

    init → backlog → promote → run → feedback → report → audit

Every command takes --dir (the org directory) and defaults to the current one.
Exit codes: 0 ok · 1 governance/verification failure (blocked run, broken
chain) · 2 configuration or usage error — scriptable from CI and cron by design.
"""

import dataclasses
import getpass
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.rule import Rule
from rich.table import Table

from flightdeck import __version__, scaffold
from flightdeck import backlog as backlog_mod
from flightdeck.config import ConfigError, Org, load_org
from flightdeck.demo import DemoSeedError, seed
from flightdeck.feedback import FeedbackError, record_feedback
from flightdeck.integrations import slack
from flightdeck.integrations.slack import SlackError
from flightdeck.ledger import Ledger
from flightdeck.metrics import build_report, monthly_statement
from flightdeck.policy import allowed_models, check_budget, should_redact
from flightdeck.report import csv_export, terminal
from flightdeck.report import html as html_report
from flightdeck.report.html import money
from flightdeck.router import NoRouteError, pick
from flightdeck.runner import VariableError, execute, required_vars
from flightdeck.scheduler import is_due, last_run_started_at
from flightdeck.store import Store

#: Optional: set to a Slack incoming-webhook URL to make `slack post` actually
#: POST. Unset (the default) keeps `slack post` offline — it prints the JSON.
SLACK_WEBHOOK_ENV = "FLIGHTDECK_SLACK_WEBHOOK"

#: Runs launched by `flightdeck tick` are attributed to this service account, so
#: the ledger distinguishes scheduled runs from human-initiated ones.
SCHEDULER_USER = "scheduler"

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="The flight deck for enterprise AI adoption: use cases as code, governed runs, provable value.",
)
audit_app = typer.Typer(help="Audit-ledger operations.")
app.add_typer(audit_app, name="audit")
slack_app = typer.Typer(help="Post runs to Slack and capture Accept/Edited/Reject feedback.")
app.add_typer(slack_app, name="slack")

console = Console()
err = Console(stderr=True)

DirOption = Annotated[Path, typer.Option("--dir", "-d", help="Org directory (with flightdeck.yaml).")]


def _org(root: Path) -> Org:
    try:
        return load_org(root)
    except ConfigError as exc:
        err.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(2) from None


@app.callback(invoke_without_command=True)
def _main(
    version: Annotated[
        bool, typer.Option("--version", help="Print version and exit.", is_eager=True)
    ] = False,
) -> None:
    if version:
        console.print(f"flightdeck {__version__}")
        raise typer.Exit()


def _ensure_gitignore(dir: Path) -> None:
    """Keep runtime state out of version control WITHOUT clobbering an existing
    file — a project's .gitignore usually predates flightdeck. Appends the rule
    when the file exists, writes the starter when it doesn't, never duplicates."""
    path = dir / ".gitignore"
    if not path.exists():
        path.write_text(scaffold.GITIGNORE, encoding="utf-8")
        return
    text = path.read_text(encoding="utf-8")
    if ".flightdeck/" in text.splitlines():
        return
    separator = "" if not text or text.endswith("\n") else "\n"
    path.write_text(text + separator + scaffold.GITIGNORE, encoding="utf-8")


@app.command()
def init(dir: DirOption = Path(".")) -> None:
    """Scaffold a starter org: config, model registry, one use case, one workflow."""
    dir.mkdir(parents=True, exist_ok=True)
    targets: dict[Path, str] = {
        dir / "flightdeck.yaml": scaffold.ORG,
        dir / "models.yaml": scaffold.MODELS,
        dir / "usecases.yaml": scaffold.USECASES,
        dir / "workflows" / "meeting-minutes.yaml": scaffold.WORKFLOW,
    }
    # Refuse if ANY target exists — not just flightdeck.yaml. Running init in the
    # wrong directory must never eat a file, and a partial scaffold helps nobody.
    existing = [path for path in targets if path.exists()]
    if existing:
        listing = ", ".join(str(path) for path in existing)
        err.print(f"[red]refusing to overwrite:[/red] {listing} already there — pick another --dir")
        raise typer.Exit(2)
    for path, content in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _ensure_gitignore(dir)
    console.print(f"[green]✓[/green] org scaffolded in [bold]{dir}[/bold] — the files are meant to be edited")
    console.print("  try it offline:  [bold]flightdeck run meeting-minutes --var notes='...'[/bold]")
    console.print("  then:            [bold]flightdeck report[/bold]")


@app.command()
def demo(
    dir: DirOption = Path("flightdeck-demo"),
    html: Annotated[Path | None, typer.Option(help="Where to write the dashboard.")] = None,
) -> None:
    """Seed a 13-week fictional org (offline, deterministic) and report on it."""
    try:
        summary = seed(dir)
    except DemoSeedError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from None
    org = _org(dir)
    with Store(org.db_path) as store:
        ledger = Ledger(org.ledger_path)
        report_data = build_report(org, store, ledger)
    ranked = backlog_mod.ranked(org)
    terminal.render(report_data, ranked, console)

    target = html or dir / "dashboard.html"
    target.write_text(html_report.render(org, report_data, ranked), encoding="utf-8")
    console.print(
        f"[green]✓[/green] seeded {summary.runs_completed:,} completed runs, "
        f"{summary.runs_blocked} blocked, {summary.feedback:,} reviews over {summary.weeks} weeks"
    )
    console.print(f"[green]✓[/green] dashboard: [bold]{target}[/bold]  ← open this in a browser")
    console.print(f"\n  poke at it:  flightdeck report --dir {dir}")
    console.print(f"               flightdeck run support-reply-drafting --dir {dir} \\")
    console.print("                   --var ticket='I was double charged' --var kb_excerpt='Refunds: ...'")
    console.print(f"               flightdeck audit verify --dir {dir}")


@app.command("backlog")
def backlog_cmd(
    dir: DirOption = Path("."),
    all: Annotated[bool, typer.Option("--all", help="Include live and killed use cases.")] = False,
) -> None:
    """Rank the use-case backlog by value × feasibility × risk ÷ effort."""
    org = _org(dir)
    ranked = backlog_mod.ranked(org, include_done=all)
    if not ranked:
        console.print("backlog is empty — add use cases to usecases.yaml")
        return
    table = Table(box=None, padding=(0, 2))
    for column, justify in (
        ("#", "right"), ("use case", "left"), ("dept", "left"), ("status", "left"),
        ("value/mo", "right"), ("feasibility", "right"), ("risk", "right"),
        ("effort", "right"), ("score", "right"),
    ):
        table.add_column(column, justify=justify, header_style="dim")
    for index, item in enumerate(ranked, 1):
        table.add_row(
            str(index), f"{item.case.name} [dim]{item.case.id}[/dim]", item.case.department,
            item.case.status, money(item.monthly_value, org.config.currency, 0),
            f"×{item.feasibility:.2f}", f"×{item.risk_discount:.2f}",
            f"{item.case.effort_weeks:g}wk", f"[bold]{item.score:,.0f}[/bold]",
        )
    console.print(table)
    console.print(f"\n[dim]promote the winner:[/dim] flightdeck promote {ranked[0].case.id}")


@app.command()
def promote(usecase_id: str, dir: DirOption = Path(".")) -> None:
    """Scaffold a workflow file from a backlog use case."""
    org = _org(dir)
    case = org.usecases.get(usecase_id)
    if case is None:
        err.print(f"[red]unknown use case:[/red] '{usecase_id}' — see flightdeck backlog")
        raise typer.Exit(2)
    path = dir / "workflows" / f"{case.id}.yaml"
    if case.id in org.workflows or path.exists():
        err.print(f"[red]refusing to overwrite:[/red] {path} already exists")
        raise typer.Exit(2)

    tier = "fast" if case.risk <= 2 else ("balanced" if case.risk == 3 else "frontier")
    data_class = "internal" if case.risk <= 3 else ("confidential" if case.risk == 4 else "restricted")
    hourly = f"\n  hourly_cost: {case.hourly_cost:g}" if case.hourly_cost else ""
    # Free-text fields go through json.dumps: a JSON string is a valid YAML
    # double-quoted scalar, so a name like "Bug #42" or a department like
    # "Ops: EU" can't break the document or be silently mis-parsed as a comment.
    name = json.dumps(case.name)
    department = json.dumps(case.department)
    description = json.dumps(case.description or "TODO")
    # Inside the prompt block scalar, collapse whitespace so a multi-line name
    # can't break the block's indentation (colons/hashes are already safe there).
    prompt_name = " ".join(case.name.split())
    content = f"""\
# Promoted from use case '{case.id}' — review every value before piloting.
id: {case.id}
name: {name}
department: {department}
use_case: {case.id}
description: {description}
data_classification: {data_class}   # derived from risk={case.risk}; confirm with the data owner
tier: {tier}
review: human_in_the_loop

baseline:
  minutes_per_task: {case.task_minutes:g}
  tasks_per_month: {case.tasks_per_month:g}{hourly}

steps:
  - id: draft
    vars: [input]
    max_output_tokens: 800
    prompt: |
      TODO: write the prompt for '{prompt_name}'. One clear job per step,
      grounded in the provided input, no invented facts.

      INPUT:
      {{{{input}}}}

guardrails:
  redact_pii: true

success:
  weekly_active_users_target: 3   # TODO: set real targets before the pilot
  acceptance_target: 0.8
"""
    path.parent.mkdir(exist_ok=True)
    path.write_text(content, encoding="utf-8")
    console.print(f"[green]✓[/green] scaffolded [bold]{path}[/bold]")
    console.print("  next: write the prompt, set targets, then mark the use case 'piloting' in usecases.yaml")


@app.command()
def run(
    workflow_id: str,
    dir: DirOption = Path("."),
    var: Annotated[
        list[str] | None, typer.Option("--var", help="Variable as name=value (repeatable).")
    ] = None,
    user: Annotated[str | None, typer.Option("--user", help="Attribute the run to this user.")] = None,
) -> None:
    """Execute a workflow under policy and record the evidence."""
    org = _org(dir)
    workflow = org.workflows.get(workflow_id)
    if workflow is None:
        available = ", ".join(sorted(org.workflows)) or "none defined yet"
        err.print(f"[red]unknown workflow:[/red] '{workflow_id}' (available: {available})")
        raise typer.Exit(2)

    variables: dict[str, str] = {}
    for item in var or []:
        if "=" not in item:
            err.print(f"[red]bad --var:[/red] '{item}' — expected name=value")
            raise typer.Exit(2)
        key, value = item.split("=", 1)
        variables[key] = value

    # Attribute the run to a STABLE directory id when the identity resolves; keep
    # the raw string otherwise (unchanged behavior when there is no directory).
    identity = user or getpass.getuser()
    resolved = org.directory.resolve(identity)
    attributed = resolved.id if resolved is not None else identity

    with Store(org.db_path) as store:
        ledger = Ledger(org.ledger_path)
        try:
            result = execute(org, workflow, variables, attributed, store, ledger)
        except VariableError as exc:
            err.print(f"[red]{exc}[/red] — this workflow needs: {', '.join(required_vars(workflow))}")
            raise typer.Exit(2) from None

    if result.status == "completed":
        console.print(
            f"[green]✓ completed[/green] · run [bold]{result.id}[/bold] · {result.model_id} "
            f"· {result.tokens_in:,}→{result.tokens_out:,} tok · {money(result.cost, org.config.currency)} "
            f"· {result.latency_ms:,} ms"
            + (f" · [bold]{result.redactions} PII redaction(s)[/bold]" if result.redactions else "")
        )
        console.print(Rule(style="dim"))
        console.print(result.output or "")
        console.print(Rule(style="dim"))
        console.print(
            f"[dim]close the loop:[/dim] flightdeck feedback {result.id} "
            f"--outcome accepted|edited|rejected --minutes <spent>"
        )
    else:
        style = "yellow" if result.status == "blocked" else "red"
        console.print(f"[{style} bold]✕ {result.status}[/{style} bold] · run {result.id}")
        console.print(f"  {result.reason}")
        console.print("  [dim]the attempt is recorded in the store and the audit ledger[/dim]")
        raise typer.Exit(1)


@app.command()
def tick(
    dir: DirOption = Path("."),
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would run without running it.")
    ] = False,
) -> None:
    """Run every scheduled review-free workflow that is due, at most once per period.

    Meant for cron/CI to invoke as often as it likes: due-ness is a CALENDAR
    PERIOD (daily/weekly/monthly), so a workflow that already ran this period is
    skipped. Even a budget-blocked attempt consumes the period — that is what
    makes a retry storm impossible by construction.

    Exit code is 0 even when runs block or fail: tick is a batch, and a budget
    block is an EXPECTED governance signal, not a failure of the batch (unlike
    `run`, which exits 1 on a block). Only usage/config errors exit 2.
    """
    org = _org(dir)
    scheduled = sorted(
        (workflow for workflow in org.workflows.values() if workflow.schedule is not None),
        key=lambda workflow: workflow.id,
    )
    if not scheduled:
        console.print(
            "[dim]no scheduled workflows — add a [bold]schedule:[/bold] block to a "
            "[bold]review: none[/bold] workflow, then cron this command[/dim]"
        )
        return

    now = datetime.now(UTC)
    config_errors = 0
    with Store(org.db_path) as store:
        ledger = Ledger(org.ledger_path)
        for workflow in scheduled:
            schedule = workflow.schedule  # never None here (filtered) and guaranteed review == "none"
            cadence = schedule.cadence
            if not is_due(cadence, last_run_started_at(store, workflow.id), now):
                console.print(f"[dim]· {workflow.id}: skipped (not due this {cadence})[/dim]")
                continue
            if dry_run:
                console.print(f"· {workflow.id}: [bold]would run[/bold] ({cadence})")
                continue
            try:
                result = execute(org, workflow, schedule.vars, SCHEDULER_USER, store, ledger, now=now)
            except VariableError as exc:
                # A misconfigured workflow must not starve the rest of the batch:
                # report it and carry on, then exit non-zero at the end so cron
                # still sees the config error.
                config_errors += 1
                err.print(
                    f"[red]config error:[/red] {workflow.id}: {exc} — declare them under "
                    f"[bold]schedule.vars[/bold] (needs: {', '.join(required_vars(workflow))})"
                )
                continue
            if result.status == "completed":
                console.print(
                    f"[green]✓ {workflow.id}: ran[/green] ({cadence}) · {result.model_id} "
                    f"· {money(result.cost, org.config.currency)} · run {result.id}"
                )
            elif result.status == "blocked":
                console.print(
                    f"[yellow]✕ {workflow.id}: blocked[/yellow] ({cadence}) · {result.reason} "
                    "[dim](the period is spent; tick will not retry until next period)[/dim]"
                )
            else:
                console.print(f"[red]✕ {workflow.id}: failed[/red] ({cadence}) · {result.reason}")
    if config_errors:
        raise typer.Exit(2)


@app.command()
def feedback(
    run_id: str,
    outcome: Annotated[str, typer.Option(help="accepted | edited | rejected")],
    dir: DirOption = Path("."),
    minutes: Annotated[float | None, typer.Option(help="Human minutes spent reviewing/fixing.")] = None,
    note: Annotated[str, typer.Option(help="Optional note.")] = "",
    by: Annotated[str | None, typer.Option(help="Reviewer (defaults to current user).")] = None,
) -> None:
    """Record what a human did with a run's output — the ROI numbers feed on this."""
    org = _org(dir)
    # Attribute the review to a STABLE directory id when the reviewer resolves;
    # keep the raw handle otherwise (unchanged when there is no directory).
    reviewer = by or getpass.getuser()
    resolved = org.directory.resolve(reviewer)
    with Store(org.db_path) as store:
        try:
            record_feedback(
                store, Ledger(org.ledger_path), run_id, outcome,
                human_minutes=minutes,
                by=resolved.id if resolved is not None else reviewer, note=note,
            )
        except FeedbackError as exc:
            err.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from None
    console.print(
        f"[green]✓[/green] recorded: {run_id} → [bold]{outcome}[/bold]"
        + (f" ({minutes:g} min)" if minutes is not None else "")
    )


@app.command()
def report(
    dir: DirOption = Path("."),
    days: Annotated[int, typer.Option(help="KPI window in days.")] = 30,
    html: Annotated[Path | None, typer.Option(help="Also write the HTML dashboard here.")] = None,
    csv: Annotated[
        Path | None, typer.Option("--csv", help="Also write the per-workflow monthly finance statement (CSV) here.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Emit the report as JSON (for pipelines).")] = False,
) -> None:
    """Adoption, hours saved, cost, value and governance posture — from evidence."""
    org = _org(dir)
    with Store(org.db_path) as store:
        ledger = Ledger(org.ledger_path)
        data = build_report(org, store, ledger, days=days)
        # The finance statement spans all history, not the KPI window — same store.
        statement = monthly_statement(org, store) if csv is not None else None
    ranked = backlog_mod.ranked(org)
    if as_json:
        console.print_json(json.dumps(dataclasses.asdict(data), default=str))
    else:
        terminal.render(data, ranked, console)
    if html is not None:
        html.write_text(html_report.render(org, data, ranked), encoding="utf-8")
        console.print(f"[green]✓[/green] dashboard: [bold]{html}[/bold]")
    if csv is not None:
        csv.write_text(csv_export.render(statement or []), encoding="utf-8")
        console.print(f"[green]✓[/green] finance statement: [bold]{csv}[/bold]")


@app.command("policy")
def policy_check(
    action: Annotated[str, typer.Argument(help="Only 'check' is supported.")],
    workflow_id: str,
    dir: DirOption = Path("."),
) -> None:
    """Dry-run the governance gates for a workflow: what would run, where, and why."""
    if action != "check":
        err.print("[red]usage:[/red] flightdeck policy check <workflow>")
        raise typer.Exit(2)
    org = _org(dir)
    workflow = org.workflows.get(workflow_id)
    if workflow is None:
        err.print(f"[red]unknown workflow:[/red] '{workflow_id}'")
        raise typer.Exit(2)

    console.print(f"\n[bold]{workflow.name}[/bold] · {workflow.department} · tier [bold]{workflow.tier}[/bold]")
    rule = org.config.policy.data_rules[workflow.data_classification]
    constraints = []
    if rule.forbid_training_vendors:
        constraints.append("no vendors that train on data")
    if rule.regions is not None:
        constraints.append(f"regions: {', '.join(rule.regions)}")
    if rule.providers is not None:
        constraints.append(f"providers: {', '.join(rule.providers)}")
    if rule.models is not None:
        constraints.append(f"explicit allowlist: {', '.join(rule.models) or '(empty — fails closed)'}")
    console.print(
        f"  data class [bold]{workflow.data_classification}[/bold] → "
        + ("; ".join(constraints) if constraints else "no constraints")
    )

    cleared = allowed_models(org, workflow)
    if not cleared:
        console.print("  [red bold]✕ no model in the registry may receive this data[/red bold] — runs will block")
        raise typer.Exit(1)
    table = Table(box=None, padding=(0, 2))
    for column in ("cleared model", "tier", "region", "trains on data", "€/Mtok in+out"):
        table.add_column(column, header_style="dim")
    for spec in cleared:
        table.add_row(
            spec.id, spec.tier, spec.region, "yes" if spec.trains_on_data else "no",
            f"{spec.input_cost_per_mtok:g} + {spec.output_cost_per_mtok:g}",
        )
    console.print(table)

    try:
        route = pick(cleared, workflow.tier)
        escalation = " [yellow](escalated: requested tier had no compliant model)[/yellow]" if route.escalated else ""
        console.print(f"  route → [bold]{route.spec.id}[/bold] ({route.spec.model}){escalation}")
    except NoRouteError as exc:
        console.print(f"  [red bold]✕ {exc}[/red bold]")
        raise typer.Exit(1) from None

    console.print(f"  PII redaction: {'[bold]on[/bold]' if should_redact(org, workflow) else 'off'}")
    cap = workflow.guardrails.monthly_budget or org.config.policy.default_monthly_budget
    if cap is not None:
        now = datetime.now().astimezone()
        with Store(org.db_path) as store:
            decision = check_budget(org, workflow, store, now.year, now.month)
            spent = store.month_cost(workflow.id, now.year, now.month)
        state = "[green]ok[/green]" if decision.allowed else "[red bold]exhausted — runs will block[/red bold]"
        console.print(
            f"  budget: {money(spent, org.config.currency)} of {money(cap, org.config.currency, 0)} "
            f"this month → {state}"
        )
    else:
        console.print("  budget: uncapped")
    console.print()


@audit_app.command("verify")
def audit_verify(dir: DirOption = Path(".")) -> None:
    """Re-walk the hash chain; exit 1 if history was tampered with."""
    org = _org(dir)
    result = Ledger(org.ledger_path).verify()
    if result.ok:
        console.print(f"[green]✓ ledger verified[/green] — {result.entries:,} entries, chain intact")
    else:
        err.print(
            f"[red bold]✕ INTEGRITY FAILURE[/red bold] at entry {result.broken_at}: {result.reason} "
            f"({result.entries:,} entries read)"
        )
        raise typer.Exit(1)


@audit_app.command("tail")
def audit_tail(
    dir: DirOption = Path("."),
    n: Annotated[int, typer.Option("-n", help="Entries to show.")] = 12,
) -> None:
    """Show the most recent ledger entries."""
    org = _org(dir)
    all_entries = Ledger(org.ledger_path).entries()
    # `[-n:]` is the whole list when n == 0 (`[-0:]` == `[0:]`), so guard it:
    # asking for 0 (or fewer) entries shows none.
    entries = all_entries[-n:] if n > 0 else []
    for entry in entries:
        stamp = entry["at"][:16].replace("T", " ")
        summary = {k: v for k, v in entry["data"].items() if k != "output_sha256"}
        console.print(f"[dim]{entry['seq']:>6} {stamp}[/dim]  [bold]{entry['event']}[/bold]  {summary}")
    if not all_entries:
        console.print("[dim]ledger is empty[/dim]")


@slack_app.command("post")
def slack_post(run_id: str, dir: DirOption = Path(".")) -> None:
    """Render a run as a Slack Block Kit message.

    Offline-first: with no webhook configured it PRINTS the JSON (pipe it to any
    poster). Set FLIGHTDECK_SLACK_WEBHOOK to POST it via stdlib urllib instead.
    Unknown run → exit 2.
    """
    org = _org(dir)
    with Store(org.db_path) as store:
        run = store.run(run_id)
        if run is None:
            err.print(f"[red]unknown run:[/red] {run_id}")
            raise typer.Exit(2)
        workflow = org.workflows.get(run.workflow_id)
        if workflow is None:
            err.print(f"[red]run {run_id} references an unknown workflow:[/red] {run.workflow_id}")
            raise typer.Exit(2)
        message = slack.build_review_message(run, workflow, org)

    webhook = os.environ.get(SLACK_WEBHOOK_ENV)
    if not webhook:
        console.print_json(json.dumps(message))
        return
    try:
        slack.post_review(message, transport=slack.WebhookTransport(webhook))
    except SlackError as exc:
        err.print(f"[red]slack post failed:[/red] {exc}")
        raise typer.Exit(1) from None
    console.print(f"[green]✓[/green] posted run [bold]{run_id}[/bold] to Slack")


@slack_app.command("handle")
def slack_handle(
    dir: DirOption = Path("."),
    minutes: Annotated[
        float | None, typer.Option(help="Override minutes (else from the modal, else org default).")
    ] = None,
) -> None:
    """Read a Slack interaction payload as JSON on STDIN and record the feedback.

    Closes the loop from an Accept/Edited/Reject click through the SAME feedback
    path as `flightdeck feedback`. Malformed payload / unknown run → exit 2.
    """
    org = _org(dir)
    try:
        payload = slack.parse_interaction_form(sys.stdin.read())
    except SlackError as exc:
        err.print(f"[red]bad payload:[/red] {exc}")
        raise typer.Exit(2) from None
    with Store(org.db_path) as store:
        try:
            entry = slack.apply_interaction(payload, store, Ledger(org.ledger_path), org, minutes=minutes)
        except (SlackError, FeedbackError) as exc:
            err.print(f"[red]cannot record feedback:[/red] {exc}")
            raise typer.Exit(2) from None
    console.print(
        f"[green]✓[/green] recorded via Slack: {entry.run_id} → [bold]{entry.outcome}[/bold] by {entry.by}"
        + (f" ({entry.human_minutes:g} min)" if entry.human_minutes is not None else "")
    )


if __name__ == "__main__":
    app()
