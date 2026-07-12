"""The terminal report renderer — the sparkline's numeric edge cases.

hours_saved per week can be negative (a rejection-heavy week earns negative
minutes), and that series is fed straight into the sparkline, so the glyph math
must survive sub-zero input.
"""

from flightdeck.report.terminal import _SPARK, _spark


def test_spark_baseline_unchanged_for_non_negative_values():
    # Regression guard: the fix must not alter the normal (non-negative) rendering.
    assert _spark([1.0, 2.0, 3.0]) == "▃▆█"
    assert _spark([0.0, 0.0, 0.0]) == "▁▁▁"


def test_spark_handles_negative_weekly_hours_without_crashing():
    # A rejection-heavy week yields negative hours_saved (metrics.minutes_saved →
    # -human), which reaches _spark via terminal.render; it must not IndexError.
    out = _spark([10.0, -20.0])  # raised IndexError before the fix
    assert len(out) == 2
    assert out[0] == "█"  # the positive week is the tallest…
    assert out[1] == "▁"  # …and the negative week floors to the lowest bar, not a wrong glyph


def test_spark_all_negative_does_not_crash():
    out = _spark([-5.0, -1.0, -10.0])
    assert len(out) == 3
    assert set(out) <= set(_SPARK)  # every glyph is a valid bar, nothing out of range
