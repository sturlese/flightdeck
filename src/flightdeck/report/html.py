"""The executive dashboard — one self-contained HTML file.

Design constraints, in order: (1) trustworthy — every number traces to a
metrics.py formula and the page says where; (2) portable — zero external
requests, so it can be mailed to a CFO, opened offline, printed, or archived
next to the ledger it summarizes; (3) quiet — thin marks, hairline chrome,
ink-colored text, one accent hue; the loudest thing on the page must be the
data. Light/dark follows the OS. Charts come from charts.py; this module only
lays out and formats.
"""

from datetime import datetime, timedelta

from jinja2 import Environment

from flightdeck import __version__
from flightdeck.backlog import ScoredUseCase
from flightdeck.config import Org
from flightdeck.format import HEALTH_LABELS, SYMBOLS, money
from flightdeck.metrics import OrgReport
from flightdeck.report import charts


def _pct(value: float | None) -> str:
    return f"{value:.0%}" if value is not None else "—"


def render(org: Org, report: OrgReport, backlog: list[ScoredUseCase]) -> str:
    currency = report.currency
    # Complete weeks only: a Wednesday's half-week reads as a decline, and an
    # executive chart must not imply one. At most a quarter of context.
    weeks = [point for point in report.weekly if point.start + timedelta(days=7) <= report.until.date()][-14:]
    labels = [point.week.split("-")[1] for point in weeks]

    hours_chart = charts.line_chart(
        "hours", labels, [point.hours_saved for point in weeks], " h", "hours saved"
    )
    spend_chart = charts.column_chart(
        labels, [point.cost for point in weeks], SYMBOLS.get(currency, ""), "AI spend"
    )
    value_chart = charts.hbar_chart(
        [(entry.name, round(entry.net_value)) for entry in report.workflows],
        SYMBOLS.get(currency, ""),
    )
    outcome = charts.outcome_chart(
        [(entry.name, entry.accepted, entry.edited, entry.rejected) for entry in report.workflows]
    )

    workdays = report.total_hours_saved / 8
    spend_share = (
        f"{report.total_ai_cost / report.total_value:.1%} of the value it enabled"
        if report.total_value > 0
        else "no measured value yet"
    )

    workflow_rows = []
    for entry in report.workflows:
        css, label = HEALTH_LABELS[entry.health]
        workflow_rows.append(
            {
                "name": entry.name,
                "department": entry.department,
                "data_class": entry.data_classification,
                "tier": entry.tier,
                "runs": f"{entry.runs_completed:,}",
                "users": entry.active_users,
                "adoption": _pct(entry.adoption),
                "acceptance": _pct(entry.acceptance_rate),
                "hours": f"{entry.hours_saved:,.1f}",
                "cost": money(entry.ai_cost, currency),
                "net": money(entry.net_value, currency),
                "health_css": css,
                "health": label,
            }
        )

    weekly_rows = [
        {
            "week": point.week,
            "runs": f"{point.runs:,}",
            "users": point.active_users,
            "hours": f"{point.hours_saved:,.1f}",
            "cost": money(point.cost, currency),
        }
        for point in weeks
    ]

    backlog_rows = [
        {
            "name": item.case.name,
            "id": item.case.id,
            "department": item.case.department,
            "status": item.case.status,
            "value": money(item.monthly_value, currency, 0),
            "feasibility": f"×{item.feasibility:.2f}",
            "risk": f"×{item.risk_discount:.2f}",
            "effort": f"{item.case.effort_weeks:g} wk",
            "score": f"{item.score:,.0f}",
        }
        for item in backlog[:5]
    ]

    gov = report.governance
    region_mix = ", ".join(
        f"{region} {count / max(sum(gov.region_mix.values()), 1):.0%}"
        for region, count in sorted(gov.region_mix.items(), key=lambda pair: -pair[1])
    ) or "—"

    env = Environment(autoescape=True)
    return env.from_string(_TEMPLATE).render(
        org_name=report.org_name,
        window_days=report.window_days,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        version=__version__,
        ledger_ok=gov.ledger_ok,
        ledger_entries=f"{gov.ledger_entries:,}",
        kpis=[
            {"label": f"Hours saved · {report.window_days}d", "value": f"{report.total_hours_saved:,.0f} h",
             "sub": f"≈ {workdays:,.1f} working days returned"},
            {"label": "Net value", "value": money(report.total_net_value, currency, 0),
             "sub": f"after {money(report.total_ai_cost, currency)} of AI spend"},
            {"label": "Active users", "value": f"{report.active_users}",
             "sub": f"{report.total_runs_completed:,} governed runs across {len(report.workflows)} workflows"},
            {"label": "AI spend", "value": money(report.total_ai_cost, currency),
             "sub": spend_share},
        ],
        hours_chart=hours_chart,
        spend_chart=spend_chart,
        value_chart=value_chart,
        outcome_chart=outcome,
        workflow_rows=workflow_rows,
        weekly_rows=weekly_rows,
        backlog_rows=backlog_rows,
        promote_hint=backlog_rows[0]["id"] if backlog_rows else None,
        gov=gov,
        region_mix=region_mix,
        no_training=_pct(gov.no_training_share),
        currency=currency,
    )


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ org_name }} · flightdeck</title>
<style>
:root{
  --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10);
  --s1:#2a78d6; --good:#0ca30c; --warn:#fab219; --crit:#d03b3b; --up:#006300;
}
@media (prefers-color-scheme: dark){:root{
  --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7;
  --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
  --s1:#3987e5; --up:#0ca30c;
}}
*{box-sizing:border-box;margin:0}
body{background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;padding:28px 20px 48px}
.wrap{max-width:1120px;margin:0 auto}
header{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px 16px;margin-bottom:20px}
h1{font-size:20px;font-weight:650}
.sub{color:var(--ink2)}
.chip{margin-left:auto;font-size:12.5px;color:var(--ink2);border:1px solid var(--border);border-radius:999px;padding:4px 12px;background:var(--surface)}
.chip .ok{color:var(--up);font-weight:650}
.chip .bad{color:var(--crit);font-weight:650}
.grid{display:grid;gap:14px}
.kpis{grid-template-columns:repeat(auto-fit,minmax(200px,1fr));margin-bottom:14px}
.two{grid-template-columns:repeat(auto-fit,minmax(380px,1fr));margin-bottom:14px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px 20px;min-width:0}
.kpi .label{font-size:12.5px;color:var(--ink2)}
.kpi .value{font-size:30px;font-weight:650;letter-spacing:-.01em;margin:2px 0}
.kpi .subline{font-size:12px;color:var(--muted)}
h2{font-size:13.5px;font-weight:650;color:var(--ink2);margin-bottom:10px}
.fd-chart{width:100%;height:auto;display:block}
.fd-grid{stroke:var(--grid);stroke-width:1}
.fd-axis{stroke:var(--axis);stroke-width:1}
.fd-tick{fill:var(--muted);font-size:10.5px;font-variant-numeric:tabular-nums}
.fd-cat{fill:var(--ink2);font-size:11.5px}
.fd-line{stroke:var(--s1);stroke-width:2;fill:none;stroke-linejoin:round;stroke-linecap:round}
.fd-area{fill:var(--s1);opacity:.1}
.fd-dot{fill:var(--s1);stroke:var(--surface);stroke-width:2}
.fd-cross{stroke:var(--axis);stroke-width:1}
.fd-bar{fill:var(--s1)}
.fd-bar:hover,.fd-bar:focus,.fd-seg:hover,.fd-seg:focus{opacity:.8;outline:none}
.fd-hit{fill:transparent;outline:none}
.fd-endlabel{fill:var(--ink);font-size:11.5px;font-weight:650}
.fd-value{fill:var(--ink);font-size:11.5px;font-variant-numeric:tabular-nums}
.fd-good{fill:var(--good)}.fd-warn{fill:var(--warn)}.fd-crit{fill:var(--crit)}
.fd-seglabel{font-size:10.5px;font-weight:650}
.fd-on-good,.fd-on-crit{fill:#fff}.fd-on-warn{fill:#0b0b0b}
.fd-empty,.fd-empty-row{color:var(--muted);fill:var(--muted);font-size:12px;font-style:italic}
.legend{display:flex;gap:16px;font-size:12px;color:var(--ink2);margin-bottom:8px;flex-wrap:wrap}
.legend .sw{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600;text-align:left;padding:6px 10px;border-bottom:1px solid var(--grid)}
td{padding:7px 10px;border-bottom:1px solid var(--grid);vertical-align:top}
tr:last-child td{border-bottom:none}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.wfname{font-weight:600}
.dept{color:var(--muted);font-size:11.5px}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--ink2);border:1px solid var(--border);border-radius:999px;padding:2px 9px;white-space:nowrap}
.pill i{width:7px;height:7px;border-radius:50%;display:inline-block}
.pill-good i{background:var(--good)}.pill-warn i{background:var(--warn)}.pill-crit i{background:var(--crit)}.pill-muted i{background:var(--muted)}
.klass{font-size:11px;color:var(--muted);border:1px solid var(--grid);border-radius:5px;padding:1px 6px;white-space:nowrap}
.scroll-x{overflow-x:auto}
.gov{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.gov .g .v{font-size:21px;font-weight:650}
.gov .g .l{font-size:11.5px;color:var(--muted)}
details{margin-top:10px}
summary{font-size:12px;color:var(--muted);cursor:pointer}
.hint{font-size:12px;color:var(--muted);margin-top:10px}
.hint code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;background:var(--page);border:1px solid var(--grid);border-radius:5px;padding:1px 6px}
footer{color:var(--muted);font-size:12px;margin-top:22px;display:flex;flex-wrap:wrap;gap:6px 18px}
.fd-tipbox{position:fixed;z-index:10;pointer-events:none;opacity:0;transition:opacity .08s;background:var(--surface);border:1px solid var(--border);border-radius:9px;box-shadow:0 4px 18px rgba(0,0,0,.13);padding:9px 12px;font-size:12px;max-width:280px}
.fd-tip-title{color:var(--muted);margin-bottom:3px}
.fd-tip-row{display:flex;gap:14px;justify-content:space-between;align-items:baseline}
.fd-tip-row span{color:var(--ink2)}
.fd-tip-row span::before{content:"";display:inline-block;width:10px;height:2.5px;border-radius:2px;background:var(--muted);margin-right:6px;vertical-align:3px}
.fd-tip-row span.key-s1::before{background:var(--s1)}
.fd-tip-row span.key-good::before{background:var(--good)}
.fd-tip-row span.key-warn::before{background:var(--warn)}
.fd-tip-row span.key-crit::before{background:var(--crit)}
.fd-tip-row strong{color:var(--ink)}
@media print{.fd-tipbox{display:none}body{padding:0}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>{{ org_name }}</h1>
  <span class="sub">AI program · last {{ window_days }} days</span>
  <span class="chip">{% if ledger_ok %}<span class="ok">✓</span> audit ledger verified · {{ ledger_entries }} hash-chained entries{% else %}<span class="bad">✕</span> LEDGER INTEGRITY FAILED — run <code>flightdeck audit verify</code>{% endif %}</span>
</header>

<div class="grid kpis">
  {% for kpi in kpis %}
  <div class="card kpi">
    <div class="label">{{ kpi.label }}</div>
    <div class="value">{{ kpi.value }}</div>
    <div class="subline">{{ kpi.sub }}</div>
  </div>
  {% endfor %}
</div>

<div class="grid two">
  <div class="card">
    <h2>Hours saved per week</h2>
    {{ hours_chart|safe }}
  </div>
  <div class="card">
    <h2>AI spend per week</h2>
    {{ spend_chart|safe }}
    <details>
      <summary>Weekly data table</summary>
      <div class="scroll-x"><table>
        <thead><tr><th>Week</th><th class="n">Runs</th><th class="n">Users</th><th class="n">Hours saved</th><th class="n">AI spend</th></tr></thead>
        <tbody>{% for row in weekly_rows %}<tr><td>{{ row.week }}</td><td class="n">{{ row.runs }}</td><td class="n">{{ row.users }}</td><td class="n">{{ row.hours }}</td><td class="n">{{ row.cost }}</td></tr>{% endfor %}</tbody>
      </table></div>
    </details>
  </div>
  <div class="card">
    <h2>Net value by workflow</h2>
    {{ value_chart|safe }}
  </div>
  <div class="card">
    <h2>Review outcomes by workflow</h2>
    <div class="legend">
      <span><i class="sw" style="background:var(--good)"></i>✓ accepted</span>
      <span><i class="sw" style="background:var(--warn)"></i>✎ edited</span>
      <span><i class="sw" style="background:var(--crit)"></i>✕ rejected</span>
    </div>
    {{ outcome_chart|safe }}
  </div>
</div>

<div class="card" style="margin-bottom:14px">
  <h2>Workflows · last {{ window_days }} days</h2>
  <div class="scroll-x">
  <table>
    <thead><tr>
      <th>Workflow</th><th>Data class</th><th class="n">Runs</th><th class="n">Users</th>
      <th class="n">Adoption</th><th class="n">Acceptance</th><th class="n">Hours saved</th>
      <th class="n">AI cost</th><th class="n">Net value</th><th>Health</th>
    </tr></thead>
    <tbody>
    {% for row in workflow_rows %}
    <tr>
      <td><span class="wfname">{{ row.name }}</span><br><span class="dept">{{ row.department }} · {{ row.tier }}</span></td>
      <td><span class="klass">{{ row.data_class }}</span></td>
      <td class="n">{{ row.runs }}</td>
      <td class="n">{{ row.users }}</td>
      <td class="n">{{ row.adoption }}</td>
      <td class="n">{{ row.acceptance }}</td>
      <td class="n">{{ row.hours }}</td>
      <td class="n">{{ row.cost }}</td>
      <td class="n">{{ row.net }}</td>
      <td><span class="pill pill-{{ row.health_css }}"><i></i>{{ row.health }}</span></td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
  </div>
</div>

<div class="grid two">
  <div class="card">
    <h2>Governance</h2>
    <div class="gov">
      <div class="g"><div class="v">{{ gov.blocked_policy }}</div><div class="l">policy blocks · window ({{ gov.blocked_policy_all }} all-time)</div></div>
      <div class="g"><div class="v">{{ gov.blocked_budget }}</div><div class="l">budget blocks · window ({{ gov.blocked_budget_all }} all-time)</div></div>
      <div class="g"><div class="v">{{ gov.failed }}</div><div class="l">failed runs · window</div></div>
      <div class="g"><div class="v">{{ gov.redactions }}</div><div class="l">PII redactions before egress</div></div>
      <div class="g"><div class="v">{{ region_mix }}</div><div class="l">model residency · completed runs</div></div>
      <div class="g"><div class="v">{{ no_training }}</div><div class="l">runs on non-training vendors</div></div>
    </div>
    <div class="hint">Every event above is one line in the hash-chained ledger — verify with <code>flightdeck audit verify</code></div>
  </div>
  <div class="card">
    <h2>Backlog · next best use cases</h2>
    <div class="scroll-x">
    <table>
      <thead><tr><th>Use case</th><th class="n">Value/mo</th><th class="n">Feasibility</th><th class="n">Risk</th><th class="n">Effort</th><th class="n">Score</th></tr></thead>
      <tbody>
      {% for row in backlog_rows %}
      <tr>
        <td><span class="wfname">{{ row.name }}</span><br><span class="dept">{{ row.department }} · {{ row.status }}</span></td>
        <td class="n">{{ row.value }}</td>
        <td class="n">{{ row.feasibility }}</td>
        <td class="n">{{ row.risk }}</td>
        <td class="n">{{ row.effort }}</td>
        <td class="n">{{ row.score }}</td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
    </div>
    {% if promote_hint %}<div class="hint">Promote the winner: <code>flightdeck promote {{ promote_hint }}</code></div>{% endif %}
  </div>
</div>

<footer>
  <span>generated by flightdeck v{{ version }} · {{ generated }}</span>
  <span>formulas: docs/metrics.md — conservative by construction</span>
  <span>evidence: .flightdeck/runs.sqlite3 · .flightdeck/ledger.jsonl</span>
</footer>
</div>

<script>
(function () {
  var tip = document.createElement("div");
  tip.className = "fd-tipbox";
  tip.setAttribute("role", "status");
  document.body.appendChild(tip);

  function show(el, x, y) {
    var data;
    try { data = JSON.parse(el.dataset.tip); } catch (e) { return; }
    tip.replaceChildren();
    var title = document.createElement("div");
    title.className = "fd-tip-title";
    title.textContent = data.t;               // untrusted labels: textContent only
    tip.appendChild(title);
    (data.r || []).forEach(function (row) {
      var line = document.createElement("div");
      line.className = "fd-tip-row";
      var key = document.createElement("span");
      if (row[2]) key.className = "key-" + row[2];
      key.textContent = row[0];
      var value = document.createElement("strong");
      value.textContent = row[1];
      line.appendChild(key);
      line.appendChild(value);
      tip.appendChild(line);
    });
    tip.style.opacity = "1";
    var box = tip.getBoundingClientRect();
    tip.style.left = Math.min(x + 14, window.innerWidth - box.width - 8) + "px";
    tip.style.top = Math.max(y - box.height - 12, 8) + "px";
  }
  function hide() { tip.style.opacity = "0"; }
  function crosshair(el, on) {
    if (!el.dataset.cross) return;
    var line = document.getElementById("cross-" + el.dataset.cross);
    if (!line) return;
    if (on) { line.setAttribute("x1", el.dataset.x); line.setAttribute("x2", el.dataset.x); }
    line.setAttribute("opacity", on ? "1" : "0");
  }
  document.querySelectorAll("[data-tip]").forEach(function (el) {
    el.addEventListener("pointermove", function (ev) { show(el, ev.clientX, ev.clientY); crosshair(el, true); });
    el.addEventListener("pointerleave", function () { hide(); crosshair(el, false); });
    el.addEventListener("focus", function () {
      var box = el.getBoundingClientRect();
      show(el, box.left + box.width / 2, box.top);
      crosshair(el, true);
    });
    el.addEventListener("blur", function () { hide(); crosshair(el, false); });
  });
})();
</script>
</body>
</html>
"""
