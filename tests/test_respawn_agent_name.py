"""A respawn must not rename a peer on its board.

Regression test for 2026-09-01: two sessions were hand-spawned without `-n`,
so `--mode running` would have fallen through to the tmux session name and
brought them back as "workspaces" and "clientorg-project-beta" instead of
"Workspaces" and "ClientOrg Project Beta". Everything those peers had written
under their real names would have been orphaned, with no error anywhere.
"""
import importlib.util
import os
import subprocess
from types import SimpleNamespace

import pytest

_spec = importlib.util.spec_from_file_location(
    "respawn", os.path.join(os.path.dirname(__file__), "..", ".claude",
                            "skills", "respawn-sessions", "respawn.py"))
respawn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(respawn)


def _fake_ps(monkeypatch, stdout, returncode=0):
    monkeypatch.setattr(
        respawn.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=returncode, stdout=stdout,
                                        stderr=""))


def test_reads_the_name_the_board_knows(monkeypatch):
    _fake_ps(monkeypatch,
             "PID TTY TIME CMD\n1 ?? 0:01 claude "
             "PATH=/usr/bin CW_AGENT_NAME=Workspaces TERM=xterm\n")
    assert respawn.agent_name_from_env(1) == "Workspaces"


def test_a_name_containing_spaces_survives(monkeypatch):
    """The exact 2026-09-01 case -- stopping at whitespace truncates to 'ClientOrg'."""
    _fake_ps(monkeypatch,
             "PID TTY TIME CMD\n1 ?? 0:01 claude "
             "CW_AGENT_NAME=ClientOrg Project Beta FEEDBACK_AGENT_NAME=ClientOrg 4128 "
             "Project Beta TERM=xterm\n")
    assert respawn.agent_name_from_env(1) == "ClientOrg Project Beta"


def test_the_name_is_last_in_the_env(monkeypatch):
    _fake_ps(monkeypatch,
             "PID TTY TIME CMD\n1 ?? 0:01 claude "
             "TERM=xterm CW_AGENT_NAME=Health Tool\n")
    assert respawn.agent_name_from_env(1) == "Health Tool"


def test_falls_back_to_the_pre_rename_spelling(monkeypatch):
    _fake_ps(monkeypatch,
             "PID TTY TIME CMD\n1 ?? 0:01 claude "
             "FEEDBACK_AGENT_NAME=Peer Bravo TERM=xterm\n")
    assert respawn.agent_name_from_env(1) == "Peer Bravo"


def test_cw_wins_over_the_old_spelling(monkeypatch):
    _fake_ps(monkeypatch,
             "PID TTY TIME CMD\n1 ?? 0:01 claude "
             "CW_AGENT_NAME=New Name FEEDBACK_AGENT_NAME=Old Name TERM=xterm\n")
    assert respawn.agent_name_from_env(1) == "New Name"


def test_no_identity_returns_none_so_the_caller_falls_through(monkeypatch):
    _fake_ps(monkeypatch, "PID TTY TIME CMD\n1 ?? 0:01 claude TERM=xterm\n")
    assert respawn.agent_name_from_env(1) is None


def test_an_empty_value_is_not_an_identity(monkeypatch):
    _fake_ps(monkeypatch,
             "PID TTY TIME CMD\n1 ?? 0:01 claude CW_AGENT_NAME= TERM=xterm\n")
    assert respawn.agent_name_from_env(1) is None


def test_a_dead_pid_is_none_not_an_exception(monkeypatch):
    _fake_ps(monkeypatch, "", returncode=1)
    assert respawn.agent_name_from_env(1) is None


def test_ps_failing_is_none_not_an_exception(monkeypatch):
    def boom(*a, **k):
        raise OSError("no ps")
    monkeypatch.setattr(respawn.subprocess, "run", boom)
    assert respawn.agent_name_from_env(1) is None


# --- the registry is the authority for the board name -------------------------
#
# 2026-09-02: a session spawned outside respawn.py had no `-n`, no identity var
# and no resolvable tmux ancestor, so the whole chain fell through to
# humanize(basename(cwd)) and it came back as "Claude Live Feedback Plugin".
# Its board writes were rejected. The registry knew it as "Workspaces" the
# entire time and nothing asked.

def _fake_registry(monkeypatch, projects):
    monkeypatch.setattr(respawn, "_REGISTRY_NAMES_CACHE", None)
    monkeypatch.setattr(respawn, "parse_registry", lambda _p: projects)
    monkeypatch.setattr(respawn.os.path, "isdir", lambda _p: True)
    monkeypatch.setattr(respawn.os.path, "realpath", lambda p: p)


def test_the_registry_name_beats_the_directory_name(monkeypatch):
    _fake_registry(monkeypatch, {
        "claude-live-feedback-plugin": {
            "path": "/dev/claude-live-feedback-plugin",
            "session_name": "Workspaces"},
    })
    assert (respawn.registry_session_name_for("/dev/claude-live-feedback-plugin")
            == "Workspaces")


def test_a_worktree_inherits_its_project_name(monkeypatch):
    """A peer in a worktree is still that peer on the board."""
    _fake_registry(monkeypatch, {
        "project-alpha": {"path": "/dev/project-alpha",
                                  "session_name": "ClientOrg Project Beta"},
    })
    assert (respawn.registry_session_name_for(
        "/dev/project-alpha/worktrees/feature-worktree/x")
        == "ClientOrg Project Beta")


def test_a_nested_project_wins_over_its_parent(monkeypatch):
    _fake_registry(monkeypatch, {
        "outer": {"path": "/dev/outer", "session_name": "Outer"},
        "inner": {"path": "/dev/outer/inner", "session_name": "Inner"},
    })
    assert respawn.registry_session_name_for("/dev/outer/inner") == "Inner"


def test_an_unregistered_path_falls_through_to_the_old_chain(monkeypatch):
    """None, not a guess -- the rest of the chain still has to run."""
    _fake_registry(monkeypatch, {
        "known": {"path": "/dev/known", "session_name": "Known"},
    })
    assert respawn.registry_session_name_for("/dev/somewhere-else") is None


def test_an_entry_without_session_name_humanizes_its_key(monkeypatch):
    _fake_registry(monkeypatch, {
        "peer-bravo": {"path": "/dev/peer-bravo"},
    })
    assert respawn.registry_session_name_for("/dev/peer-bravo") == "Peer Bravo"
