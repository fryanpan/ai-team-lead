"""A cold boot must look revivable, not like a state the guard refuses to act on.

Regression test for the reason both monitor loops stayed down for ~70 minutes
after the 2026-08-31 freeze: with no tmux server running, `loop_status` reported
"no-server", which `main` does not treat as revivable, so the guard notified and
did nothing. `tmux new-session` starts a server itself, and a launchd-started
server reads the secondary volume fine -- measured with a private socket under
`launchctl submit` -- so no-server is simply "down".
"""
import importlib.util
import os

import pytest

_spec = importlib.util.spec_from_file_location(
    "guard", os.path.join(os.path.dirname(__file__), "..", "scripts",
                          "fleet_guard.py"))
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


@pytest.fixture
def tmux_present(monkeypatch):
    monkeypatch.setattr(guard.os.path, "exists", lambda p: True)


def test_no_server_reads_as_down(monkeypatch, tmux_present):
    """`tmux ls` failing means no server -- every loop is down and revivable."""
    monkeypatch.setattr(guard, "sh",
                        lambda cmd, timeout=10: ("no server running", 1))
    assert set(guard.loop_status().values()) == {"down"}


def test_missing_binary_is_not_down(monkeypatch):
    """No tmux at all is a different problem; reviving cannot fix it."""
    monkeypatch.setattr(guard.os.path, "exists", lambda p: False)
    assert set(guard.loop_status().values()) == {"no-tmux"}


def test_running_sessions_are_up(monkeypatch, tmux_present):
    listing = "fleet-budget: 1 windows\nfleet-monitor: 1 windows\n"
    monkeypatch.setattr(guard, "sh", lambda cmd, timeout=10: (listing, 0))
    assert set(guard.loop_status().values()) == {"up"}


def test_one_missing_session_is_down(monkeypatch, tmux_present):
    monkeypatch.setattr(guard, "sh",
                        lambda cmd, timeout=10: ("fleet-budget: 1 windows", 0))
    status = guard.loop_status()
    assert status["fleet-budget"] == "up"
    assert status["fleet-monitor"] == "down"


def test_cold_selftest_uses_a_private_socket():
    """The default socket usually has a server, which tests the easy case."""
    assert guard.TMUX_SOCKET == ""
    assert guard.tmux() == guard.TMUX
