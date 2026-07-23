"""Chart rendering edge cases — cross-surface consistency of money labels."""

from flightdeck.format import money
from flightdeck.report.charts import hbar_chart


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
