"""The quota meter must be checked for READINGS, not for a scheduled job.

Regression test for 2026-09-01: the token-watch cron died on a respawn and
nothing noticed for two days. The healthcheck ran 34 green checks the whole
time, none of which looked at whether a quota reading had ever been taken.

Every case here runs against a fabricated log, never the live one -- testing a
monitor against real state fires an alert someone has to chase.
"""
import importlib.util
import os
from datetime import datetime, timedelta

import pytest

_spec = importlib.util.spec_from_file_location(
    "fhc", os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "fleet_healthcheck.py"))
fhc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fhc)


def _log(tmp_path, *entries):
    body = "## Trend log\n\n"
    for ts, rest in entries:
        body += "`%s %s`\n\nsome prose about the reading\n\n" % (ts, rest)
    p = tmp_path / "token-control.md"
    p.write_text(body, encoding="utf-8")
    return {"name": "quota trend log", "path": str(p)}


def _stamp(hours_ago):
    return (datetime.now() - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%d %H:%M PT")


def test_a_recent_reading_is_green(tmp_path):
    spec = _log(tmp_path, (_stamp(2), "- 41% (Fable 12%) - 30% elapsed - OK"))
    ok, msg = fhc.check_trend_log(spec)
    assert ok, msg


def test_a_two_day_gap_is_red(tmp_path):
    """The actual 2026-09-01 shape: last reading Aug 30, nothing since."""
    spec = _log(tmp_path, (_stamp(48), "- ACCOUNT CHANGED - no verdict possible"))
    ok, msg = fhc.check_trend_log(spec)
    assert not ok
    assert "STALE" in msg
    assert "unarmed" in msg, "must name the likely cause, not just the symptom"


def test_the_newest_entry_wins_regardless_of_file_order(tmp_path):
    """The log is newest-first, but nothing should depend on that."""
    spec = _log(tmp_path,
                (_stamp(50), "- old"),
                (_stamp(1), "- new"),
                (_stamp(70), "- older"))
    ok, msg = fhc.check_trend_log(spec)
    assert ok, msg


def test_an_approximate_time_still_parses(tmp_path):
    """Entries are sometimes written `2026-08-30 ~11:20 PT`."""
    ts = (datetime.now() - timedelta(hours=1))
    spec = _log(tmp_path, (ts.strftime("%Y-%m-%d ~%H:%M PT"), "- 40% - OK"))
    ok, msg = fhc.check_trend_log(spec)
    assert ok, msg


def test_mtime_does_not_rescue_a_stale_log(tmp_path):
    """The doc is edited for unrelated reasons; only the entry date counts."""
    spec = _log(tmp_path, (_stamp(48), "- old reading"))
    os.utime(spec["path"], None)          # freshly touched, still stale
    ok, _ = fhc.check_trend_log(spec)
    assert not ok


def test_a_log_with_no_dated_entry_is_red(tmp_path):
    p = tmp_path / "token-control.md"
    p.write_text("## Trend log\n\nOne line per check: ...\n", encoding="utf-8")
    ok, msg = fhc.check_trend_log({"name": "quota trend log", "path": str(p)})
    assert not ok
    assert "never been read" in msg


def test_a_missing_file_is_red_not_an_exception(tmp_path):
    ok, msg = fhc.check_trend_log(
        {"name": "quota trend log", "path": str(tmp_path / "nope.md")})
    assert not ok
    assert "MISSING" in msg


def test_the_window_is_configurable(tmp_path):
    spec = _log(tmp_path, (_stamp(10), "- 40% - OK"))
    assert not fhc.check_trend_log(dict(spec, max_age_hours=8))[0]
    assert fhc.check_trend_log(dict(spec, max_age_hours=24))[0]


def test_a_malformed_date_is_skipped_not_fatal(tmp_path):
    spec = _log(tmp_path,
                ("2026-13-45 99:99 PT", "- garbage"),
                (_stamp(1), "- 40% - OK"))
    ok, msg = fhc.check_trend_log(spec)
    assert ok, msg
