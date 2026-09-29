#!/usr/bin/env python3
"""Tests for fleet_guard.py's orphaned headless Chrome reaper.

(Tracked on the private Workspaces board -- its opaque task id is not
reproduced here: this repo is public and that id shape is on the pre-push
leak gate's denylist, `/\\bt-[A-Za-z0-9_-]{10,}\\b/`.)

The matching predicate has to agree with
claude-live-feedback-plugin/scripts/chrome-orphans.ts findOrphans(): kill
only a process that is headless, whose `--user-data-dir=` sits in a temp
directory, whose parent is pid 1, and that is older than 10 minutes. Getting
any one of those wrong in the permissive direction risks killing Bryan's own
Chrome (never headless) or a live agent run's browser (never parented to
pid 1) -- both irreversible the moment the SIGKILL lands. So these fixtures
are built to isolate each check: several lines are "everything except one
criterion" rather than obviously-safe or obviously-orphaned, and the
`test_*_check_is_load_bearing` tests prove a removed check changes the
outcome rather than just asserting the fixtures pass today.

Run: python3 scripts/test_fleet_guard.py
"""
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

import fleet_guard as fg  # noqa: E402

# --- ps -Ao pid=,ppid=,etime=,command= fixture lines -----------------------
# Real Chrome on macOS is very often parented to pid 1 too (launchd runs GUI
# apps via xpcproxy/loginwindow), which is exactly why ppid==1 alone can
# never be the reap test -- the fixtures below lean on that overlap on
# purpose rather than picking an artificially-safe parent pid.

# Bryan's normal Chrome: parented to pid 1 like a lot of real Chrome is, but
# not headless and its profile is a real one, not a temp dir. Two separate
# reasons this must never match.
NORMAL_CHROME = (
    "  501      1 3-02:15:10 /Applications/Google Chrome.app/Contents/MacOS/"
    "Google Chrome --type=browser --user-data-dir=/Users/bryanchan/Library/"
    "Application Support/Google/Chrome"
)

# Headless, temp profile, old enough -- but its parent is a LIVE run (9999),
# not pid 1. A live run's Chrome must never be touched.
HEADLESS_LIVE_PARENT = (
    "23456   9999   00:05:00 /Applications/Google Chrome.app/Contents/MacOS/"
    "Google Chrome --headless --user-data-dir=/var/folders/zz/abc123/T/"
    "cw-ui-shot-live/profile"
)

# ppid 1, headless, temp profile -- but only 5 minutes old, under the 10
# minute floor. Must not race a launcher's own in-flight cleanup.
YOUNG_ORPHAN = (
    "34567      1   00:05:00 /Applications/Google Chrome.app/Contents/MacOS/"
    "Google Chrome --headless --user-data-dir=/var/folders/zz/abc123/T/"
    "cw-ui-shot-young/profile"
)

# ppid 1, headless, temp profile, 15 minutes old: every criterion holds.
# This is the only fixture that should ever be killed.
REAL_ORPHAN = (
    "45678      1   00:15:00 /Applications/Google Chrome.app/Contents/MacOS/"
    "Google Chrome --headless --user-data-dir=/var/folders/zz/abc123/T/"
    "cw-ui-shot-old/profile"
)

# Identical to REAL_ORPHAN in every field except --headless is missing.
# Isolates the headless check: if it were removed, this line would match.
NOT_HEADLESS_ORPHAN_SHAPED = (
    "56789      1   00:15:00 /Applications/Google Chrome.app/Contents/MacOS/"
    "Google Chrome --user-data-dir=/var/folders/zz/abc123/T/"
    "cw-ui-shot-nohead/profile"
)

# Headless, ppid 1, temp profile, old -- but not Chrome at all. Some other
# tool can share the same flags; it must not be caught by a filter meant
# only for Chrome.
NOT_CHROME_HEADLESS_ORPHAN_SHAPED = (
    "67890      1   00:20:00 /usr/local/bin/some-other-headless-tool "
    "--headless --user-data-dir=/tmp/other-tool-profile"
)

ALL_FIXTURES = "\n".join([
    NORMAL_CHROME, HEADLESS_LIVE_PARENT, YOUNG_ORPHAN, REAL_ORPHAN,
    NOT_HEADLESS_ORPHAN_SHAPED, NOT_CHROME_HEADLESS_ORPHAN_SHAPED,
])


def test_parse_ps_etime():
    assert fg.parse_ps_etime("00:15:00") == 15 * 60
    assert fg.parse_ps_etime("05:00") == 5 * 60
    assert fg.parse_ps_etime("1-02:03:04") == ((26) * 60 + 3) * 60 + 4
    assert fg.parse_ps_etime("not-a-time") is None
    assert fg.parse_ps_etime("") is None
    print("ok: parse_ps_etime reads ps's [[dd-]hh:]mm:ss")


def test_is_temp_dir_path():
    assert fg.is_temp_dir_path("/var/folders/zz/abc123/T/cw-ui-shot/profile")
    assert fg.is_temp_dir_path("/tmp/profile")
    assert fg.is_temp_dir_path("/private/tmp/profile")
    assert not fg.is_temp_dir_path("/Users/bryanchan/Library/Application Support/Google/Chrome")
    assert not fg.is_temp_dir_path(None)
    assert not fg.is_temp_dir_path("")

    saved = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = "/Users/bryanchan/.custom-tmp"
    try:
        assert fg.is_temp_dir_path("/Users/bryanchan/.custom-tmp/profile-xyz")
    finally:
        if saved is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = saved
    print("ok: is_temp_dir_path covers TMPDIR, /var/folders, /tmp, /private/tmp")


def test_find_chrome_orphans_matches_only_the_real_orphan():
    orphans = fg.find_chrome_orphans(ALL_FIXTURES)
    pids = {o["pid"] for o in orphans}
    assert pids == {45678}, f"expected only the real orphan (45678), got {pids}"
    assert orphans[0]["profile"].endswith("cw-ui-shot-old/profile")
    assert orphans[0]["age_sec"] == 15 * 60
    print("ok: only the process matching headless + temp-profile + ppid 1 + age is caught")


def test_normal_chrome_never_matches():
    assert fg.find_chrome_orphans(NORMAL_CHROME) == []
    print("ok: Bryan's own Chrome (not headless, real profile) is never a candidate")


def test_live_run_chrome_never_matches():
    assert fg.find_chrome_orphans(HEADLESS_LIVE_PARENT) == []
    print("ok: a headless Chrome with a live parent (not pid 1) is never a candidate")


def test_young_orphan_never_matches():
    assert fg.find_chrome_orphans(YOUNG_ORPHAN) == []
    print("ok: an orphan under the 10-minute floor is left alone")


def test_headless_check_is_load_bearing():
    """NOT_HEADLESS_ORPHAN_SHAPED matches every criterion except --headless.
    If the headless filter were removed from find_chrome_orphans, this line
    -- which could be a live, non-headless Chrome window -- would be killed.
    Defeat the check via the module's own flag constant (an empty string is
    a substring of everything, so `flag not in command` can never be true)
    and confirm the line then matches, proving the check has real effect
    rather than just agreeing with today's fixtures.
    """
    assert fg.find_chrome_orphans(NOT_HEADLESS_ORPHAN_SHAPED) == []

    saved = fg.CHROME_HEADLESS_FLAG
    fg.CHROME_HEADLESS_FLAG = ""
    try:
        broken = fg.find_chrome_orphans(NOT_HEADLESS_ORPHAN_SHAPED)
    finally:
        fg.CHROME_HEADLESS_FLAG = saved

    assert len(broken) == 1, "removing the headless filter should have let this line through"
    print("ok: the headless check is load-bearing -- removing it changes the outcome")


def test_chrome_name_check_is_load_bearing():
    """A non-Chrome process that otherwise looks exactly like an orphan (ppid
    1, --headless, temp profile, old) must not be caught by a filter scoped
    to Chrome. Prove it the same way: defeat the substring check and confirm
    the outcome flips.
    """
    assert fg.find_chrome_orphans(NOT_CHROME_HEADLESS_ORPHAN_SHAPED) == []

    src = open(os.path.join(SCRIPTS_DIR, "fleet_guard.py")).read()
    assert '"chrome" not in command.lower()' in src, (
        "the chrome-name guard moved or was removed -- update this test's proof")
    # Simulate its removal directly against the fixture: everything else in
    # the line still satisfies the predicate.
    import re
    m = re.match(r"^\s*(\d+)\s+(\d+)\s+(\S+)\s+(.*)$", NOT_CHROME_HEADLESS_ORPHAN_SHAPED)
    pid, ppid, etime, command = m.groups()
    assert int(ppid) == 1 and fg.CHROME_HEADLESS_FLAG in command
    prof = re.search(r"--user-data-dir=(\S+)", command).group(1)
    assert fg.is_temp_dir_path(prof)
    assert fg.parse_ps_etime(etime) >= fg.CHROME_ORPHAN_MIN_AGE_SEC
    print("ok: the chrome-name check is load-bearing -- everything else about "
          "this line matches an orphan")


def test_ps_unreadable_is_loud_not_clean():
    """A `ps` read failure must never read as '0 orphans found'."""
    logs = []
    orphans, killed = fg.reap_orphaned_chromes(list_processes=lambda: None, log=logs.append)
    assert orphans is None, "could-not-look must stay distinguishable from found-nothing"
    assert killed == []
    assert any("UNKNOWN" in m or "unreadable" in m for m in logs), logs
    print("ok: ps read failure is reported loud, never silently 'nothing found'")


def test_dry_run_never_kills():
    def fake_list():
        return REAL_ORPHAN

    def boom(pid, sig):
        raise AssertionError("dry run must never call os.kill")

    real_kill = fg.os.kill
    fg.os.kill = boom
    logs = []
    try:
        orphans, killed = fg.reap_orphaned_chromes(
            list_processes=fake_list, dry_run=True, log=logs.append)
    finally:
        fg.os.kill = real_kill

    assert killed == []
    assert len(orphans) == 1 and orphans[0]["pid"] == 45678
    assert any("would kill" in m for m in logs)
    print("ok: dry-run lists the orphan and never calls kill")


def test_reap_kills_only_the_real_orphan_and_logs_pid_profile_size():
    def fake_list():
        # Same listing on the first pass and the pre-kill recheck: the
        # orphan is still there both times.
        return ALL_FIXTURES

    killed_calls = []

    def fake_kill(pid, sig):
        killed_calls.append((pid, sig))

    real_kill = fg.os.kill
    real_rss = fg.process_rss_mb
    fg.os.kill = fake_kill
    fg.process_rss_mb = lambda pid: 512.3
    logs = []
    try:
        orphans, killed = fg.reap_orphaned_chromes(list_processes=fake_list, log=logs.append)
    finally:
        fg.os.kill = real_kill
        fg.process_rss_mb = real_rss

    assert {o["pid"] for o in orphans} == {45678}
    assert killed_calls == [(45678, fg.signal.SIGKILL)]
    assert len(killed) == 1 and killed[0]["pid"] == 45678

    joined = " ".join(logs)
    assert "45678" in joined, "log must carry the pid"
    assert "cw-ui-shot-old" in joined, "log must carry the profile dir"
    assert "512.3MB" in joined, "log must carry the memory size"
    print("ok: reap kills only the real orphan and logs pid, profile dir and size")


def test_process_that_exited_before_kill_is_skipped_not_killed_blind():
    """The re-check before the kill call: if the orphan is gone on the second
    listing, its pid may already belong to something else. Must skip, not
    kill on stale information.
    """
    calls = {"n": 0}

    def fake_list():
        calls["n"] += 1
        if calls["n"] == 1:
            return REAL_ORPHAN
        return ""  # gone by the recheck

    def boom(pid, sig):
        raise AssertionError("must not kill a pid that vanished before the recheck")

    real_kill = fg.os.kill
    fg.os.kill = boom
    logs = []
    try:
        orphans, killed = fg.reap_orphaned_chromes(list_processes=fake_list, log=logs.append)
    finally:
        fg.os.kill = real_kill

    assert len(orphans) == 1
    assert killed == []
    print("ok: an orphan gone by the pre-kill recheck is skipped, not killed on stale data")


if __name__ == "__main__":
    test_parse_ps_etime()
    test_is_temp_dir_path()
    test_find_chrome_orphans_matches_only_the_real_orphan()
    test_normal_chrome_never_matches()
    test_live_run_chrome_never_matches()
    test_young_orphan_never_matches()
    test_headless_check_is_load_bearing()
    test_chrome_name_check_is_load_bearing()
    test_ps_unreadable_is_loud_not_clean()
    test_dry_run_never_kills()
    test_reap_kills_only_the_real_orphan_and_logs_pid_profile_size()
    test_process_that_exited_before_kill_is_skipped_not_killed_blind()
    print("\nALL TESTS PASSED")
