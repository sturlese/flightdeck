"""Finance export — the monthly statement as CSV, for the controller's own tooling.

A fourth report surface alongside terminal/HTML, and the dumbest of them: it
renders the rows metrics.py already computed and does nothing else. Like the HTML
dashboard (see the docstring in ``report/__init__.py``), it computes no KPI — if a
number here can't be traced to ``metrics.monthly_statement``, it doesn't exist.

Determinism is the whole point of a finance file: a stable header, a fixed column
order, fixed float formatting (money and hours to 2 decimals, reviewed share as a
fraction to 4), and rows already sorted by (workflow, month). Reopen the export a
month later and the diff is only the new rows. Money is written as a plain machine
number (``-3.00``, not ``−€3``) so a spreadsheet parses it without cleanup.
"""

import csv
import io
from collections.abc import Iterable

from flightdeck.metrics import MonthlyStatementRow

#: Column order is part of the contract — appended to, never reordered.
HEADER: tuple[str, ...] = (
    "workflow_id",
    "workflow_name",
    "department",
    "month",
    "currency",
    "runs_completed",
    "reviewed",
    "reviewed_pct",
    "hours_saved",
    "value",
    "ai_cost",
    "net",
)


def _fixed(value: float, decimals: int) -> str:
    """Fixed-decimal string, with negative zero normalized to ``0`` so a rounding
    artifact never writes ``-0.00`` into a finance file."""
    rounded = round(value, decimals)
    if rounded == 0:
        rounded = 0.0
    return f"{rounded:.{decimals}f}"


def render(rows: Iterable[MonthlyStatementRow]) -> str:
    """Serialize statement rows to CSV text (header first). Unix newlines, so the
    output is byte-stable across platforms."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADER)
    for row in rows:
        writer.writerow(
            (
                row.workflow_id,
                row.workflow_name,
                row.department,
                row.month,
                row.currency,
                row.runs_completed,
                row.reviewed,
                _fixed(row.reviewed_pct, 4),
                _fixed(row.hours_saved, 2),
                _fixed(row.value, 2),
                _fixed(row.ai_cost, 2),
                _fixed(row.net, 2),
            )
        )
    return buffer.getvalue()
