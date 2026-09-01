"""The error window has to actually bound what it counts.

Two bugs, both of which kept a check red on errors that had stopped:

1. A log stamping a TIME with no date matched no date regex, so every matching
   line was treated as "undated, file written recently, could be recent" -- a
   warning from any hour of any day counted forever. The dated path had this
   fixed years earlier; the undated half was never looked at.
2. No way to tolerate a known-benign line. 70 of the 74 lines pinning the
   github broker red were an expected start race, so the one real warning
   arrived buried in noise nobody read.
"""
import importlib.util
import os
import time
from datetime import datetime, timedelta

import pytest

_spec = importlib.util.spec_from_file_location(
    "hc", os.path.join(os.path.dirname(__file__), "..", "scripts",
                       "fleet_healthcheck.py"))
hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hc)


def make_log(tmp_path, lines, name="d.log"):
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n")
    return p


def hhmmss(dt):
    return dt.strftime("%H:%M:%S")


# ── time-only timestamps ─────────────────────────────────────────────────────

def test_time_only_line_inside_the_window_counts(tmp_path):
    now = datetime.now()
    p = make_log(tmp_path, [f"[broker {hhmmss(now - timedelta(minutes=5))}] error: boom",
                            f"[broker {hhmmss(now)}] ok"])
    ok, detail = hc.check_log_errors(
        {"name": "t", "path": str(p), "pattern": "error", "max": 0,
         "window_minutes": 60})
    assert not ok
    assert "1 error lines" in detail


def test_time_only_line_outside_the_window_does_not_count(tmp_path):
    """The bug: this line used to count forever because it had no date."""
    now = datetime.now()
    p = make_log(tmp_path, [f"[broker {hhmmss(now - timedelta(hours=6))}] error: boom",
                            f"[broker {hhmmss(now)}] ok"])
    ok, detail = hc.check_log_errors(
        {"name": "t", "path": str(p), "pattern": "error", "max": 0,
         "window_minutes": 60})
    assert ok, detail


def test_time_only_across_midnight_walks_the_date_back(tmp_path):
    """Times that increase as you walk backwards mean the log crossed midnight."""
    anchor = datetime.now().replace(hour=0, minute=30, second=0, microsecond=0)
    p = make_log(tmp_path, ["[broker 23:45:00] error: yesterday",
                            "[broker 00:29:00] fine"])
    old = anchor.timestamp()
    os.utime(p, (old, old))
    ok, _ = hc.check_log_errors(
        {"name": "t", "path": str(p), "pattern": "error", "max": 0,
         "window_minutes": 30})
    assert ok, "a line 45m before the anchor must fall outside a 30m window"


def test_dated_lines_still_win_over_time_only(tmp_path):
    """A log with real dates must not be re-interpreted by the fallback."""
    old = datetime.now() - timedelta(days=3)
    p = make_log(tmp_path, [f"{old:%Y-%m-%d %H:%M:%S} error: ancient",
                            f"{datetime.now():%Y-%m-%d %H:%M:%S} ok"])
    ok, _ = hc.check_log_errors(
        {"name": "t", "path": str(p), "pattern": "error", "max": 0,
         "window_minutes": 60})
    assert ok


# ── ignore ───────────────────────────────────────────────────────────────────

def test_ignored_lines_do_not_count(tmp_path):
    now = datetime.now()
    p = make_log(tmp_path, [f"[b {hhmmss(now)}] error: Failed to start server. "
                            f"Is port 7902 in use?"] * 5)
    ok, detail = hc.check_log_errors(
        {"name": "t", "path": str(p), "pattern": "error", "max": 0,
         "window_minutes": 60,
         "ignore": r"Failed to start server\. Is port \d+ in use\?"})
    assert ok
    assert "5 ignored" in detail, "a silenced flood must still be visible"


def test_ignore_does_not_swallow_a_real_error(tmp_path):
    """The whole risk of an ignore rule: it must stay narrow."""
    now = datetime.now()
    p = make_log(tmp_path, [f"[b {hhmmss(now)}] error: Failed to start server. "
                            f"Is port 7902 in use?",
                            f"[b {hhmmss(now)}] error: token rejected"])
    ok, detail = hc.check_log_errors(
        {"name": "t", "path": str(p), "pattern": "error", "max": 0,
         "window_minutes": 60,
         "ignore": r"Failed to start server\. Is port \d+ in use\?"})
    assert not ok
    assert "token rejected" in detail


def test_no_ignore_key_behaves_as_before(tmp_path):
    now = datetime.now()
    p = make_log(tmp_path, [f"[b {hhmmss(now)}] error: boom"])
    ok, detail = hc.check_log_errors(
        {"name": "t", "path": str(p), "pattern": "error", "max": 0,
         "window_minutes": 60})
    assert not ok
    assert "ignored" not in detail
