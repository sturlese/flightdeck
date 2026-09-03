"""Chart rendering edge cases — cross-surface consistency of money labels, and
the weekly line chart's numeric domain.

hours_saved per week can be negative (docs/metrics.md: a rejected run earns −m
minutes), and that series feeds line_chart on the dashboard, so its y-domain
must reach below zero — the terminal sparkline already does (test_terminal.py).
"""

import re

from flightdeck.format import money
from flightdeck.report.charts import _H, _PAD_B, _PAD_T, column_chart, hbar_chart, line_chart

# The band a point may legally occupy: outside it the browser clips against the
# viewBox and the value disappears from the page.
_BAND = (_PAD_T, _H - _PAD_B)


def _line_ys(svg: str) -> list[float]:
    path = re.search(r'class="fd-line" d="([^"]+)"', svg).group(1)
    return [float(y) for y in re.findall(r"[ML]-?[\d.]+ (-?[\d.]+)", path)]


def test_hbar_negative_matches_the_shared_money_minus_glyph():
    # The net-by-workflow chart and the workflow table sit on the same dashboard
    # page. A negative net must render identically on both: format.money uses a
    # U+2212 minus BEFORE the symbol ("−€5,000"), not an ASCII "€-5,000". And a
    # value that rounds to zero must be unsigned, exactly as money() normalizes it.
    out = hbar_chart([("Alpha", -5000), ("Zero", -0.0)], "€")

    assert money(-5000.0, "EUR") in out  # "−€5,000": U+2212, sign before the symbol
    assert "−" in out  # the real minus glyph is present
    assert "€-5,000" not in out  # never the ASCII-hyphen-after-symbol form
    assert "€-0" not in out  # negative zero normalized away


def test_hbar_positive_and_empty_render():
    assert money(3000.0, "EUR") in hbar_chart([("Up", 3000)], "€")  # "€3,000"
    assert "no data yet" in hbar_chart([], "€")


def test_line_chart_keeps_a_negative_week_inside_the_viewbox():
    # A rejection-heavy week is exactly the week an executive must see. Before the
    # fix the domain was floored at zero, so -20 mapped to y=582 in a 236-tall
    # viewBox: clipped away, and the dashboard quietly hid the bad news.
    ys = _line_ys(line_chart("hours", ["W27", "W28", "W29"], [10.0, -20.0, 5.0], " h", "hours saved"))

    assert len(ys) == 3
    assert all(_BAND[0] <= y <= _BAND[1] for y in ys), ys
    assert ys[1] > ys[0] and ys[1] > ys[2]  # the negative week sits lowest (SVG y grows downward)


def test_line_chart_all_negative_stays_in_band_and_hangs_below_the_axis():
    svg = line_chart("hours", ["W27", "W28"], [-5.0, -10.0], " h", "hours saved")
    ys = _line_ys(svg)

    assert all(_BAND[0] <= y <= _BAND[1] for y in ys), ys
    # Every point is below the zero axis, which is now drawn at the top of the band.
    zero_y = float(re.search(r'class="fd-axis"[^/]*y1="([\d.]+)"', svg).group(1))
    assert all(y > zero_y for y in ys)


def test_line_chart_non_negative_output_is_byte_identical():
    # Regression guard, pinned against the pre-fix output: widening the domain must
    # not move — or reformat — anything on the non-negative path. The axis and the
    # area are the two marks the fix actually rewrote, so assert those verbatim
    # rather than only the line coordinates.
    svg = line_chart("hours", ["a", "b", "c"], [1.0, 2.0, 3.0], " h", "s")

    assert _line_ys(svg) == [168.4, 130.8, 93.2]
    assert '<line class="fd-axis" x1="44" y1="206" x2="544" y2="206"/>' in svg
    assert '<path class="fd-area" d="M127.3 168.4 L294.0 130.8 L460.7 93.2 L460.7 206 L127.3 206 Z"/>' in svg

    flat = line_chart("hours", ["a", "b"], [0.0, 0.0], " h", "s")
    assert _line_ys(flat) == [206.0, 206.0]
    assert '<line class="fd-axis" x1="44" y1="206" x2="544" y2="206"/>' in flat


def test_column_chart_is_untouched_by_the_line_chart_domain_fix():
    # column_chart shares _grid_and_axis with line_chart. It plots AI spend, which
    # is never negative, so it must come out exactly as it did before the domain
    # became two-sided — the fix has no business changing the spend card.
    svg = column_chart(["a", "b", "c"], [10.0, 20.0, 30.0], "€", "AI spend")

    # The zero line keeps its integer form; the bars' own ":.1f" coordinates are
    # untouched by the fix and stay as they were.
    assert '<line class="fd-axis" x1="44" y1="206" x2="544" y2="206"/>' in svg
