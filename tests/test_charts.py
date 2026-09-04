"""Chart rendering edge cases — cross-surface consistency of money labels, and
the weekly line chart's numeric domain.

hours_saved per week can be negative (docs/metrics.md: a rejected run earns −m
minutes), and that series feeds line_chart on the dashboard, so its y-domain must
reach below zero and stay readable there. The terminal sparkline survives the same
input by flooring it (test_terminal.py), which a chart with a labeled axis cannot
do: it has to show how deep the week went.
"""

import re

from flightdeck.format import money
from flightdeck.report.charts import _H, _PAD_B, _PAD_T, column_chart, hbar_chart, line_chart

# The band a point may legally occupy: outside it the browser clips against the
# viewBox and the value disappears from the page.
_BAND = (_PAD_T, _H - _PAD_B)


def _ticks(svg: str) -> list[str]:
    return re.findall(r'class="fd-tick"[^>]*>([^<]+ h)<', svg)


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


def test_line_chart_labels_zero_and_the_depth_of_a_bad_week():
    # Keeping the negative week on the page is only half the fix: without a labeled
    # zero the axis is a heavier rule at an arbitrary height, and the reader can see
    # the dip but not read how far it goes.
    svg = line_chart("hours", ["W27", "W28", "W29"], [10.0, -20.0, 5.0], " h", "hours saved")
    ticks = _ticks(svg)

    assert "0 h" in ticks  # zero is on the scale, not implied by the frame
    assert "-20 h" in ticks  # so is the floor of the domain: the depth of the week
    values = [float(t.removesuffix(" h")) for t in ticks]
    assert values == sorted(values)  # monotonic, emitted bottom of the band upward
    assert len(set(ticks)) == len(ticks)  # every label distinguishable from its neighbour


def test_line_chart_ticks_never_render_a_signed_zero():
    # format.money and charts._money both promise a value that rounds to zero is
    # unsigned. Ticks only started going negative with the two-sided domain, so the
    # same promise has to hold here: "-0.0 h" beside "0.0 h" reads as two zeros.
    ticks = _ticks(line_chart("hours", ["W27", "W28"], [0.0, -0.1], " h", "hours saved"))

    assert not [t for t in ticks if t.startswith("-") and float(t.removesuffix(" h")) == 0], ticks
    assert len(set(ticks)) == len(ticks), ticks  # decimals come off the step, not the value


def test_line_chart_draws_no_hairline_under_the_zero_axis():
    # The axis already rules zero; a gridline at the same y would stack two opaque
    # 1px strokes and cost the chart one of its three promised hairlines.
    svg = line_chart("hours", ["W27", "W28"], [-5.0, -10.0], " h", "hours saved")

    # Compare numbers, not strings: the axis goes through _coord ("18") while
    # gridlines use ":.1f" ("18.0"), so a string test silently passes at exactly
    # the two integral positions where a regression would land.
    axis_y = float(re.search(r'class="fd-axis" x1="44" y1="([\d.]+)"', svg).group(1))
    grid_ys = [float(y) for y in re.findall(r'class="fd-grid" x1="44" y1="([\d.]+)"', svg)]
    assert axis_y not in grid_ys, (axis_y, grid_ys)
