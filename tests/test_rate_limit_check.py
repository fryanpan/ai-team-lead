"""The fleet must not be able to run out without something saying so.

Six 5-hour session-limit episodes between 2026-08-29 and 2026-09-01 were each
discovered by Bryan noticing work had stopped. The weekly meter was 53h stale
and the 5h budget watch was reporting a share, so both were green through all
six. Claude Code had written every rejection into the transcripts the whole
time; nothing read them.

Fabricated transcripts only -- never point a monitor's tests at live state.
"""
import importlib.util
import json
import os
import time

import pytest

_spec = importlib.util.spec_from_file_location(
    "fhc", os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "fleet_healthcheck.py"))
fhc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fhc)


def _stub_scan(monkeypatch, hits):
    """Stand in for the bun scan, so the test exercises the verdict logic."""
    class R:
        returncode = 0
        stdout = json.dumps(hits)
    monkeypatch.setattr(fhc, "bun_works", lambda: True)
    monkeypatch.setattr(fhc, "bun_path", lambda: "/bin/true")
    monkeypatch.setattr(fhc.subprocess, "run", lambda *a, **k: R())


def test_a_clean_day_is_green(monkeypatch):
    _stub_scan(monkeypatch, [])
    ok, msg = fhc.check_rate_limit_hits({"name": "session-limit hits"})
    assert ok is True
    assert "no session-limit rejections" in msg


def test_one_rejection_is_red(monkeypatch):
    _stub_scan(monkeypatch, [{"t": int(time.time() * 1000), "project": "p"}])
    ok, msg = fhc.check_rate_limit_hits({"name": "session-limit hits"})
    assert ok is False
    assert "BLOCKED" in msg


def test_a_retry_burst_counts_as_one_episode(monkeypatch):
    """Rejections retry in bursts. Counting raw hits reports 21 outages for one."""
    now = int(time.time() * 1000)
    _stub_scan(monkeypatch, [{"t": now + i * 1000, "project": "p"}
                             for i in range(20)])
    ok, msg = fhc.check_rate_limit_hits({"name": "session-limit hits"})
    assert ok is False
    assert "1 session-limit episode(s)" in msg


def test_bursts_more_than_thirty_minutes_apart_are_separate_episodes(monkeypatch):
    now = int(time.time() * 1000)
    _stub_scan(monkeypatch, [{"t": now, "project": "p"},
                             {"t": now + 45 * 60 * 1000, "project": "p"}])
    ok, msg = fhc.check_rate_limit_hits({"name": "session-limit hits"})
    assert "2 session-limit episode(s)" in msg


def test_a_blind_scanner_is_red_and_says_so(monkeypatch):
    """No read access is not the same as no rejections -- the failure this
    project keeps re-learning. It must not report green."""
    monkeypatch.setattr(fhc, "bun_works", lambda: False)
    ok, msg = fhc.check_rate_limit_hits({"name": "session-limit hits"})
    assert ok is False
    assert "cannot scan" in msg
    assert "no session-limit" not in msg


def test_the_projects_that_were_blocked_are_named(monkeypatch):
    """"Something was rate-limited" is furniture; the name is the finding."""
    _stub_scan(monkeypatch, [
        {"t": int(time.time() * 1000), "project": "-Users-x-dev-project-beta"}])
    _, msg = fhc.check_rate_limit_hits({"name": "session-limit hits"})
    assert "project-beta" in msg
