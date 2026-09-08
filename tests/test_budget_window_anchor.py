"""The 5h window has a PHASE, and it does not come from the clock.

The watcher once returned BREACH, and held fleet fan-out, on a window trailing
from run time -- while the account's own session meter, covering the real
window, read about half used at roughly two thirds elapsed. A couple of hours of
pre-reset burn were counted against a window that had already forgiven them.

The phase cannot be recovered from the transcripts. A window opens at the first
turn after the previous one closes, so it is only visible in an idle gap, and a
fleet busy enough for this watch to matter does not have one: reconstructing the
boundaries over five lookbacks returned two phases hours apart, each
self-consistent. So the anchor is the recorded /usage session reset, and where
there is none the reading is an upper bound and must not raise a breach.
"""
import datetime
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "fbw", os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "fleet_budget_watch.py"))
fbw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fbw)

UTC = datetime.timezone.utc
NOW = datetime.datetime(2031, 4, 2, 21, 0, tzinfo=UTC)
RESET = NOW + datetime.timedelta(hours=1)   # the panel prints the UPCOMING reset


def test_an_upcoming_reset_gives_the_exact_window():
    """The panel prints the NEXT reset, so the window is [reset-5h, reset]."""
    start, end, basis = fbw.session_window(NOW, RESET)
    assert basis == fbw.WINDOW_ANCHORED
    assert end == RESET
    assert start == RESET - datetime.timedelta(hours=5)
    # The live failure: a trailing window would have started 2h too early.
    assert start > NOW - datetime.timedelta(hours=5)


def test_no_anchor_falls_back_to_trailing_and_says_so():
    start, end, basis = fbw.session_window(NOW, None)
    assert basis == fbw.WINDOW_TRAILING
    assert (end, start) == (NOW, NOW - datetime.timedelta(hours=5))


def test_a_reset_just_behind_us_tiles_one_step_forward():
    """Between two /usage reads the recorded reset goes stale by up to 5h."""
    start, end, basis = fbw.session_window(NOW, NOW - datetime.timedelta(hours=1))
    assert basis == fbw.WINDOW_TILED
    assert end == NOW + datetime.timedelta(hours=4)
    assert start == NOW - datetime.timedelta(hours=1)


def test_an_anchor_more_than_one_tile_stale_is_abandoned():
    """Tiling assumes the fleet never went idle. Two tiles is too much to assume."""
    _, _, basis = fbw.session_window(NOW, NOW - datetime.timedelta(hours=7))
    assert basis == fbw.WINDOW_TRAILING


def test_anchor_is_read_per_account():
    ledger = {"a@x.com": {"session_resets": RESET.isoformat()},
              "b@x.com": {"session_resets": "2026-01-01T00:00:00+00:00"}}
    assert fbw.anchor_for(ledger, "a@x.com") == RESET
    # A pool we are not billing to must never lend its phase.
    assert fbw.anchor_for(ledger, "c@x.com") is None
    assert fbw.anchor_for(ledger, None) is None


def test_an_unparseable_anchor_is_no_anchor():
    assert fbw.anchor_for({"a": {"session_resets": "3:29pm"}}, "a") is None
    assert fbw.anchor_for({"a": {}}, "a") is None


# ---------------------------------------------------------------- verdicts

ROWS = [("noisy", {"tokens": 412_000_000, "share": 0.92})]


def _verdict(tokens, basis):
    return fbw.verdict_for(tokens, 0.92, 0.08, 0.40, "noisy", ROWS, basis)


def test_a_trailing_window_cannot_raise_a_breach():
    """Over the ceiling, but on an upper bound. The live false alarm."""
    verdict, detail = _verdict(412_000_000, fbw.WINDOW_TRAILING)
    assert verdict == "WATCH"
    assert "UPPER BOUND" in detail


def test_the_same_number_on_an_anchored_window_still_breaches():
    """The downgrade is about the basis, not about softening the threshold."""
    verdict, _ = _verdict(412_000_000, fbw.WINDOW_ANCHORED)
    assert verdict == "BREACH"


def test_a_tiled_window_is_a_measurement_and_still_breaches():
    verdict, detail = _verdict(412_000_000, fbw.WINDOW_TILED)
    assert verdict == "BREACH"
    assert "carried one step" in detail


# ------------------------------------------------------------- admission

def test_an_upper_bound_over_the_floor_does_not_hold_the_whole_fleet():
    """What actually fired: hold-all off a phase-shifted window."""
    assert fbw.admission_level(450_000_000, 0,
                               basis=fbw.WINDOW_TRAILING) == "clear"
    assert fbw.admission_level(450_000_000, 0,
                               basis=fbw.WINDOW_ANCHORED) == "hold-all"


def test_the_projection_still_holds_the_fleet_without_an_anchor():
    """The last hour run forward does not depend on where the window's phase is,
    so losing the anchor must not disarm prevention -- only the rear-view arm."""
    assert fbw.admission_level(0, 500_000_000,
                               basis=fbw.WINDOW_TRAILING) == "hold-all"


# ------------------------------------------- checking the proxy against the meter

def _entry(pct, read_at):
    return {"session_pct": pct, "session_read_at": read_at.isoformat()}


W_START = datetime.datetime(2031, 4, 2, 17, 0, tzinfo=UTC)
W_END = W_START + datetime.timedelta(hours=5)
READ = W_START + datetime.timedelta(hours=3, minutes=15)   # 65% elapsed


def test_the_meter_contradicting_the_proxy_is_written_into_the_verdict():
    """The live shape: proxy at its ceiling, meter at 53% with 65% elapsed."""
    got = fbw.meter_crosscheck(_entry(53, READ), W_START, W_END, READ, 0.99)
    pct, elapsed, projected, note = got
    assert pct == 53
    assert round(elapsed, 2) == 0.65
    assert 80 < projected < 83
    assert note == ""   # below the ceiling, nothing to contradict yet
    _, _, _, note = fbw.meter_crosscheck(_entry(53, READ), W_START, W_END, READ, 1.02)
    assert "disagrees" in note and "53%" in note


def test_no_note_when_the_meter_agrees_that_it_is_bad():
    _, _, projected, note = fbw.meter_crosscheck(
        _entry(88, READ), W_START, W_END, READ, 1.02)
    assert projected > 85
    assert note == ""


def test_a_reading_from_a_previous_window_is_not_used():
    """That pool has been refilled since; using it would describe the wrong 5h."""
    stale = READ - datetime.timedelta(hours=9)
    assert fbw.meter_crosscheck(_entry(53, stale), W_START, W_END, READ, 1.02) is None


def test_a_missing_or_unparseable_reading_is_simply_absent():
    assert fbw.meter_crosscheck({}, W_START, W_END, READ, 1.02) is None
    assert fbw.meter_crosscheck({"session_pct": "half", "session_read_at":
                                 READ.isoformat()}, W_START, W_END, READ, 1.02) is None
