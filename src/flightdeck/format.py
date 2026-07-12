"""Shared presentation helpers — the one place a number becomes a string.

``money()`` and the health-label mapping are consumed by every surface: CLI
messages, the terminal report, Slack messages, the HTML dashboard. They live
here, dependency-free, so an integration never has to import the HTML renderer
to format a currency amount, and every surface stays character-for-character
consistent (same U+2212 minus, same symbol fallback) — a KPI must read the
same in the terminal as on the page a CFO gets mailed.
"""

SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}

#: WorkflowReport.health → (status key, human label). The status key selects the
#: CSS class on the dashboard and the text style in the terminal report.
HEALTH_LABELS = {
    "healthy": ("good", "healthy"),
    "watch": ("warn", "watch"),
    "underperforming": ("crit", "underperforming"),
    "no_data": ("muted", "no reviews yet"),
    "no_target": ("muted", "no targets set"),
}


def money(value: float, currency: str, decimals: int | None = None) -> str:
    symbol = SYMBOLS.get(currency, f"{currency} ")
    if decimals is None:
        decimals = 2 if 0 < abs(value) < 20 else 0
    amount = f"{abs(value):,.{decimals}f}"
    # Decide the sign from the ROUNDED value: a tiny negative (e.g. a near
    # break-even net that rounds to 0.00) must read as "0", never "−0.00".
    negative = round(value, decimals) < 0
    return f"{'−' if negative else ''}{symbol}{amount}"
