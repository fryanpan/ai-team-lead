"""The loops check must go red for the failure it was added for.

fleet-monitor writes no state file, so nothing in the healthcheck could see it
die -- both loops went down in the 2026-08-31 reboot and only the budget half
was detectable. This check reads the guard's own state instead of asking each
loop for an artifact.

Tested against fabricated state files, never against the live guard: pointing a
monitor's test at the thing it watches fires a real alert someone has to chase.
"""
import importlib.util
import json
import os
import time

import pytest

_spec = importlib.util.spec_from_file_location(
    "hc", os.path.join(os.path.dirname(__file__), "..", "scripts",
                       "fleet_healthcheck.py"))
hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hc)


def write_state(tmp_path, loops, age_minutes=0):
    p = tmp_path / "guard-state.json"
    p.write_text(json.dumps({"loops": loops}))
    if age_minutes:
        old = time.time() - age_minutes * 60
        os.utime(p, (old, old))
    return {"type": "monitor_loops", "name": "monitor loops",
            "path": str(p), "max_age_minutes": 15,
            "why": "the fleet is unwatched"}


def test_both_loops_up_is_green(tmp_path):
    ok, detail = hc.check_monitor_loops(
        write_state(tmp_path, {"fleet-budget": "up", "fleet-monitor": "up"}))
    assert ok
    assert "2 up" in detail


def test_a_downed_loop_is_red(tmp_path):
    """The 2026-08-31 case: fleet-monitor dead, budget watcher alive."""
    ok, detail = hc.check_monitor_loops(
        write_state(tmp_path, {"fleet-budget": "up", "fleet-monitor": "down"}))
    assert not ok
    assert "fleet-monitor down" in detail
    assert "fleet-budget" not in detail   # name only what is broken


def test_revived_counts_as_up_but_is_named(tmp_path):
    """A loop the guard restarted is working, and worth saying out loud."""
    ok, detail = hc.check_monitor_loops(
        write_state(tmp_path, {"fleet-budget": "revived", "fleet-monitor": "up"}))
    assert ok
    assert "fleet-budget" in detail and "restarted" in detail


def test_revive_failure_is_red(tmp_path):
    ok, detail = hc.check_monitor_loops(
        write_state(tmp_path, {"fleet-budget": "revive-failed",
                               "fleet-monitor": "up"}))
    assert not ok
    assert "revive-failed" in detail


def test_stale_guard_state_is_red(tmp_path):
    """Loop status from an abandoned state file is not evidence of anything."""
    ok, detail = hc.check_monitor_loops(
        write_state(tmp_path, {"fleet-budget": "up", "fleet-monitor": "up"},
                    age_minutes=90))
    assert not ok
    assert "STALE" in detail


def test_missing_state_is_red(tmp_path):
    spec = write_state(tmp_path, {"fleet-budget": "up"})
    os.remove(spec["path"])
    ok, detail = hc.check_monitor_loops(spec)
    assert not ok
    assert "MISSING" in detail


def test_empty_loops_is_red(tmp_path):
    """A guard that grades no loops is not the same as all loops healthy."""
    ok, detail = hc.check_monitor_loops(write_state(tmp_path, {}))
    assert not ok
    assert "no loops" in detail


def test_unreadable_state_is_red(tmp_path):
    spec = write_state(tmp_path, {"fleet-budget": "up"})
    open(spec["path"], "w").write("{not json")
    ok, detail = hc.check_monitor_loops(spec)
    assert not ok
    assert "unreadable" in detail
