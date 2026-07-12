"""The operator's report — same numbers as the dashboard, in the terminal."""

from datetime import timedelta

from rich.console import Console
from rich.table import Table

from flightdeck.backlog import ScoredUseCase
from flightdeck.metrics import OrgReport
from flightdeck.report.html import _HEALTH_LABELS, money

_SPARK = "▁▂▃▄▅▆▇█"
_HEALTH_STYLE = {"good": "green", "warn": "yellow", "crit": "red", "muted": "dim"}


def _spark(values: list[float]) -> str:
    # hours_saved can be negative (a rejection-heavy week earns negative minutes),
    # so scale off a non-negative top and clamp the glyph index to [0, 7]: a
    # negative value floors to the lowest bar instead of a wrong glyph or an
    # IndexError from negative indexing.
    top = max(max(values), 0.0) or 1.0
    return "".join(_SPARK[min(max(int(v / top * 7.999), 0), 7)] for v in values)


def render(report: OrgReport, backlog: list[ScoredUseCase], console: Console) -> None:
    currency = report.currency
    gov = report.governance

    console.print()
    console.print(f"[bold]{report.org_name}[/bold] · AI program · last {report.window_days} days")
    ledger_state = (
        f"[green]✓ ledger verified[/green] · {gov.ledger_entries:,} entries"
        if gov.ledger_ok
        else "[red bold]✕ LEDGER INTEGRITY FAILED[/red bold]"
    )
    console.print(f"[dim]{ledger_state}[/dim]\n")

    tiles = [
        ("Hours saved", f"{report.total_hours_saved:,.0f} h"),
        ("Net value", money(report.total_net_value, currency, 0)),
        ("Active users", str(report.active_users)),
        ("AI spend", money(report.total_ai_cost, currency)),
        ("Runs", f"{report.total_runs_completed:,}"),
    ]
    kpis = Table.grid(padding=(0, 4))
    for _ in tiles:
        kpis.add_column(justify="left")
    kpis.add_row(*[f"[dim]{label}[/dim]" for label, _ in tiles])
    kpis.add_row(*[f"[bold]{value}[/bold]" for _, value in tiles])
    console.print(kpis)

    weeks = [  # complete weeks only, same presentation rule as the dashboard
        point for point in report.weekly if point.start + timedelta(days=7) <= report.until.date()
    ][-14:]
    if weeks:
        console.print(
            f"\n[dim]hours/week[/dim]  {_spark([point.hours_saved for point in weeks])}"
            f"  [dim]{weeks[0].week} → {weeks[-1].week}[/dim]"
        )
        console.print(f"[dim]spend/week[/dim]  {_spark([point.cost for point in weeks])}")

    table = Table(title=None, pad_edge=False, box=None, show_edge=False, padding=(0, 2))
    for column, justify in (
        ("workflow", "left"), ("class", "left"), ("runs", "right"), ("users", "right"),
        ("adoption", "right"), ("accept", "right"), ("hours", "right"),
        ("net", "right"), ("health", "left"),
    ):
        table.add_column(column, justify=justify, style=None, header_style="dim")
    for entry in report.workflows:
        css, label = _HEALTH_LABELS[entry.health]
        table.add_row(
            entry.name,
            entry.data_classification,
            f"{entry.runs_completed:,}",
            str(entry.active_users),
            f"{entry.adoption:.0%}" if entry.adoption is not None else "—",
            f"{entry.acceptance_rate:.0%}" if entry.acceptance_rate is not None else "—",
            f"{entry.hours_saved:,.1f}",
            money(entry.net_value, currency, 0),
            f"[{_HEALTH_STYLE[css]}]{label}[/{_HEALTH_STYLE[css]}]",
        )
    console.print()
    console.print(table)

    console.print(
        f"\n[dim]governance[/dim]  policy blocks {gov.blocked_policy} (window) / {gov.blocked_policy_all} (all-time)"
        f" · budget blocks {gov.blocked_budget} / {gov.blocked_budget_all}"
        f" · failed {gov.failed} · redactions {gov.redactions}"
    )
    if gov.region_mix:
        total = sum(gov.region_mix.values())
        mix = " · ".join(f"{region} {count / total:.0%}" for region, count in sorted(gov.region_mix.items()))
        training = f"{gov.no_training_share:.0%}" if gov.no_training_share is not None else "—"
        console.print(f"[dim]residency[/dim]   {mix} · non-training vendors {training}")

    if backlog:
        console.print("\n[dim]backlog · next best use cases[/dim]")
        ranked = Table(box=None, show_edge=False, padding=(0, 2))
        for column, justify in (
            ("use case", "left"), ("value/mo", "right"), ("feas", "right"),
            ("risk", "right"), ("effort", "right"), ("score", "right"),
        ):
            ranked.add_column(column, justify=justify, header_style="dim")
        for item in backlog[:5]:
            ranked.add_row(
                f"{item.case.name} [dim]({item.case.department})[/dim]",
                money(item.monthly_value, currency, 0),
                f"×{item.feasibility:.2f}",
                f"×{item.risk_discount:.2f}",
                f"{item.case.effort_weeks:g}wk",
                f"[bold]{item.score:,.0f}[/bold]",
            )
        console.print(ranked)
    console.print()
