"""The standing decision must survive a quiet sample between two breaches.

Regression test for the bug that made `--decide` useless in practice: the
decision was dropped on the first non-breach wake, so it only ever survived an
unbroken run of breaches. The fleet oscillates across the threshold, so in
practice the decision was erased within an hour and the next breach asked
again -- the exact re-litigation the flag exists to prevent.
"""
import importlib.util
import os
from datetime import datetime, timedelta

import pytest

_spec = importlib.util.spec_from_file_location(
    "fbw", os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "fleet_budget_watch.py"))
fbw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fbw)

NOW = datetime.now().astimezone()


def _decision(**kw):
    d = {"text": "let it run", "at": NOW.isoformat(timespec="seconds"),
         "share": 0.61}
    d.update(kw)
    return d


def test_survives_an_ongoing_breach():
    assert fbw.carry_decision(_decision(), "BREACH", NOW) is not None


def test_survives_a_single_quiet_sample():
    """The bug: this returned None, so the next breach re-asked."""
    kept = fbw.carry_decision(_decision(), "OK", NOW)
    assert kept is not None
    assert "clear_since" in kept, "must start timing the quiet period"


def test_quiet_period_is_cumulative_not_restarted_by_each_wake():
    d = _decision()
    for _ in range(5):
        d = fbw.carry_decision(d, "OK", NOW)
    assert d is not None
    # one clear_since, set on the first quiet wake and never refreshed
    assert d["clear_since"] == fbw.carry_decision(
        _decision(clear_since=d["clear_since"]), "OK", NOW)["clear_since"]


def test_a_breach_resets_the_quiet_timer():
    d = _decision(clear_since=(NOW - timedelta(minutes=80)).isoformat())
    d = fbw.carry_decision(d, "BREACH", NOW)
    assert "clear_since" not in d, "a new breach means the episode never ended"


def test_episode_ends_after_a_sustained_quiet_period():
    old = (NOW - timedelta(minutes=fbw.EPISODE_OVER_MIN + 1)).isoformat()
    assert fbw.carry_decision(_decision(clear_since=old), "OK", NOW) is None


def test_episode_does_not_end_just_before_the_threshold():
    recent = (NOW - timedelta(minutes=fbw.EPISODE_OVER_MIN - 5)).isoformat()
    assert fbw.carry_decision(_decision(clear_since=recent), "OK", NOW) is not None


def test_corrupt_timestamp_expires_rather_than_pins_forever():
    """A decision that can never expire is worse than one that expires early."""
    assert fbw.carry_decision(_decision(clear_since="not-a-date"), "OK", NOW) is None


def test_no_decision_stays_none():
    assert fbw.carry_decision(None, "BREACH", NOW) is None
