"""setup-private.sh provides .claude/settings.json without ever replacing a live one.

The file is local to the machine and not tracked. The main worktree's copy is
the one a running session uses, so the script may create it when it is missing
and may never overwrite, link over or delete it. A linked worktree gets its own
copy of the main worktree's file, never a link: a write inside a worktree must
not change the file the main session runs on.
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


def run_setup(cwd, private, script="./scripts/setup-private.sh"):
    env = {**os.environ, "TEAM_LEAD_PRIVATE_CHECKOUT": str(private),
           "TEAM_LEAD_FLEET_PRIVATE_CONF": os.devnull}
    return subprocess.run(["/bin/bash", str(script)], cwd=cwd, env=env,
                          capture_output=True, text=True, check=True).stdout


def untracked(cwd):
    return subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=cwd,
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


def test_a_linked_worktree_gets_its_own_copy_of_mains_file(repo):
    main, private = repo
    (main / SETTINGS).write_text('{"live": true}\n')
    wt = add_worktree(main)
    run_setup(wt, private)
    copy = wt / SETTINGS
    assert not copy.is_symlink()
    assert copy.read_text() == '{"live": true}\n'


def test_a_write_inside_a_worktree_does_not_change_mains_file(repo):
    main, private = repo
    (main / SETTINGS).write_text('{"live": true}\n')
    wt = add_worktree(main)
    run_setup(wt, private)
    with open(wt / SETTINGS, "w") as f:      # writes through a link, if it is one
        f.write('{"edited": "in the worktree"}\n')
    assert (main / SETTINGS).read_text() == '{"live": true}\n'
    run_setup(wt, private)                   # and a second run keeps the edit
    assert (wt / SETTINGS).read_text() == '{"edited": "in the worktree"}\n'


def test_a_link_to_mains_file_left_by_an_older_run_becomes_a_copy(repo):
    main, private = repo
    (main / SETTINGS).write_text('{"live": true}\n')
    wt = add_worktree(main)
    (wt / ".claude").mkdir(exist_ok=True)
    os.symlink(main / SETTINGS, wt / SETTINGS)
    run_setup(wt, private)
    assert not (wt / SETTINGS).is_symlink()
    assert (wt / SETTINGS).read_text() == '{"live": true}\n'
    assert (main / SETTINGS).read_text() == '{"live": true}\n'
    assert not (main / SETTINGS).is_symlink()


def test_a_link_to_some_other_file_is_left_alone(repo, tmp_path):
    main, private = repo
    (main / SETTINGS).write_text('{"live": true}\n')
    wt = add_worktree(main)
    other = tmp_path / "other.json"
    other.write_text('{"other": true}\n')
    (wt / ".claude").mkdir(exist_ok=True)
    os.symlink(other, wt / SETTINGS)
    out = run_setup(wt, private)
    assert os.path.realpath(wt / SETTINGS) == os.path.realpath(other)
    assert other.read_text() == '{"other": true}\n'
    assert "left alone" in out


def test_run_from_a_subdirectory_of_main_acts_on_the_root(repo):
    main, private = repo
    (main / SETTINGS).write_text('{"live": true}\n')
    before = untracked(main)
    run_setup(main / "scripts", private, script=main / "scripts" / "setup-private.sh")
    assert not (main / "scripts" / ".claude").exists()
    assert not os.path.lexists(main / "scripts" / "registry.yaml")
    assert untracked(main) == before
    assert not (main / SETTINGS).is_symlink()
    assert (main / SETTINGS).read_text() == '{"live": true}\n'


def test_run_from_a_subdirectory_of_main_restores_at_the_root(repo):
    main, private = repo
    run_setup(main / "scripts", private, script=main / "scripts" / "setup-private.sh")
    assert (main / SETTINGS).read_text() == '{"from": "backup"}\n'
    assert not (main / "scripts" / ".claude").exists()


def test_run_from_a_subdirectory_of_a_worktree_acts_on_its_root(repo):
    main, private = repo
    (main / SETTINGS).write_text('{"live": true}\n')
    wt = add_worktree(main)
    run_setup(wt / "scripts", private, script=wt / "scripts" / "setup-private.sh")
    assert (wt / SETTINGS).read_text() == '{"live": true}\n'
    assert not (wt / SETTINGS).is_symlink()
    assert not (wt / "scripts" / ".claude").exists()
    assert os.path.lexists(wt / "registry.yaml")
    assert not os.path.lexists(wt / "scripts" / "registry.yaml")


def test_a_regular_file_in_a_linked_worktree_is_left_alone(repo):
    main, private = repo
    (main / SETTINGS).write_text('{"live": true}\n')
    wt = add_worktree(main)
    (wt / SETTINGS).write_text('{"edited": "here"}\n')
    out = run_setup(wt, private)
    assert not (wt / SETTINGS).is_symlink()
    assert (wt / SETTINGS).read_text() == '{"edited": "here"}\n'
    assert "left alone" in out


def test_a_branch_that_still_tracks_the_file_keeps_its_tracked_copy(repo):
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
