"""The quota-trend staleness check must not be blind to a format slip.

2026-09-07: two live /usage readings were written to the trend log as bullets
(`- **2026-09-07 18:42 PT ...`) rather than in the older backticked form. The
parser matched only the backticked form, so the check reported "no reading has
been recorded" while both readings sat in the file. That is the worst shape a
monitor can fail in -- it named a dead loop, and the loop had run.

Fabricated log text only -- never point a monitor's tests at live state.
"""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "fh", os.path.join(os.path.dirname(__file__), "..", "scripts",
                       "fleet_healthcheck.py"))
fh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fh)

BACKTICK = "`2026-09-01 20:45 PT - 24% all-models - OK`"
BULLET = "- **2026-09-07 18:42 PT - all-models 21% - no Tier 2.** Detail here."


def test_reads_the_backticked_form():
    assert fh._latest_trend_entry(BACKTICK).strftime("%Y-%m-%d %H:%M") == "2026-09-01 20:45"


def test_reads_the_bullet_bold_form():
    """This is the assertion that failed before the parser was widened."""
    assert fh._latest_trend_entry(BULLET).strftime("%Y-%m-%d %H:%M") == "2026-09-07 18:42"


def test_takes_the_newest_across_both_forms():
    """A file carrying both must not report the older one just because it matched first."""
    assert fh._latest_trend_entry(BACKTICK + "\n" + BULLET).strftime("%H:%M") == "18:42"
    assert fh._latest_trend_entry(BULLET + "\n" + BACKTICK).strftime("%H:%M") == "18:42"


def test_approximate_minute_marker_still_parses():
    assert fh._latest_trend_entry("`2026-09-01 ~9:05 PT - reading`") is not None


def test_no_dated_entry_returns_none():
    assert fh._latest_trend_entry("no readings here at all") is None


def test_a_date_mid_line_is_not_an_entry():
    """Only a line that STARTS an entry counts, or prose would fake freshness."""
    assert fh._latest_trend_entry("we compared it against 2026-09-07 18:42 PT numbers") is None
