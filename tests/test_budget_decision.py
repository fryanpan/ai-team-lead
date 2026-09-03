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


# --- a rising split is not a worsening fleet ---------------------------------
#
# 2026-09-02 23:00: the fleet total fell 459M -> 454M while unprotected share
# rose 73% -> 82%, because a PROTECTED peer (project-alpha, 25% -> 16%)
# went quiet. `worsened` read only the share, so falling burn woke a human to
# re-decide a breach that was receding. Same lesson as d7c2073 -- the level
# decides severity, not the split -- reaching the wake path it did not touch.

def test_a_quiet_protected_peer_does_not_manufacture_a_worsening():
    """The exact 2026-09-02 case: share up 9pp, fleet total down."""
    d = _decision(share=0.73, tokens=459_000_000)
    assert not fbw.worsened_since(d, 0.82, 454_000_000)


def test_more_burn_and_a_wider_split_is_a_real_worsening():
    d = _decision(share=0.73, tokens=459_000_000)
    assert fbw.worsened_since(d, 0.82, 470_000_000)


def test_a_trivial_rise_without_a_wider_split_is_not_a_worsening():
    d = _decision(share=0.73, tokens=459_000_000)
    assert not fbw.worsened_since(d, 0.74, 460_000_000)


def test_a_material_rise_alone_is_a_worsening_even_at_a_flat_split():
    """The split test saturates near 100% and would otherwise go deaf to a
    fleet doubling its burn. The step is WORSEN_PP of the ceiling."""
    d = _decision(share=0.99, tokens=304_000_000)
    step = fbw.WORSEN_PP * fbw.WINDOW_CEILING_TOKENS
    assert not fbw.worsened_since(d, 0.99, 304_000_000 + step - 1)
    assert fbw.worsened_since(d, 0.99, 304_000_000 + step)


def test_a_decision_recorded_before_the_level_was_stored_still_wakes():
    """No `tokens` key -> fall back to the share-only test, not silence."""
    d = _decision(share=0.73)
    assert "tokens" not in d
    assert fbw.worsened_since(d, 0.82, 454_000_000)


# --- the TTL must not re-ask about a receding episode -------------------------
#
# 2026-09-03 03:57: the 4h TTL woke a human for a fleet burning 304M against a
# decision recorded at 410M -- the fourth consecutive wake reporting an
# escalation while burn fell 151M.

def test_a_fresh_decision_does_not_age_out():
    d = _decision(tokens=410_000_000)
    assert not fbw.aged_into_a_question(d, 10, 500_000_000, "now")


def test_an_aged_decision_over_a_receding_fleet_restarts_its_clock():
    """The exact 2026-09-03 case."""
    d = _decision(tokens=410_000_000)
    assert not fbw.aged_into_a_question(d, 999, 304_000_000, "restamped")
    assert d["at"] == "restamped", "clock must restart"
    assert d["tokens"] == 304_000_000, "baseline must ratchet down"


def test_an_aged_decision_over_a_holding_fleet_still_asks():
    d = _decision(tokens=410_000_000)
    assert fbw.aged_into_a_question(d, 999, 410_000_000, "now")


def test_a_ratcheted_baseline_still_catches_a_rebound():
    """Deferring must not go deaf -- the restamped level is the new bar."""
    d = _decision(share=0.99, tokens=410_000_000)
    fbw.aged_into_a_question(d, 999, 304_000_000, "restamped")
    step = fbw.WORSEN_PP * fbw.WINDOW_CEILING_TOKENS
    assert fbw.worsened_since(d, 0.99, 304_000_000 + step)


def test_a_decision_with_no_stored_level_keeps_the_plain_age_test():
    d = _decision()
    assert "tokens" not in d
    assert fbw.aged_into_a_question(d, 999, 1, "now")


# --- a correct signal still needs a floor on how often it fires ---------------
#
# 2026-09-03 11:30: burn rose 341M -> 381M, a real 40M worsening, 12 minutes
# after the escalation went to Bryan. The wake was right and useless -- there
# was no second decision to make until he answered. The decision branch had no
# REWAKE_MINUTES floor at all, so a steadily-climbing fleet can wake every run.

def test_a_real_worsening_still_waits_out_the_rewake_floor():
    assert not fbw.wake_now(worsened=True, aged_out=False,
                            mins_since_last_wake=12)


def test_a_real_worsening_fires_once_the_floor_has_passed():
    assert fbw.wake_now(worsened=True, aged_out=False,
                        mins_since_last_wake=fbw.REWAKE_MINUTES)


def test_the_floor_does_not_invent_a_wake():
    assert not fbw.wake_now(worsened=False, aged_out=False,
                            mins_since_last_wake=999)


def test_a_first_wake_has_no_previous_one_to_wait_on():
    assert fbw.wake_now(worsened=True, aged_out=False,
                        mins_since_last_wake=None)


def test_ageing_out_is_floored_too():
    """4h TTL already clears 60min, but the floor must not depend on that."""
    assert not fbw.wake_now(worsened=False, aged_out=True,
                            mins_since_last_wake=5)
