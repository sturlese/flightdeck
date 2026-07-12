# Metrics — every formula, written down

A number nobody can recompute is a number nobody should present to a board. This file is the
contract behind every figure flightdeck reports: the formula, the inputs, and the deliberate
biases. The implementation lives in [`metrics.py`](../src/flightdeck/metrics.py) and
[`backlog.py`](../src/flightdeck/backlog.py); the test suite pins each rule.

The standing bias is **conservative**: when information is missing, the model understates
savings. An impressive number you have to walk back costs more credibility than it ever bought.

## Inputs

Three sources, nothing else:

1. **Declared baselines** — each workflow's `baseline:` block: `minutes_per_task`,
   `tasks_per_month`, `hourly_cost` (falling back to the org default).
2. **Recorded runs** — status, model, tokens, cost, redactions, timestamps.
3. **Recorded human feedback** — `accepted | edited | rejected` plus `human_minutes`
   actually spent on the output.

No survey answers, no vendor-reported "productivity scores", no model-estimated anything.

## Run cost

```
cost = tokens_in / 1e6 × input_cost_per_mtok  +  tokens_out / 1e6 × output_cost_per_mtok
```

Prices come from the org's model registry, in the org currency (single currency org-wide — a
deliberate simplification; if you operate multi-currency, normalize in the registry).

## Minutes saved per run

`baseline` below is the workflow's `minutes_per_task`; `default` is the org's
`default_review_minutes` (what a review costs when nobody timed it).

| Run state | Feedback | Minutes earned |
|---|---|---|
| completed | accepted, minutes recorded `m` | `baseline − m` |
| completed | accepted, no minutes | `baseline − default` |
| completed | edited, minutes recorded `m` | `baseline − m` *(can be negative — fixing can cost more than doing)* |
| completed | rejected, minutes `m` (or default) | `−m` — **negative**: review time spent, nothing produced |
| completed | none, review = `human_in_the_loop` | `0` — unmeasured is not saved |
| completed | none, review = `spot_check` | `baseline − default` |
| completed | none, review = `none` | `baseline` — the task no longer takes human time *(declare this consciously)* |
| blocked / failed | — | `0` (their cost still counts on the spend side) |

Per-run earnings are capped at `baseline` — a run cannot save more than the task took.

## The monthly cap

Per workflow, per calendar month:

```
Σ positive minutes  ≤  minutes_per_task × tasks_per_month
```

A workflow can never claim more time than the task volume it declared exists. Credit is
chronological; negative minutes (rejected outputs) always count. This is what makes the
dashboard immune to runaway loops and duplicate runs: 300 copies of the same digest are
worth at most one month of digests, and the incident still shows — in the cost chart and
the blocked-run count, where it belongs. ([ADR 004](decisions/004-conservative-savings-model.md))

## Hours, value, net value

```
hours_saved = Σ earned_minutes / 60
value       = hours_saved × hourly_cost          (workflow's, else org default)
net_value   = value − ai_cost                    (runs' recorded cost in the window)
```

`net_value` ignores platform/subscription overhead and the program's own staffing — those are
budget lines, not per-workflow evidence. Add them in your board narrative, not in the tool.

## Finance export (CSV)

`flightdeck report --csv <path>` emits one row per **(workflow, calendar-month)** across all
history — a statement spans time, so unlike the dashboard it ignores the KPI window. Every
column reuses the formulas above (same `earned_minutes`, same monthly cap, same hourly cost),
so the file ties out to the dashboard to the cent. Only months in which a workflow had at least
one run appear; rows are sorted by `(workflow_id, month)`.

| Column | Meaning |
|---|---|
| `workflow_id`, `workflow_name`, `department` | identity, so the file reads on its own |
| `month` | calendar month, `YYYY-MM` (bucket = `started_at`'s month) |
| `currency` | the org currency (prices are single-currency org-wide) |
| `runs_completed` | completed runs that month |
| `reviewed` | of those, how many a human gave feedback on |
| `reviewed_pct` | `reviewed / runs_completed` as a fraction, 4 decimals (`0` when no completed runs) |
| `hours_saved` | `Σ earned_minutes / 60` for the month, cap applied — 2 decimals |
| `value` | `hours_saved × hourly_cost` — 2 decimals |
| `ai_cost` | `Σ cost` of **all** the month's runs (completed + failed carry cost; blocked cost 0) — 2 decimals |
| `net` | `value − ai_cost` — 2 decimals |

Money and hours are plain machine numbers (`-3.00`, not `−€3`) so a spreadsheet parses them
without cleanup. The serializer ([`report/csv_export.py`](../src/flightdeck/report/csv_export.py))
computes nothing — it renders the rows [`metrics.monthly_statement`](../src/flightdeck/metrics.py)
produced, exactly like the HTML dashboard renders `OrgReport`.

## Adoption

```
weekly_active_avg = mean(distinct users per ISO week, last ≤ 4 COMPLETE weeks with activity)
adoption          = weekly_active_avg / eligible_users
```

`eligible_users` resolves: explicit on the workflow → department headcount → unknown (the
report shows “—” rather than inventing a denominator). Weeks in progress never count — a
Wednesday must not read as a decline.

## Acceptance

```
acceptance_rate = (accepted + edited) / reviewed
```

Edited counts as acceptance — the draft was useful enough to fix — but its *minutes* already
discounted the fixing time above. Quality and time are kept in separate columns on purpose.

## Health

Against the workflow's declared `success:` targets (acceptance target, weekly-active-users
target), using the worst ratio:

| worst ratio | health |
|---|---|
| ≥ 1.0 | `healthy` |
| ≥ 0.75 | `watch` |
| < 0.75 | `underperforming` |
| no reviews yet | `no_data` |
| no targets declared | `no_target` |

Targets are declared in the workflow file *before* the pilot, in version control — moving a
goalpost is a visible diff with an author.

## Backlog score

```
monthly_value = task_minutes/60 × tasks_per_month × hourly_cost × automation_potential
feasibility   = (data_readiness + process_stability) / 10        → 0.2 … 1.0
risk_discount = 1 − (risk − 1) × 0.15                            → 1.0 … 0.4
score         = monthly_value × feasibility × risk_discount / max(effort_weeks, 0.5)
```

A prioritization aid, not an oracle: its job is to force the inputs into the open (*why is
data_readiness a 2? says who?*) and keep pet projects honest.

## Worked example

Support reply drafting: baseline 12 min, 640 tasks/month, €38/h. In the window: 400 completed
runs, 340 reviewed — 220 accepted (avg 2.1 min), 96 edited (avg 6.4 min), 24 rejected
(avg 3 min); AI cost €1.02.

```
accepted:  220 × (12 − 2.1)  = +2,178 min
edited:     96 × (12 − 6.4)  =   +538 min
rejected:   24 × (−3)        =    −72 min
unreviewed: 60 × 0           =      0
                              ─────────
                              2,644 min = 44.1 h   (cap: 12 × 640 = 7,680 min — not binding)
value  = 44.1 × 38 = €1,676
net    = 1,676 − 1.02 ≈ €1,675
```

## Known limitations, on purpose

- **Baselines are self-declared.** Recalibrate them quarterly by timing the manual task again;
  a stale baseline inflates (or deflates) everything downstream.
- **Minutes are self-reported.** The default-review fallback bounds the damage of lazy
  reporting, but a team that never records minutes gets coarser numbers.
- **`review: none` is trust, declared.** The digest that nobody reads earns its full baseline.
  If that makes you uncomfortable, that discomfort is correct — use `spot_check`.
- **Time is the only benefit counted.** Revenue effects, quality lift and morale are real and
  belong in the narrative — with their own evidence, not smuggled into an hours number.
