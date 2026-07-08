"""Report rendering: the same OrgReport, three surfaces.

- terminal.py — the operator's view (rich tables in the CLI)
- html.py + charts.py — the executive dashboard: one self-contained HTML file,
  no CDNs, no external requests, light/dark aware, printable and mailable.

Rendering never computes a KPI. Everything comes in as an OrgReport from
metrics.py — if a number on the dashboard can't be traced to a metrics
function, it doesn't exist.
"""
