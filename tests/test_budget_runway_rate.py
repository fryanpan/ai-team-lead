"""Runway rate must be points GAINED over the stint, not `used / hours`.

`used / hours` assumes the pool was empty when the stint began. For a pool
re-entered mid-week that overstates the rate several-fold and projects a wall
that never arrives.
"""
import importlib.util
import os
from datetime import datetime, timedelta, timezone

_spec = importlib.util.spec_from_file_location(
    "fbw", os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "fleet_budget_watch.py"))
fbw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fbw)

PT = timezone(timedelta(hours=-7))
WEEK = "2026-09-14T06:59:00-07:00"          # this week's reset
SINCE = datetime(2026, 9, 9, 19, 47, tzinfo=PT)
NOW = datetime(2026, 9, 10, 13, 40, tzinfo=PT)


def r(at, used, resets=WEEK):
    return {"read_at": at.isoformat(), "all_models": used, "resets": resets}


def entry(*hist):
    e = dict(hist[-1])
    e["history"] = list(hist)
    return {"pool": e}


def rate(ledger, since=SINCE, now=NOW):
    return fbw.observed_points_per_hour(ledger, "pool", since.isoformat(), now)


def test_reentered_pool_uses_the_level_it_was_left_at():
    left = r(datetime(2026, 9, 8, 15, 1, tzinfo=PT), 59)
    read = r(datetime(2026, 9, 10, 13, 35, tzinfo=PT), 90)
    pph, _ = rate(entry(left, read))
    assert abs(pph - 31 / 17.8) < 0.05          # ~1.74, never 90/17.8 = 5.05


def test_two_readings_inside_the_stint_need_no_assumption():
    left = r(datetime(2026, 9, 8, 15, 1, tzinfo=PT), 59)
    a = r(datetime(2026, 9, 10, 8, 37, tzinfo=PT), 81)
    b = r(datetime(2026, 9, 10, 13, 35, tzinfo=PT), 90)
    pph, base_h = rate(entry(left, a, b))
    assert abs(pph - 9 / base_h) < 1e-9 and 4.9 < base_h < 5.0


def test_midweek_reentry_with_no_prior_reading_is_unknowable():
    # A pre-history ledger: only the latest reading survives.
    only = r(datetime(2026, 9, 10, 13, 35, tzinfo=PT), 90)
    assert rate({"pool": only}) == (None, None)


def test_fresh_week_starts_from_zero():
    since = datetime(2026, 9, 10, 23, 33, tzinfo=PT)
    fresh = "2026-09-17T18:00:00-07:00"          # week began 5.5h before since
    read = r(since + timedelta(hours=3), 6, fresh)
    pph, _ = rate(entry(read), since=since, now=since + timedelta(hours=4))
    assert abs(pph - 2.0) < 1e-9


def test_a_reset_seen_in_history_means_zero_base():
    since = datetime(2026, 9, 11, 10, 0, tzinfo=PT)
    old = r(datetime(2026, 9, 5, 18, 38, tzinfo=PT), 80,
            "2026-09-10T17:59:00-07:00")
    read = r(since + timedelta(hours=4), 10, "2026-09-17T18:00:00-07:00")
    pph, _ = rate(entry(old, read), since=since, now=since + timedelta(hours=5))
    assert abs(pph - 2.5) < 1e-9


def test_rate_ends_at_the_reading_not_the_clock():
    left = r(datetime(2026, 9, 8, 15, 1, tzinfo=PT), 59)
    read = r(datetime(2026, 9, 10, 13, 35, tzinfo=PT), 90)
    later = rate(entry(left, read), now=NOW + timedelta(hours=10))
    assert later == rate(entry(left, read))
