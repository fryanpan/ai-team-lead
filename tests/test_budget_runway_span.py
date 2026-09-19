"""A stale reading on the ACTIVE pool gives a runway span, not a figure.

The row is already labelled UNDER-READ because the pool kept burning after the
reading. Computing runway from that reading anyway spends the label and then
ignores it, and on 2026-09-19 that produced a 14h disagreement off one ledger
entry: the panel said the wall was 16h away, the morning digest decayed the
same entry forward and said 2h, and neither output admitted the other existed.
"""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "fbw", os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "fleet_budget_watch.py"))
fbw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fbw)

PPH = 2.51          # points/h, as measured on 2026-09-19


def row(used, age, active=True):
    return dict(pool="p", used=used, age=age, active=active)


def test_fresh_active_reading_has_no_span():
    lo, hi = fbw._runway_span(row(59.0, 0.2), PPH)
    assert lo == hi


def test_stale_active_reading_decays_to_a_lower_bound():
    """59% read 14.1h ago is 94% if the measured rate held through them."""
    lo, hi = fbw._runway_span(row(59.0, 14.1), PPH)
    assert round(hi, 1) == 16.3          # the reading taken at face value
    assert round(lo, 1) == 2.2           # the reading carried forward
    assert hi - lo > 10                  # the span IS the finding


def test_an_idle_pool_is_never_decayed():
    """An idle pool does not burn, however old its reading is."""
    lo, hi = fbw._runway_span(row(59.0, 47.6, active=False), PPH)
    assert lo == hi


def test_decay_clamps_at_a_hundred_rather_than_going_negative():
    lo, hi = fbw._runway_span(row(59.0, 400.0), PPH)
    assert lo == 0.0
    assert hi > 0


def test_span_is_rendered_only_when_the_ends_differ():
    assert fbw._fmt_runway(16.3, 16.3) == "~16h"
    assert fbw._fmt_runway(16.0, 16.4) == "~16h"     # under an hour apart
    assert fbw._fmt_runway(2.2, 16.3) == "~2-16h"
    assert fbw._fmt_runway(None, None) == "?"
