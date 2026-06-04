#!/usr/bin/env python3
"""Tests for local-only (no-repo) registry support.

A registry entry with no `repo` field is a plain local folder (e.g. a synced
Google Drive subfolder). The tooling must:
  - parse it fine (path + respawn, no repo),
  - skip ALL git for it (refresh_team_state),
  - still respawn it (respawn.py never touches git anyway),
without regressing repo-backed projects.

Run: python3 scripts/test_local_registry.py
"""
import os
import sys
import tempfile
import textwrap

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPTS_DIR, ".."))
RESPAWN_DIR = os.path.join(REPO_ROOT, ".claude", "skills", "respawn-sessions")
sys.path.insert(0, SCRIPTS_DIR)
sys.path.insert(0, RESPAWN_DIR)

import refresh_team_state as rts  # noqa: E402
import respawn  # noqa: E402


def test_parse_registry_local_only():
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(textwrap.dedent("""\
            projects:
              repo-proj:
                path: ~/dev/repo-proj
                repo: someorg/repo-proj
                respawn: true
                session_name: "Repo Proj"
              local-proj:
                path: ~/Documents/local-proj
                respawn: true
                session_name: "Local Proj"
        """))
        reg = f.name
    try:
        for mod in (rts, respawn):
            parsed = mod.parse_registry(reg)
            assert "local-proj" in parsed, mod.__name__
            assert parsed["local-proj"].get("repo", "") == "", "local project must have no repo"
            assert parsed["local-proj"]["path"] == "~/Documents/local-proj"
            assert parsed["local-proj"]["respawn"] == "true"
            assert parsed["repo-proj"]["repo"] == "someorg/repo-proj"
    finally:
        os.unlink(reg)
    print("ok: both parsers handle local-only (no repo)")


def test_git_state_local_only_skips_git():
    with tempfile.TemporaryDirectory() as d:
        st = rts.get_git_state(d, has_repo=False)
        assert st["available"] is False
        assert st.get("local") is True
        assert "local folder" in st["reason"]
    print("ok: get_git_state(has_repo=False) -> local, skips git")


def test_git_state_repo_flag_on_nongit_dir():
    with tempfile.TemporaryDirectory() as d:
        st = rts.get_git_state(d, has_repo=True)
        assert st["available"] is False
        assert st["reason"] == "not a git repo"
        assert not st.get("local")
    print("ok: get_git_state(has_repo=True) on non-git dir -> 'not a git repo' (regression)")


def test_git_state_real_repo():
    st = rts.get_git_state(REPO_ROOT, has_repo=True)
    assert st["available"] is True, "expected the metaproject repo to be a git repo"
    assert "branch" in st
    print("ok: get_git_state on a real repo -> available (regression)")


def test_render_local_only():
    peer = {"name": "local-proj", "session_name": "Local Proj", "path": "/tmp/x", "repo": ""}
    data = {"transcript": None, "git": rts.get_git_state("/tmp/x", has_repo=False)}
    section = rts.format_peer_section(peer, data)
    assert "Folder:** local" in section
    assert "Git:** unavailable" not in section
    print("ok: format_peer_section renders a local folder distinctly")


if __name__ == "__main__":
    test_parse_registry_local_only()
    test_git_state_local_only_skips_git()
    test_git_state_repo_flag_on_nongit_dir()
    test_git_state_real_repo()
    test_render_local_only()
    print("\nALL TESTS PASSED")
