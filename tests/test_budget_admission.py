"""The watcher has to keep the fleet out of the 5h limit, not narrate arrival.

2026-09-03: the fleet was rate-limited twice in one day while this script
worked perfectly -- it measured, it woke a human, and the human read it after
the rejections. The trailing 5h window cannot be the trigger: it reaches the
ceiling only after five hours of burn that already happened.

Fabricated numbers only -- never point a monitor's tests at live state.
"""
import importlib.util
import os

import pytest

_spec = importlib.util.spec_from_file_location(
    "fbw", os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "fleet_budget_watch.py"))
fbw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fbw)

CEILING = 420_000_000
FLOOR = 442_000_000


def _rows(*pairs):
    return [(k, {"share": sh}) for k, sh in pairs]


# --- projection ---------------------------------------------------------------

def test_the_last_hour_run_forward_is_the_projection():
    assert fbw.projected_window(100_000_000, 1.0, 5.0) == 500_000_000


def test_a_half_hour_sample_still_projects_five_hours():
    assert fbw.projected_window(50_000_000, 0.5, 5.0) == 500_000_000


def test_a_zero_length_sample_projects_nothing_rather_than_dividing_by_zero():
    assert fbw.projected_window(10, 0, 5.0) == 0


# --- level --------------------------------------------------------------------

def test_a_quiet_fleet_is_clear():
    assert fbw.admission_level(100_000_000, 150_000_000, CEILING, FLOOR) == "clear"


def test_an_accelerating_fleet_is_caught_before_it_arrives():
    """The whole point: window well under the ceiling, projection over it."""
    assert fbw.admission_level(200_000_000, 600_000_000, CEILING, FLOOR) == "hold-all"


def test_the_soft_band_asks_the_top_burner_only():
    # 0.70 * 420M = 294M projected, still under the ceiling
    assert fbw.admission_level(150_000_000, 300_000_000, CEILING, FLOOR) == "hold-top"


def test_a_window_already_in_the_rejection_band_holds_everyone():
    """However it got there -- a projection that has since calmed does not
    excuse a window sitting where we have actually been rejected."""
    assert fbw.admission_level(450_000_000, 1_000, CEILING, FLOOR) == "hold-all"


def test_the_two_triggers_are_independent():
    """Level alone, with a projection that says the burn already stopped."""
    assert fbw.admission_level(FLOOR, 0, CEILING, FLOOR) == "hold-all"
    assert fbw.admission_level(0, CEILING, CEILING, FLOOR) == "hold-all"


# --- targets ------------------------------------------------------------------

def test_clear_asks_nobody():
    assert fbw.hold_targets(_rows(("a", 0.9)), "clear", {}) == []


def test_hold_top_asks_the_single_biggest():
    rows = _rows(("big", 0.65), ("mid", 0.30), ("small", 0.05))
    assert fbw.hold_targets(rows, "hold-top", {}) == ["big"]


def test_hold_all_asks_everyone_material():
    rows = _rows(("big", 0.65), ("mid", 0.20), ("noise", 0.02))
    assert fbw.hold_targets(rows, "hold-all", {}) == ["big", "mid"]


def test_protection_decides_who_is_trimmed_first_in_the_soft_band():
    """There is a choice here about whose fan-out to cut, and protected work
    should not be the one cut."""
    rows = _rows(("guarded", 0.55), ("big", 0.40))
    assert fbw.hold_targets(rows, "hold-top", {"guarded": 0.4}) == ["big"]


def test_protection_stops_applying_in_the_rejection_band():
    """The limit is per-account and machine-wide. A protected project that
    keeps fanning out there is spending the window that blocks itself, so
    exempting it protects the label and loses the work."""
    rows = _rows(("big", 0.60), ("guarded", 0.30))
    assert fbw.hold_targets(rows, "hold-all", {"guarded": 0.4}) == ["big", "guarded"]


def test_a_top_burner_below_the_bar_is_not_worth_asking_alone():
    """A fleet with no single dominant project has nobody useful to ask."""
    rows = _rows(("a", 0.2), ("b", 0.2), ("c", 0.2))
    assert fbw.hold_targets(rows, "hold-top", {}) == []


# --- send / release -----------------------------------------------------------

def test_a_fresh_target_is_asked():
    ask, release = fbw.holds_to_send(["a"], {}, "2026-09-03T16:00:00-07:00")
    assert ask == ["a"] and release == []


def test_a_peer_asked_two_minutes_ago_is_not_asked_again():
    prev = {"a": {"at": "2026-09-03T15:58:00-07:00"}}
    ask, _ = fbw.holds_to_send(["a"], prev, "2026-09-03T16:00:00-07:00")
    assert ask == []


def test_a_hold_is_repeated_once_it_goes_stale():
    prev = {"a": {"at": "2026-09-03T15:00:00-07:00"}}
    ask, _ = fbw.holds_to_send(["a"], prev, "2026-09-03T16:00:00-07:00")
    assert ask == ["a"]


def test_a_peer_no_longer_a_target_is_released():
    """A hold with no release is a permanent throttle nobody remembers setting."""
    prev = {"a": {"at": "2026-09-03T15:00:00-07:00"}}
    ask, release = fbw.holds_to_send([], prev, "2026-09-03T16:00:00-07:00")
    assert ask == [] and release == ["a"]


def test_an_unparseable_hold_timestamp_re_asks_rather_than_going_silent():
    prev = {"a": {"at": "not a date"}}
    ask, _ = fbw.holds_to_send(["a"], prev, "2026-09-03T16:00:00-07:00")
    assert ask == ["a"]


# --- peer lookup --------------------------------------------------------------

def test_a_worktree_maps_to_its_project(monkeypatch):
    """A peer in project-alpha/worktrees/... is still that project's
    burn, and the burn rows are keyed on the project."""
    payload = [{"cwd": "/Volumes/Data/Users/b/dev/project-alpha/worktrees/x/y",
                "stable_id": "abc"},
               {"cwd": "/Volumes/Data/Users/b/dev/peer-alpha", "stable_id": "def"}]
    class R:
        stdout = __import__("json").dumps(payload)
    monkeypatch.setattr(fbw.subprocess, "run", lambda *a, **k: R())
    ids = fbw.peer_stable_ids()
    assert ids["project-alpha"] == "abc"
    assert ids["peer-alpha"] == "def"


def test_an_unreachable_hive_is_an_empty_map_not_an_exception(monkeypatch):
    def boom(*a, **k):
        raise OSError("no broker")
    monkeypatch.setattr(fbw.subprocess, "run", boom)
    assert fbw.peer_stable_ids() == {}


# --- recent share vs window share ---------------------------------------------

def _rows2(*triples):
    return [(k, {"share": sh, "recent_share": rs}) for k, sh, rs in triples]


def test_a_project_that_just_started_burning_is_still_a_target():
    """The 2026-09-03 case: 3% of the 5h window, 11% of the last hour, because
    it had only just started. The window share stays small for hours -- which
    is the whole period in which asking it would have helped."""
    rows = _rows2(("big", 0.65, 0.55), ("newcomer", 0.03, 0.11))
    assert "newcomer" in fbw.hold_targets(rows, "hold-all", {})


def test_a_project_that_has_stopped_is_still_a_target_while_its_window_is_big():
    """Symmetric: its tokens are still in the window and still count against
    the ceiling, so it does not get released by going quiet for ten minutes."""
    rows = _rows2(("faded", 0.40, 0.01),)
    assert fbw.hold_targets(rows, "hold-all", {}) == ["faded"]


def test_the_biggest_recent_burner_is_the_one_asked_in_the_soft_band():
    rows = _rows2(("historic", 0.60, 0.05), ("current", 0.10, 0.70))
    assert fbw.hold_targets(rows, "hold-top", {}) == ["current"]


# --- a peer that has nothing left to give -------------------------------------

def test_a_peer_that_acked_is_not_asked_again():
    """ClientOrg on 2026-09-03: 66% of recent burn, all of it Bryan's own live
    review. No fan-out left to cut, so a re-ask is an alert fired at somebody
    who cannot act on it -- the exact failure this file exists to remove."""
    prev = {"a": {"at": "2026-09-03T15:00:00-07:00",
                  "acked_at": "2026-09-03T16:25:00-07:00"}}
    ask, _ = fbw.holds_to_send(["a"], prev, "2026-09-03T17:00:00-07:00")
    assert ask == []


def test_an_ack_expires():
    """'Nothing to cut' is a statement about right now, not forever."""
    prev = {"a": {"at": "2026-09-03T15:00:00-07:00",
                  "acked_at": "2026-09-03T10:00:00-07:00"}}
    ask, _ = fbw.holds_to_send(["a"], prev, "2026-09-03T17:00:00-07:00")
    assert ask == ["a"]


def test_an_acked_peer_is_still_released_when_it_stops_being_a_target():
    prev = {"a": {"at": "2026-09-03T15:00:00-07:00",
                  "acked_at": "2026-09-03T16:55:00-07:00"}}
    _, release = fbw.holds_to_send([], prev, "2026-09-03T17:00:00-07:00")
    assert release == ["a"]


def test_a_missing_hold_record_is_not_an_ack():
    assert not fbw.acked_recently(None, "2026-09-03T17:00:00-07:00")
    assert not fbw.acked_recently({}, "2026-09-03T17:00:00-07:00")
