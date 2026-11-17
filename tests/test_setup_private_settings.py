"""setup-private.sh provides .claude/settings.json without ever replacing a live one.

The file is local to the machine and not tracked. The main worktree's copy is
the one a running session uses, so the script may create it when it is missing
and may never overwrite, link over or delete it. A linked worktree gets a link
to the main worktree's copy, the way it gets the registry.
"""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "setup-private.sh")
SETTINGS = os.path.join(".claude", "settings.json")


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})


def run_setup(cwd, private):
    env = {**os.environ, "TEAM_LEAD_PRIVATE_CHECKOUT": str(private),
           "TEAM_LEAD_FLEET_PRIVATE_CONF": os.devnull}
    return subprocess.run(["/bin/bash", "./scripts/setup-private.sh"], cwd=cwd, env=env,
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    """A main worktree with the script, and a private checkout holding a backup."""
    main = tmp_path / "main"
    (main / "scripts").mkdir(parents=True)
    (main / "docs" / "process").mkdir(parents=True)
    (main / ".claude").mkdir()
    shutil.copy(SCRIPT, main / "scripts" / "setup-private.sh")
    (main / "docs" / "process" / ".keep").write_text("")
    (main / ".claude" / ".keep").write_text("")
    (main / ".gitignore").write_text(".claude/settings.json\nregistry.yaml\ndocs/process/*.md\n")
    git(main, "init", "-q", "-b", "main")
    git(main, "add", "-A")
    git(main, "commit", "-q", "-m", "init")
    private = tmp_path / "private"
    (private / "team-lead").mkdir(parents=True)
    (private / "team-lead" / "settings.json").write_text('{"from": "backup"}\n')
    return main, private


def add_worktree(main, name="wt", branch="feature"):
    wt = main.parent / name
    git(main, "worktree", "add", "-q", "-b", branch, str(wt))
    return wt


def test_main_worktrees_live_file_is_never_replaced(repo):
    main, private = repo
    live = main / SETTINGS
    live.write_text('{"live": true}\n')
    run_setup(main, private)
    assert not live.is_symlink()
    assert live.read_text() == '{"live": true}\n'


def test_a_live_file_equal_to_the_backup_stays_a_regular_file(repo):
    """Byte-identical is not a reason to swap it for a link."""
    main, private = repo
    live = main / SETTINGS
    live.write_text('{"from": "backup"}\n')
    run_setup(main, private)
    assert not live.is_symlink()


def test_a_missing_file_in_main_is_restored_as_a_copy(repo):
    main, private = repo
    out = run_setup(main, private)
    live = main / SETTINGS
    assert live.read_text() == '{"from": "backup"}\n'
    assert not live.is_symlink(), "a copy, so later edits do not write into the backup"
    assert "Restored" in out


def test_a_missing_file_with_no_backup_is_said_out_loud(repo, tmp_path):
    main, _ = repo
    out = run_setup(main, tmp_path / "no-such-checkout")
    assert not (main / SETTINGS).exists()
    assert "WARNING: .claude/settings.json is missing" in out


def test_a_linked_worktree_gets_a_link_to_mains_file(repo):
    main, private = repo
    (main / SETTINGS).write_text('{"live": true}\n')
    wt = add_worktree(main)
    run_setup(wt, private)
    linked = wt / SETTINGS
    assert linked.is_symlink()
    assert os.path.realpath(linked) == os.path.realpath(main / SETTINGS)


def test_a_regular_file_in_a_linked_worktree_is_left_alone(repo):
    main, private = repo
    (main / SETTINGS).write_text('{"live": true}\n')
    wt = add_worktree(main)
    (wt / SETTINGS).write_text('{"edited": "here"}\n')
    out = run_setup(wt, private)
    assert not (wt / SETTINGS).is_symlink()
    assert (wt / SETTINGS).read_text() == '{"edited": "here"}\n'
    assert "left alone" in out


def test_a_branch_that_still_tracks_the_file_is_not_linked(repo):
    main, private = repo
    (main / SETTINGS).write_text('{"live": true}\n')
    wt = add_worktree(main, "old", "old-branch")
    (wt / SETTINGS).write_text('{"tracked": true}\n')
    git(wt, "add", "-f", SETTINGS)
    git(wt, "commit", "-q", "-m", "tracks settings")
    run_setup(wt, private)
    assert not (wt / SETTINGS).is_symlink()
    assert (wt / SETTINGS).read_text() == '{"tracked": true}\n'


def test_switching_to_an_older_commit_and_back_brings_the_live_file_back(repo):
    """Git overwrites an ignored file on checkout. The kept copy undoes it."""
    main, private = repo
    live = main / ".claude" / "settings.json"
    # an older commit that still tracks the file
    git(main, "switch", "-q", "-c", "older")
    live.write_text('{"from": "an old commit"}\n')
    git(main, "add", "-f", ".claude/settings.json")
    git(main, "commit", "-q", "-m", "older commit tracks the file")
    git(main, "switch", "-q", "main")
    live.write_text('{"live": "edited here"}\n')
    run_setup(main, private)                      # what the hooks do
    git(main, "switch", "-q", "older")
    assert live.read_text() == '{"from": "an old commit"}\n'   # git replaced it
    run_setup(main, private)                      # must not keep the old commit's copy
    git(main, "switch", "-q", "main")
    assert not live.exists()                      # and git removed it on the way back
    out = run_setup(main, private)
    assert live.read_text() == '{"live": "edited here"}\n'
    assert "Restored" in out
