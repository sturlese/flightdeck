"""Inline-SVG chart builders for the dashboard. Stdlib only, CSS-variable themed.

The marks follow a fixed spec so every chart reads as one system: thin marks
(bars ≤ 24px, lines 2px), 4px rounded data-ends with square baselines, 2px
surface gaps between touching fills, hairline solid gridlines, selective direct
labels (the endpoint or the extreme — never a number on every point), and text
always in ink tokens rather than series colors. Hover/focus tooltips are wired
through ``data-tip`` attributes read by the page's script; every hover value is
also present in the page's tables, so the tooltip enhances and never gates.
"""

import json
import math
from html import escape

# Geometry shared by the time charts.
_W, _H = 560, 236
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 44, 16, 18, 30


def _tip(title: str, rows: list[tuple[str, str, str | None]]) -> str:
    payload = {"t": title, "r": [[k, v, c] for k, v, c in rows]}
    return escape(json.dumps(payload, ensure_ascii=False), quote=True)


def _nice_top(value: float) -> float:
    """Smallest 1/2/2.5/5 × 10^k that covers value — clean axis numbers."""
    if value <= 0:
        return 1.0
    raw = 10 ** math.floor(math.log10(value))
    for mult in (1, 2, 2.5, 5, 10):
        if raw * mult >= value:
            return raw * mult
    return raw * 10


def _fmt(value: float, decimals: int | None = None) -> str:
    if decimals is None:
        decimals = 0 if abs(value) >= 100 or value == int(value) else 1
    return f"{value:,.{decimals}f}"


def _grid_and_axis(top: float, unit: str, plot_h: float) -> str:
    parts = []
    for index in range(1, 4):  # 3 hairlines + baseline
        y = _PAD_T + plot_h * (1 - index / 3)
        tick = top * index / 3
        parts.append(f'<line class="fd-grid" x1="{_PAD_L}" y1="{y:.1f}" x2="{_W - _PAD_R}" y2="{y:.1f}"/>')
        parts.append(
            f'<text class="fd-tick" x="{_PAD_L - 6}" y="{y + 3.5:.1f}" text-anchor="end">{_fmt(tick)}{unit}</text>'
        )
    baseline_y = _PAD_T + plot_h
    parts.append(f'<line class="fd-axis" x1="{_PAD_L}" y1="{baseline_y}" x2="{_W - _PAD_R}" y2="{baseline_y}"/>')
    return "".join(parts)


def _x_labels(labels: list[str], xs: list[float], plot_h: float) -> str:
    step = 2 if len(labels) > 8 else 1
    y = _PAD_T + plot_h + 18
    return "".join(
        f'<text class="fd-tick" x="{xs[i]:.1f}" y="{y}" text-anchor="middle">{escape(labels[i])}</text>'
        for i in range(0, len(labels), step)
    )


def line_chart(chart_id: str, labels: list[str], values: list[float], unit: str, series_name: str) -> str:
    """Single-series trend: 2px line, 10% area wash, ring-guarded end dot, the
    endpoint direct-labeled, a crosshair + tooltip per x position."""
    if not values:
        return '<p class="fd-empty">no data yet</p>'
    plot_h = _H - _PAD_T - _PAD_B
    plot_w = _W - _PAD_L - _PAD_R
    top = _nice_top(max(values))
    n = len(values)
    xs = [_PAD_L + plot_w * (i + 0.5) / n for i in range(n)]
    ys = [_PAD_T + plot_h * (1 - v / top) for v in values]
    baseline_y = _PAD_T + plot_h

    line_path = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in zip(xs, ys, strict=True))
    area_path = f"{line_path} L{xs[-1]:.1f} {baseline_y} L{xs[0]:.1f} {baseline_y} Z"

    hover = []
    slot = plot_w / n
    for i, label in enumerate(labels):
        tip = _tip(label, [(series_name, f"{_fmt(values[i], 1)}{unit}", "s1")])
        hover.append(
            f'<rect class="fd-hit" x="{xs[i] - slot / 2:.1f}" y="{_PAD_T}" width="{slot:.1f}" '
            f'height="{plot_h}" data-tip="{tip}" data-cross="{chart_id}" data-x="{xs[i]:.1f}" tabindex="0"/>'
        )

    end_label = f"{_fmt(values[-1], 1)}{unit}"
    end_x = min(xs[-1] + 8, _W - _PAD_R - 4)
    return f"""<svg class="fd-chart" viewBox="0 0 {_W} {_H}" role="img" aria-label="{escape(series_name)} by week">
{_grid_and_axis(top, unit, plot_h)}
<path class="fd-area" d="{area_path}"/>
<path class="fd-line" d="{line_path}"/>
<line id="cross-{chart_id}" class="fd-cross" x1="0" y1="{_PAD_T}" x2="0" y2="{baseline_y}" opacity="0"/>
<circle class="fd-dot" cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="4.5"/>
<text class="fd-endlabel" x="{end_x:.1f}" y="{max(ys[-1] - 8, _PAD_T + 10):.1f}" text-anchor="end">{end_label}</text>
{_x_labels(labels, xs, plot_h)}
{"".join(hover)}
</svg>"""


def column_chart(labels: list[str], values: list[float], unit: str, series_name: str) -> str:
    """Weekly columns: ≤24px thick, 4px rounded caps, square baselines; only the
    extreme is direct-labeled (that's the story), the rest live in ticks + tooltips."""
    if not values:
        return '<p class="fd-empty">no data yet</p>'
    plot_h = _H - _PAD_T - _PAD_B
    plot_w = _W - _PAD_L - _PAD_R
    top = _nice_top(max(values))
    n = len(values)
    slot = plot_w / n
    width = min(24.0, slot - 4)
    xs = [_PAD_L + slot * (i + 0.5) for i in range(n)]
    baseline_y = _PAD_T + plot_h
    peak = values.index(max(values))

    bars = []
    for i, value in enumerate(values):
        h = plot_h * value / top
        x = xs[i] - width / 2
        y = baseline_y - h
        r = min(4.0, h / 2, width / 2)
        path = (
            f"M{x:.1f} {baseline_y:.1f} V{y + r:.1f} Q{x:.1f} {y:.1f} {x + r:.1f} {y:.1f} "
            f"H{x + width - r:.1f} Q{x + width:.1f} {y:.1f} {x + width:.1f} {y + r:.1f} "
            f"V{baseline_y:.1f} Z"
        )
        tip = _tip(labels[i], [(series_name, f"{unit}{_fmt(value, 2)}", "s1")])
        bars.append(f'<path class="fd-bar" d="{path}" data-tip="{tip}" tabindex="0"/>')

    peak_label = f"{unit}{_fmt(values[peak], 2)}"
    peak_y = baseline_y - plot_h * values[peak] / top - 6
    return f"""<svg class="fd-chart" viewBox="0 0 {_W} {_H}" role="img" aria-label="{escape(series_name)} by week">
{_grid_and_axis(top, "", plot_h)}
{"".join(bars)}
<text class="fd-endlabel" x="{xs[peak]:.1f}" y="{peak_y:.1f}" text-anchor="middle">{peak_label}</text>
{_x_labels(labels, xs, plot_h)}
</svg>"""


def hbar_chart(items: list[tuple[str, float]], unit: str) -> str:
    """Horizontal bars, one series (slot 1), value at the tip in ink. Handles
    negative values (a workflow can destroy value; the chart must be able to
    say so) by growing left from the zero baseline."""
    if not items:
        return '<p class="fd-empty">no data yet</p>'
    row_h, bar_h, label_w, pad_r = 34, 18, 190, 64
    width = 560
    height = len(items) * row_h + 8
    lo = min(0.0, min(v for _, v in items))
    hi = max(0.0, max(v for _, v in items))
    span = (hi - lo) or 1.0
    plot_w = width - label_w - pad_r
    x_zero = label_w + plot_w * (-lo / span)

    rows = []
    for i, (name, value) in enumerate(items):
        y = i * row_h + 6
        w = plot_w * abs(value) / span
        x = x_zero if value >= 0 else x_zero - w
        r = min(4.0, w / 2, bar_h / 2)
        if value >= 0:  # rounded data-end (right), square baseline (left)
            path = (
                f"M{x:.1f} {y} H{x + w - r:.1f} Q{x + w:.1f} {y} {x + w:.1f} {y + r:.1f} "
                f"V{y + bar_h - r:.1f} Q{x + w:.1f} {y + bar_h} {x + w - r:.1f} {y + bar_h} "
                f"H{x:.1f} Z"
            )
            value_x, anchor = x + w + 8, "start"
        else:
            path = (
                f"M{x + w:.1f} {y} H{x + r:.1f} Q{x:.1f} {y} {x:.1f} {y + r:.1f} "
                f"V{y + bar_h - r:.1f} Q{x:.1f} {y + bar_h} {x + r:.1f} {y + bar_h} "
                f"H{x + w:.1f} Z"
            )
            value_x, anchor = x - 8, "end"
        tip = _tip(name, [("net value", f"{unit}{_fmt(value)}", "s1")])
        mid = y + bar_h / 2 + 4
        rows.append(
            f'<text class="fd-cat" x="{label_w - 10}" y="{mid}" text-anchor="end">{escape(name)}</text>'
            f'<path class="fd-bar" d="{path}" data-tip="{tip}" tabindex="0"/>'
            f'<text class="fd-value" x="{value_x:.1f}" y="{mid}" text-anchor="{anchor}">{unit}{_fmt(value)}</text>'
        )
    axis = f'<line class="fd-axis" x1="{x_zero:.1f}" y1="2" x2="{x_zero:.1f}" y2="{height - 2}"/>'
    return (
        f'<svg class="fd-chart" viewBox="0 0 {width} {height}" role="img" aria-label="net value by workflow">'
        f"{axis}{''.join(rows)}</svg>"
    )


def outcome_chart(items: list[tuple[str, int, int, int]]) -> str:
    """Reviewed-output quality per workflow: 100% stacked bars in the STATUS
    palette (accepted=good, edited=warning, rejected=critical — this is state,
    not identity). 2px surface gaps separate segments; a share is labeled inside
    only when it fits, and the tooltip + table carry everything regardless."""
    row_h, bar_h, label_w, pad_r = 34, 18, 190, 16
    width = 560
    rows = []
    kinds = [("accepted", "good"), ("edited", "warn"), ("rejected", "crit")]
    y_cursor = 6
    for name, accepted, edited, rejected in items:
        total = accepted + edited + rejected
        mid = y_cursor + bar_h / 2 + 4
        category = f'<text class="fd-cat" x="{label_w - 10}" y="{mid}" text-anchor="end">{escape(name)}</text>'
        if total == 0:
            rows.append(
                category
                + f'<text class="fd-empty-row" x="{label_w}" y="{mid}">no reviews (automated or pending)</text>'
            )
            y_cursor += row_h
            continue
        plot_w = width - label_w - pad_r - 2 * 2  # two 2px surface gaps
        x = float(label_w)
        segments = [category]
        for (kind, css), count in zip(kinds, (accepted, edited, rejected), strict=True):
            share = count / total
            w = plot_w * share
            if w > 0.5:
                tip = _tip(name, [(kind, f"{share:.0%} ({count} of {total} reviewed)", css)])
                segments.append(
                    f'<rect class="fd-seg fd-{css}" x="{x:.1f}" y="{y_cursor}" width="{w:.1f}" '
                    f'height="{bar_h}" rx="2" data-tip="{tip}" tabindex="0"/>'
                )
                if w >= 40:  # label only when it fits with padding; tooltip has the rest
                    segments.append(
                        f'<text class="fd-seglabel fd-on-{css}" x="{x + w / 2:.1f}" '
                        f'y="{y_cursor + bar_h / 2 + 3.5}" text-anchor="middle">{share:.0%}</text>'
                    )
            x += w + 2
        rows.append("".join(segments))
        y_cursor += row_h
    height = y_cursor + 2
    return (
        f'<svg class="fd-chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="review outcomes by workflow">{"".join(rows)}</svg>'
    )
