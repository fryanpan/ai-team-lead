"""check_token_resolvable must run its command fallback through bun.

Under launchd an Apple-signed interpreter (and its subprocesses) is denied the
secondary volume that ~/.config symlinks onto, so a direct subprocess of the
credential CLI failed and the check cried "NO SOURCE ANSWERED" hourly while the
consumer resolved fine. A genuinely missing token must still go red.
"""

import importlib.util
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "fleet_healthcheck_token", os.path.join(ROOT, "scripts", "fleet_healthcheck.py"))
hc = importlib.util.module_from_spec(_spec)
sys.modules["fleet_healthcheck_token"] = hc
_spec.loader.exec_module(hc)

SPEC = {"name": "github token", "env": "TEST_TOKEN_UNSET_VAR",
        "commands": [["/opt/homebrew/bin/gh", "auth", "token"]],
        "commands_label": "gh auth token"}


def fake_run(bun_ok, direct_ok):
    """bun invocations answer bun_ok; any direct invocation answers direct_ok."""
    def run(cmd, **kw):
        via_bun = cmd[0].endswith("bun")
        ok = bun_ok if via_bun else direct_ok
        return subprocess.CompletedProcess(cmd, 0 if ok else 1,
                                           stdout="x" * 40 if (ok and not via_bun) else "",
                                           stderr="")
    return run


def setup(monkeypatch, bun_ok, direct_ok):
    monkeypatch.delenv("TEST_TOKEN_UNSET_VAR", raising=False)
    monkeypatch.setattr(hc, "bun_works", lambda: True)
    monkeypatch.setattr(hc, "bun_path", lambda: "/fake/bun")
    monkeypatch.setattr(hc.subprocess, "run", fake_run(bun_ok, direct_ok))


def test_green_when_only_bun_can_run_the_command(monkeypatch):
    # The launchd case: direct subprocess is denied, bun succeeds.
    setup(monkeypatch, bun_ok=True, direct_ok=False)
    ok, msg = hc.check_token_resolvable(SPEC)
    assert ok and "gh auth token" in msg


def test_red_when_no_source_answers(monkeypatch):
    setup(monkeypatch, bun_ok=False, direct_ok=False)
    ok, msg = hc.check_token_resolvable(SPEC)
    assert not ok and "NO SOURCE ANSWERED" in msg


def test_falls_back_to_direct_when_bun_unavailable(monkeypatch):
    setup(monkeypatch, bun_ok=False, direct_ok=True)
    monkeypatch.setattr(hc, "bun_works", lambda: False)
    ok, _ = hc.check_token_resolvable(SPEC)
    assert ok
