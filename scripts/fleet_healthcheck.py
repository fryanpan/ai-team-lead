#!/usr/bin/env python3
"""Fleet health check — asserts END STATE, not liveness. Zero tokens.

WHY THIS EXISTS
---------------
Every outage found on 2026-08-11 was "running but not working", and a process
check would have called all of them green:

  * a message broker up on its port with no API token, so it never polled
  * a bridge daemon alive for 2.5 days doing nothing but logging 403s
  * a receiver bound to the wrong port, so its tunnel delivered to nobody
  * two listeners on one port in different address families, both "fine"
  * plugins enabled (tools present) but never named on the launch line, so no
    inbound event could ever wake a session

So no check here is allowed to conclude "healthy" from a PID. Each one asserts
the observable end state: a port has exactly one listener, an endpoint returns
the expected body, an error stream is quiet, a launch line carries the flag.

DESIGN CONSTRAINTS (both learned the hard way)
---------------------------------------------
1. ZERO TOKENS ON GREEN. No model runs. A plain script checks and stays silent;
   only a RED result notifies a human. Steady state costs nothing, so this can
   run several times a day forever.

2. THIS FILE MUST LIVE ON THE BOOT DISK. Under launchd, an Apple-signed
   interpreter cannot open ANY file on the secondary volume (/Volumes/Data) --
   `[Errno 1] Operation not permitted`, verified 2026-08-11. A monitor whose own
   source lived there would be killed by the exact class of bug it exists to
   catch. install-healthcheck.sh deploys this file and its generated config to
   ~/Library/Application Support/team-lead/. Everything read at runtime -- config,
   log files -- must therefore be a boot-disk path. ps/lsof/launchctl/curl are
   fine: they read kernel state or the network, not that volume.

Exit 0 = all green. Exit 1 = at least one RED.
"""

import hashlib
import json
import io
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta

# Boot-disk deploy root. Everything launchd execs or reads must live under a
# path that resolves to the boot disk -- and under $HOME most of the dotfile
# tree does NOT: ~/.claude, ~/.config, ~/.local and ~/.bun are each a symlink
# into /Volumes/Data on this machine. /opt is genuinely disk3s5.
DEPLOY_ROOT = "/opt/fleet"


def _state_dir():
    """Where the deployed copy keeps its config and status.

    Resolved rather than hardcoded so the deploy root can move without editing
    the checker: the script's own directory wins (install_healthcheck.py puts
    the config beside the copy it deploys), then the known roots in order. Only
    a directory that actually holds the config counts, so running the repo copy
    from a checkout still finds the deployed state instead of silently starting
    with no checks.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (here, DEPLOY_ROOT,
              os.path.expanduser("~/Library/Application Support/team-lead")):
        if os.path.exists(os.path.join(d, "healthcheck-config.json")):
            return d
    return DEPLOY_ROOT


STATE_DIR = _state_dir()
CONFIG = os.path.join(STATE_DIR, "healthcheck-config.json")
STATUS = os.path.join(STATE_DIR, "healthcheck-status.json")


_PREV_STATUS = None
_STREAKS = {}


_TCC_RECORD = {}


def prev_status():
    """Last run's status file, or {} on the first run.

    A per-run check cannot see a slow drip: a failure arriving every 40 minutes
    never crosses a 3-per-90-minutes threshold in any single run, so it stays
    green forever while being continuously broken. Carrying a streak across runs
    is the cheapest way to give a check memory.
    """
    global _PREV_STATUS
    if _PREV_STATUS is None:
        try:
            with open(STATUS) as f:
                _PREV_STATUS = json.load(f)
        except Exception:
            _PREV_STATUS = {}
    return _PREV_STATUS


def bump_streak(name, had_error):
    """Consecutive runs on which this check saw at least one error."""
    prior = prev_status().get("streaks", {}).get(name, 0)
    n = prior + 1 if had_error else 0
    _STREAKS[name] = n
    return n


def sh(cmd, timeout=15):
    """Run a shell command, returning stdout ('' on any failure)."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout)
        return r.stdout
    except Exception:
        return ""


def claude_sessions():
    """[(pid, argv)] for every Claude session owned by this user.

    Enumerated from `ps`, deliberately NOT from `pgrep -f`. On this machine
    pgrep omits the team-lead's own process -- it is absent from
    `pgrep -U <uid> -f 'bin/claude'` while `ps` shows a plainly matching
    command line for it. A monitor that cannot see the team-lead is worse than
    no monitor, and the failure is silent, so ps is the source of truth.

    The uid filter matters: a second macOS account on this machine runs its own
    Claude sessions, and reporting its config as our failure would be noise.
    """
    uid = os.getuid()
    out = []
    for line in sh("ps -axww -o pid=,uid=,command=").splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, puid, argv = parts
        if puid != str(uid) or "/bin/claude" not in argv:
            continue
        if "claude-hive-mcp" in argv or "/bin/zsh" in argv.split()[0]:
            continue
        out.append((pid, argv))
    return out


# Candidate bun binaries, in preference order. Resolution is by PROBE, never by
# path: which of these works depends on the context this script is running in
# and on a TCC grant that has already lapsed once.
#
# TCC attaches per BINARY plus a Full Disk Access grant, not per volume
# (verified 2026-09-01, see fleet-ops.md). So:
#   - ~/.bun/bin/bun can NEVER work under launchd -- ~/.bun is a symlink into
#     /Volumes/Data, so the binary itself is on the blocked volume and dies at
#     exec. Granting it FDA cannot fix that. It stays here because it is the
#     right choice from a shell, where there is no gate at all.
#   - A boot-disk bun works under launchd only while it holds a grant. The
#     claude-workspaces copy has one; it belongs to another project, so treat
#     it as something that can vanish, never as a fixed dependency.
BUN_CANDIDATES = [
    os.path.expanduser("~/Library/Application Support/claude-workspaces/bin/bun"),
    "/opt/homebrew/bin/bun",
    os.path.expanduser("~/.bun/bin/bun"),
]

# A file on the secondary volume, used to probe for the capability we actually
# need. Any tracked file on that volume would do.
PROBE_DATA_FILE = "/Volumes/Data/Users/bryanchan/dev/ai-team-lead/README.md"

# Seconds to wait on any bun call. Was 30, which turned a broken bun into a
# three-minute run: six checks each burning a full timeout in series. A probe
# that cannot answer in a few seconds is not going to answer.
BUN_TIMEOUT = 6
_BUN_RESOLVED = None          # (path_or_None, reason)


def resolve_bun():
    """Pick a bun that can READ THE SECONDARY VOLUME here. Probed once, cached.

    The old probe ran `console.log(1)` and asked only whether bun executes.
    That is the wrong question, and it would pass a boot-disk bun with no Full
    Disk Access while every dependent check failed -- the check would look
    healthy and its dependents would each report their own private mystery,
    which is the exact failure this probe was added to end.

    So probe the capability, not the tool: have each candidate read a file on
    the secondary volume. The first that returns its contents is the one every
    other check uses.
    """
    global _BUN_RESOLVED
    if _BUN_RESOLVED is not None:
        return _BUN_RESOLVED

    tried = []
    script = (f"console.log(require('fs')"
              f".readFileSync({PROBE_DATA_FILE!r},'utf8').length)")
    for cand in BUN_CANDIDATES:
        if not os.path.exists(cand):
            tried.append(f"{os.path.basename(os.path.dirname(cand))}/bun: absent")
            continue
        try:
            r = subprocess.run([cand, "-e", script], capture_output=True,
                               text=True, cwd="/", timeout=BUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            tried.append(f"{cand}: hung")      # pre-reboot signature
            continue
        except Exception as exc:
            tried.append(f"{cand}: {type(exc).__name__}")
            continue
        if r.returncode == 0 and r.stdout.strip().isdigit():
            _BUN_RESOLVED = (cand, f"reads the secondary volume ({cand})")
            return _BUN_RESOLVED
        err = (r.stderr or "").strip().splitlines()
        tried.append(f"{cand}: {err[-1][:60] if err else 'exit ' + str(r.returncode)}")

    _BUN_RESOLVED = (None, "; ".join(tried))
    return _BUN_RESOLVED


def bun_works():
    return resolve_bun()[0] is not None


def bun_path():
    """The resolved binary. Callers must check bun_works() first."""
    return resolve_bun()[0]


BUN_UNAVAILABLE = ("no bun on this machine can read the secondary volume in "
                   "this context, so anything stored there is unreadable "
                   "here -- see fleet-ops.md")


_PLUGIN_PROBE_JS = r"""
const fs = require("fs");
// `bun -e` argv is [bunPath, ...args] -- there is no script slot, so this is
// slice(1), not the slice(2) a node script would use.
const [cacheDir, manifest] = process.argv.slice(1);
const out = {live: null, source: null, err: null};
try {
  out.live = fs.readdirSync(cacheDir, {withFileTypes: true})
    .filter(d => d.isDirectory())
    .map(d => d.name)
    .filter(n => !fs.existsSync(cacheDir + "/" + n + "/.orphaned_at"));
} catch (e) { out.err = String(e); }
try {
  out.source = JSON.parse(fs.readFileSync(manifest, "utf8")).version || null;
} catch (e) {}
console.log(JSON.stringify(out));
"""


def exists_via_bun(path):
    """Existence test for a path that may resolve onto the secondary volume."""
    if not bun_works():
        return os.path.exists(path)
    r = subprocess.run(
        [bun_path(), "-e",
         # `bun -e` argv is [bunPath, ...args] -- the path is argv[1], not [2].
         'process.exit(require("fs").existsSync(process.argv[1]) ? 0 : 1)',
         "--", path],
        capture_output=True, cwd="/", timeout=BUN_TIMEOUT)
    return r.returncode == 0


def probe_plugin_via_bun(cache_dir, source_manifest):
    """Run the whole plugin-version probe inside bun -- readdir included.

    Under launchd an Apple-signed interpreter cannot touch /Volumes/Data at all:
    not its own script, not a data file, and not even a directory listing (all
    three measured). The gate is per-binary rather than per-process-tree, and
    `bun` is a user install that reaches the volume fine -- which is why the
    notion and github daemons work at all.

    Delegating only the manifest READ was not enough, and that was the bug: on
    this machine `~/.claude`, `~/.local` and `~/.bun` are each a symlink into
    /Volumes/Data, so the plugin cache under `~/.claude/plugins/cache` IS the
    secondary volume. A path being under $HOME says nothing about which disk it
    lands on -- resolve it before assuming a boot-disk read is safe.

    Returns (live_version_dirs, source_version). Either may be None, meaning
    "could not determine" -- never confuse that with "found nothing".
    """
    if not bun_works():
        return None, None
    r = subprocess.run([bun_path(), "-e", _PLUGIN_PROBE_JS, "--",
                        cache_dir, source_manifest],
                       capture_output=True, text=True, cwd="/", timeout=BUN_TIMEOUT)
    if r.returncode != 0:
        return None, None
    try:
        data = json.loads(r.stdout)
    except Exception:
        return None, None
    return data.get("live"), data.get("source")


_ARCHIVE_BACKLOG_JS = r"""
const fs = require("fs"), path = require("path");
// `bun -e` argv is [bunPath, ...args] -- slice(1), not slice(2).
const [liveRoot, archiveRoot, ...ignoreDirs] = process.argv.slice(1);
const out = {unarchived: null, oldestDays: null, oldestName: null, err: null};

// Session id is the FILENAME, never the directory. A worktree cwd gets its own
// encoded project dir and the transcript may live under either it or the main
// repo's dir -- placement genuinely varies. Matching on the id is right in both
// worlds; matching on the path silently misses whichever half it guessed wrong.
function jsonlIds(root, into) {
  for (const d of fs.readdirSync(root, {withFileTypes: true})) {
    const full = path.join(root, d.name);
    if (d.isDirectory()) { jsonlIds(full, into); continue; }
    if (d.isFile() && d.name.endsWith(".jsonl")) into.set(d.name, full);
  }
  return into;
}

try {
  const archived = new Set(jsonlIds(archiveRoot, new Map()).keys());
  const live = jsonlIds(liveRoot, new Map());
  const now = Date.now();
  let count = 0, oldestMs = -1, oldestName = null;
  for (const [name, full] of live) {
    if (archived.has(name)) continue;
    if (ignoreDirs.some(d => full.includes("/" + d + "/"))) continue;
    count++;
    // mtime is only an AGE here, never a liveness claim -- the file's absence
    // from the archive is the assertion, and that came from a real readdir.
    const age = now - fs.statSync(full).mtimeMs;
    if (age > oldestMs) { oldestMs = age; oldestName = name; }
  }
  out.unarchived = count;
  out.oldestDays = oldestMs < 0 ? 0 : oldestMs / 86400000;
  out.oldestName = oldestName;
} catch (e) { out.err = String(e); }
console.log(JSON.stringify(out));
"""


_READ_SHA_JS = r"""
const fs = require("fs"), crypto = require("crypto");
// `bun -e` argv is [bunPath, ...args] -- slice(1), not slice(2).
try {
  const buf = fs.readFileSync(process.argv.slice(1)[0]);
  console.log(crypto.createHash("sha256").update(buf).digest("hex"));
} catch (e) { process.exit(3); }
"""


def check_self_version(spec):
    """The DEPLOYED checker is the same file as the one in the repo.

    Editing the repo copy changes nothing -- launchd execs the deployed copy, so
    a forgotten `install_healthcheck.py` leaves a stale checker running three
    green times a day with the new assertion absent from every run, and no
    surface anywhere saying the new check never executed. That is this monitor's
    own failure mode turned on itself, and it is the reason this check exists.

    Same live-vs-source shape as check_plugin_version. The repo lives on the
    secondary volume, so the source read goes through bun; the deployed copy is
    on the boot disk and reads normally.
    """
    if not bun_works():
        return False, f"{spec['name']}: cannot verify -- {BUN_UNAVAILABLE}"
    source = os.path.expanduser(spec["source"])
    running = os.path.abspath(__file__)

    try:
        with open(running, "rb") as f:
            live_sha = hashlib.sha256(f.read()).hexdigest()
    except OSError as e:
        return False, f"{spec['name']}: cannot read running copy {running}: {e}"

    if not bun_works():
        return False, f"{spec['name']}: cannot verify -- {BUN_UNAVAILABLE}"
    r = subprocess.run([bun_path(), "-e", _READ_SHA_JS, "--", source],
                       capture_output=True, text=True, cwd="/", timeout=BUN_TIMEOUT)
    if r.returncode != 0:
        # Unreadable source is NOT a pass. Absence of a comparison is absence of
        # information, and this monitor never converts that into a green line.
        return False, (f"{spec['name']}: cannot read source at {source} "
                       f"-- comparison impossible, not clean")
    source_sha = (r.stdout or "").strip()

    if source_sha != live_sha:
        return False, (f"{spec['name']}: DEPLOYED COPY IS STALE -- "
                       f"{running} does not match {source}. Every check below "
                       f"ran from the old file. Fix: python3 "
                       f"scripts/install_healthcheck.py")
    return True, f"{spec['name']}: deployed copy matches source"


def check_archive_backlog(spec):
    """Transcripts are reaching the archive before the live store rotates them.

    Asserts DELIVERY, not that an archiver ran: it compares session ids present
    in the live store against ids present anywhere in the archive, and reports
    the age of the oldest one that never made it. A copy that stopped happening
    shows up here even if the process reporting it is healthy -- which is the
    case that produced this check. The scheduled archiver was dead for 17 days
    (2026-08-08 onward, `Operation not permitted` at exec, hourly) and NOTHING
    surfaced it, because running the analysis pipeline by hand copied the same
    files as a side effect and kept the folder looking current.

    Why the threshold is generous: the live store holds ~16 weeks, so a backlog
    is only permanent loss once it approaches that. Alarming at 21 days gives
    weeks of margin while still being far shorter than the window in which the
    loss becomes irreversible.

    Everything runs inside bun. Both paths resolve onto the secondary volume, so
    an Apple-signed interpreter cannot readdir either of them under launchd.
    Note also that a `stat`-based freshness probe would be WRONG here even with
    the right interpreter: stat succeeds on that volume where open fails, so a
    denied checker reports a healthy mtime on data it cannot read.
    """
    if not bun_works():
        return False, f"{spec['name']}: cannot verify -- {BUN_UNAVAILABLE}"
    live = os.path.expanduser(spec["live_root"])
    archive = os.path.expanduser(spec["archive_root"])
    max_age = float(spec.get("max_age_days", 21))
    # No default exclusions: the walk already filters to `.jsonl`, and the
    # archive demonstrably mirrors `subagents/` too -- excluding those would
    # hide an entire class of unarchived transcript behind a green line.
    ignore = spec.get("ignore_dirs", [])

    if not bun_works():
        return False, f"{spec['name']}: cannot verify -- {BUN_UNAVAILABLE}"
    for label, pth in (("live store", live), ("archive", archive)):
        if not exists_via_bun(pth):
            return False, f"{spec['name']}: {label} MISSING at {pth}"

    r = subprocess.run([bun_path(), "-e", _ARCHIVE_BACKLOG_JS, "--",
                        live, archive, *ignore],
                       capture_output=True, text=True, cwd="/", timeout=180)
    if r.returncode != 0:
        return False, (f"{spec['name']}: probe failed rc={r.returncode} "
                       f"{(r.stderr or '').strip()[:120]}")
    try:
        d = json.loads(r.stdout)
    except Exception:
        return False, f"{spec['name']}: probe returned unparseable output"
    if d.get("err"):
        return False, f"{spec['name']}: probe error {d['err'][:120]}"

    # "Could not determine" is never a pass. Absence has three causes and only
    # one of them is information; a blind checker must alarm on its own blindness.
    if d.get("unarchived") is None:
        return False, f"{spec['name']}: probe returned no result"

    n, age = d["unarchived"], d.get("oldestDays") or 0.0
    if n and age > max_age:
        # Name who clears it, in the line itself. A RED with no owner reads as
        # furniture -- this monitor emitted 116 of them before anyone read one.
        owner = spec.get("owner", "unowned -- assign before this fires again")
        return False, (f"{spec['name']}: {n} transcript(s) never archived, "
                       f"oldest {age:.1f}d > {max_age:.0f}d -- the copy step has "
                       f"stopped; live store rotates at ~16w. Owner: {owner}")
    return True, (f"{spec['name']}: {n} unarchived, oldest {age:.1f}d "
                  f"(alarms past {max_age:.0f}d)")


def _semver(s):
    out = []
    for part in str(s).split("."):
        digits = "".join(c for c in part if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def check_plugin_version(spec):
    """The installed plugin cache must match the version in its source repo.

    This is the "fleet silently runs old code" check. It has bitten twice with
    no external symptom: the fleet ran six weeks behind main, and writing rules
    were edited and deployed to nobody. A stale plugin looks exactly like a
    working one from inside a session.

    Compares the highest non-orphaned version directory in the cache against
    the source manifest. Note an updated cache leaves the OLD directory behind
    with an `.orphaned_at` marker, and writing that marker TOUCHES it -- so the
    superseded copy becomes newest by mtime. Never pick by mtime.
    """
    cache_root = os.path.expanduser(spec["cache_dir"])
    live, source = probe_plugin_via_bun(cache_root, spec["source_manifest"])

    if live is None:
        return False, f"{spec['name']}: could not read plugin cache at {cache_root}"
    if not live:
        return False, f"{spec['name']}: cache has no live version dir"
    installed = max(live, key=_semver)

    if not source:
        return True, f"{spec['name']}: {installed} installed (source unreadable)"

    if _semver(source) > _semver(installed):
        return False, (f"{spec['name']}: STALE — fleet runs {installed}, "
                       f"repo is {source}; run "
                       f"`claude plugin update {spec['plugin']}@{spec['marketplace']}` "
                       f"then respawn (a session reads the cache at startup)")
    return True, f"{spec['name']}: {installed} matches source"


def session_cwd(pid):
    """Working directory of a pid, or '' if it can't be read."""
    for line in sh(f"lsof -a -p {pid} -d cwd -Fn 2>/dev/null").splitlines():
        if line.startswith("n"):
            return line[1:]
    return ""


# --- checks ---------------------------------------------------------------
# Each returns (ok: bool, detail: str). Detail is shown only when not ok, so
# it must say what is wrong and where to look -- it is the whole notification.

# A process is called inert only when all three of these hold at once. Each
# alone is normal: daemons idle, small tools hold few descriptors, and anything
# looks quiet at first. Together they mean it forked and never worked.
INERT_MIN_AGE_SEC = 300      # below this, quiet is just "still starting"
INERT_MAX_CPU_SEC = 1.0      # a process that has done work has burned a second
INERT_MAX_FDS = 12           # script + config + socket + logs clears this easily


def check_launchd(spec):
    """A LaunchAgent must have a live PID and a zero last-exit.

    `launchctl list` prints "PID  LAST_EXIT  LABEL"; a '-' PID means not
    running. Note the last-exit column is HISTORICAL -- a live process can show
    a nonzero code from a previous run, which is why the PID is checked first
    and reported separately. Reading only the exit column is how a daemon that
    had been up for days got reported as dead.
    """
    # `label` may be a list: a job being renamed is live under either spelling
    # during the transition, and a check pinned to one of them reports the
    # other as a dead daemon. That is a FALSE RED, and it is worse than no
    # check -- com.fryanpan.live-feedback was reported "not loaded in launchd
    # at all" for 8 consecutive runs while the job ran healthy under its new
    # name com.fryanpan.claude-workspaces (2026-08-21). Accept any spelling;
    # report which one actually matched.
    labels = spec["label"]
    if isinstance(labels, str):
        labels = [labels]
    for line in sh("launchctl list").splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[2] in labels:
            label = parts[2]
            pid, last_exit = parts[0], parts[1]
            if pid == "-":
                return False, f"{label}: NOT RUNNING (last exit {last_exit})"
            inert = _inert_reason(label, pid)
            if inert:
                return False, f"{label}: LOADED BUT INERT -- pid {pid} {inert}"
            return True, f"{label}: pid {pid}"
    return False, f"{' / '.join(labels)}: not loaded in launchd at all"


def _launchctl_field(label, field):
    """One scalar out of `launchctl print`. Returns None when absent.

    Only top-level fields are read: the output nests per-endpoint dicts that
    repeat key names (`state` appears three times for a job with two sockets),
    so the first match at minimum indentation is the job's own value.
    """
    out = sh(f"launchctl print gui/{os.getuid()}/{label}")
    if not out:
        return None
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{field} =") and line.startswith("\t" + field):
            return stripped.split("=", 1)[1].strip()
    return None


def _inert_reason(label, pid):
    """Whether a live pid has done nothing since it started -- '' if it looks fine.

    This exists because a launchd job reported `state = running`, `runs = 1`,
    `last exit code = (never exited)` while its process had never opened its own
    entry script. The service was down for 50 minutes and every process-level
    signal said healthy (2026-09-01). A PID is proof a fork happened, nothing
    more.

    Three independent measures must ALL indicate nothing-has-happened before
    this fires, because each one alone has a legitimate explanation: a daemon
    can idle at zero CPU, a small tool can hold few descriptors, and anything
    can look quiet in its first seconds. Together they describe a process that
    started and then never did any work at all.

    Deliberately conservative -- a false RED here would land on a healthy
    daemon, and this monitor has produced those before. Reports the raw numbers
    rather than a verdict, so a human can disagree with the threshold.
    """
    ps = sh(f"/bin/ps -o etime=,time=,command= -p {pid}")
    if not ps or not ps.strip():
        return ""
    parts = ps.strip().split(None, 2)
    if len(parts) < 2:
        return ""
    alive_s = _etime_seconds(parts[0])
    cpu_s = _cputime_seconds(parts[1])
    if alive_s is None or cpu_s is None:
        return ""
    if alive_s < INERT_MIN_AGE_SEC or cpu_s >= INERT_MAX_CPU_SEC:
        return ""
    fds = sh(f"/usr/sbin/lsof -p {pid} 2>/dev/null | /usr/bin/wc -l")
    try:
        n_fds = int(fds.strip())
    except (ValueError, AttributeError):
        return ""          # cannot measure -> do not accuse
    if n_fds >= INERT_MAX_FDS:
        return ""
    return (f"alive {int(alive_s)}s, {cpu_s:.1f}s CPU, {n_fds} open files "
            f"-- started and did nothing")


def _etime_seconds(text):
    """ps etime: [[dd-]hh:]mm:ss."""
    try:
        days = 0
        if "-" in text:
            d, text = text.split("-", 1)
            days = int(d)
        bits = [int(x) for x in text.split(":")]
        while len(bits) < 3:
            bits.insert(0, 0)
        return days * 86400 + bits[0] * 3600 + bits[1] * 60 + bits[2]
    except (ValueError, IndexError):
        return None


def _cputime_seconds(text):
    """ps time: [mmm:]ss.hh cumulative CPU."""
    try:
        bits = text.split(":")
        return int(bits[0]) * 60 + float(bits[1]) if len(bits) == 2 else float(bits[0])
    except (ValueError, IndexError):
        return None


def check_launchd_ran(spec):
    """A SCHEDULED job must have actually fired at least once.

    Separate from check_launchd because the two job shapes fail in opposite
    directions. A daemon is unhealthy when it has no PID; a scheduled job has
    no PID almost all the time, and asserting one would be red between every
    run. What a scheduled job owes you is evidence it has ever executed.

    `runs = 0` on a loaded job is the shape nothing else catches. Every other
    check on a periodic job compares the freshness of what it writes -- and if
    it has never run, that output has never existed, so a staleness check
    cannot tell "never started" from "path is wrong" from "not installed yet".
    All three read as one ambiguous missing file. `runs` distinguishes them:
    the job is loaded, launchd agrees it should have fired, and it has not.

    Counters reset when a job is re-bootstrapped, so a low count right after a
    deploy is expected and only zero is treated as a failure.
    """
    label = spec["label"]
    runs = _launchctl_field(label, "runs")
    if runs is None:
        return False, f"{label}: not loaded in launchd at all"
    try:
        n = int(runs)
    except ValueError:
        return True, f"{label}: loaded (runs unreadable: {runs!r})"
    if n == 0:
        return False, (f"{label}: LOADED BUT HAS NEVER RUN -- launchd accepted "
                       f"the job and has not once executed it; "
                       f"{spec.get('why', 'nothing it produces has ever existed')}")
    last = _launchctl_field(label, "last exit code")
    return True, f"{label}: {n} run(s), last exit {last or 'unknown'}"


def reboot_recovery_report(red, green):
    """First run after a restart: what came back, and what did not.

    A volume-readability probe alone would have called this morning's outage
    green -- the disk was fine and the service was not serving. "Readable" and
    "serving" are different questions, and the one that actually hurt was the
    second. So on the first run after a boot-session change, compare every check
    that was green before the restart against what is red now: anything in both
    sets is something the reboot took away and nothing brought back.

    Returns None when no restart happened, which is almost every run.
    """
    was = prev_status()
    was_boot = was.get("tcc", {}).get("boot")
    now_boot = _TCC_RECORD.get("boot")
    if not was_boot or not now_boot or was_boot == now_boot:
        return None

    def names(details):
        return {d.split(":", 1)[0].strip() for d in details if ":" in d}

    was_ok, now_bad, now_ok = names(was.get("green", [])), names(red), names(green)
    lost = sorted(was_ok & now_bad)
    gone = sorted(was_ok - now_bad - now_ok)   # check no longer runs at all

    grant = ("secondary-volume access still readable"
             if _TCC_RECORD.get("readable") else
             "SECONDARY VOLUME NOT READABLE -- the FDA grant did not survive")

    if not lost and not gone:
        return (f"REBOOT DETECTED -- all {len(was_ok)} previously-green checks "
                f"came back. {grant}.")
    parts = [f"REBOOT DETECTED -- {len(lost) + len(gone)} of {len(was_ok)} "
             f"previously-green checks did NOT come back. {grant}."]
    if lost:
        parts.append("  did not recover: " + ", ".join(lost))
    if gone:
        parts.append("  no longer being checked: " + ", ".join(gone))
    return "\n".join(parts)


def check_tcc_grant(spec):
    """Does the Full Disk Access grant still hold, and did it survive the reboot?

    This exists because a grant lapsed silently on 2026-09-01 and took the
    review surface down for 50 minutes. The recovery was to move the service's
    binary to the boot disk and grant it FDA -- which fixes today, and leaves
    open the question the incident actually raised: whether a grant survives a
    restart, or whether the next reboot re-teaches us the same lesson.

    Nobody could answer that on the day, because it needs an observation
    spanning a reboot. So record the answer instead of reasoning about it: each
    run stores the boot session it observed and whether the volume was readable.
    When the boot session changes, the comparison against the stored one is the
    measurement, and it is made automatically the first time the machine comes
    back up.

    Reported plainly either way -- "survived reboot" is the result worth having,
    not just the failure.
    """
    name = spec["name"]
    boot = _sysctl("kern.boottime") or "unknown"
    resolved, reason = resolve_bun()
    readable = resolved is not None

    prior = prev_status().get("tcc", {})
    was_boot, was_readable = prior.get("boot"), prior.get("readable")
    _TCC_RECORD.update({"boot": boot, "readable": readable,
                        "binary": resolved or None})

    if was_boot and was_boot != boot:
        # The machine restarted between runs. This is the whole point.
        if was_readable and not readable:
            return False, (f"{name}: THE GRANT DID NOT SURVIVE THE REBOOT -- the "
                           f"volume was readable before the restart and is not "
                           f"now. Re-granting Full Disk Access will fix today "
                           f"and will lapse again the same way. Tried: {reason}")
        if was_readable and readable:
            return True, (f"{name}: grant SURVIVED a reboot (readable before and "
                          f"after) via {os.path.basename(resolved)}")
        if readable:
            return True, f"{name}: readable after reboot (was not, before)"
        return False, (f"{name}: still unreadable across a reboot -- a restart "
                       f"is not the fix. Tried: {reason}")

    if not readable:
        return False, f"{name}: secondary volume UNREADABLE here -- {reason}"
    return True, (f"{name}: readable via {os.path.basename(resolved)} "
                  f"(no reboot since last run)")


def check_port(spec):
    """Exactly one listener on the port, and it is the expected program.

    Two listeners is a RED, not a curiosity: a receiver bound 127.0.0.1:8787
    (IPv4) beside a server on *:8787 (IPv6) and BOTH succeeded, silently
    stealing traffic. Address-family collisions never raise an error anywhere.
    """
    port = spec["port"]
    out = sh(f"lsof -nP -iTCP:{port} -sTCP:LISTEN 2>/dev/null")
    rows = [l for l in out.splitlines()[1:] if l.strip()]
    if not rows:
        return False, f"port {port} ({spec['name']}): NOTHING LISTENING"
    if len(rows) > 1:
        who = "; ".join(" ".join(r.split()[:2]) + " " + r.split()[8] for r in rows)
        return False, f"port {port} ({spec['name']}): {len(rows)} LISTENERS COLLIDING -> {who}"
    return True, f"port {port} ({spec['name']}): 1 listener"


def check_http(spec):
    """An endpoint must answer AND contain the expected marker.

    A 200 alone is not health. Where a public hostname is given, this is the
    only check that proves the whole tunnel->daemon path, rather than proving a
    process exists on a port nobody routes to.
    """
    body = sh(f"curl -s -m {spec.get('timeout', 10)} {spec['url']!r}")
    if not body:
        return False, f"{spec['name']}: NO RESPONSE from {spec['url']}"
    expect = spec.get("expect")
    if expect and expect not in body:
        return False, (f"{spec['name']}: {spec['url']} answered but is missing "
                       f"{expect!r} -> {body[:120]}")
    return True, f"{spec['name']}: ok"


def _parse_log_times(tail_lines, mtime):
    """Timestamp each line, handling logs that stamp a TIME with no date.

    The dated case is easy. The undated one is not, and it silently defeated
    the window: a log writing `[broker 06:22:08]` never matched the date regex,
    so every matching line was treated as "undated, and the file was written
    recently, so it might be recent" -- which counts a warning from any hour of
    any day forever. That is the same bug the dated path already fixed, in the
    half nobody looked at, and it is what pins a check RED on a warning emitted
    once at a restart hours earlier.

    Anchoring: the last stamped line is assumed to be about as old as the
    file's mtime, which also calibrates whatever clock the log writes in (these
    daemons stamp UTC). Walking backwards, a time-of-day that is LATER than the
    line after it means the log crossed midnight, so the date steps back a day.

    Returns (timestamps, calibrated) -- `calibrated` is False when nothing
    could be stamped at all, and the caller falls back to its old behaviour.
    """
    dated = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})")
    parsed = []
    for line in tail_lines:
        m = dated.search(line)
        if not m:
            parsed.append(None)
            continue
        try:
            parsed.append(datetime.strptime(f"{m.group(1)} {m.group(2)}",
                                            "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            parsed.append(None)
    if any(parsed):
        return parsed, True

    # No dated line anywhere -- try time-only.
    timeonly = re.compile(r"\b(\d{2}):(\d{2}):(\d{2})\b")
    times = []
    for line in tail_lines:
        m = timeonly.search(line)
        if not m:
            times.append(None)
            continue
        h, mi, sec = (int(g) for g in m.groups())
        times.append(None if h > 23 or mi > 59 or sec > 59
                     else timedelta(hours=h, minutes=mi, seconds=sec))
    if not any(t is not None for t in times):
        return parsed, False

    anchor = datetime.fromtimestamp(mtime)
    last = next(t for t in reversed(times) if t is not None)
    # Same wall-clock day as mtime by construction; the difference between the
    # log's own last stamp and mtime's time-of-day is the clock offset.
    day = anchor - timedelta(hours=anchor.hour, minutes=anchor.minute,
                             seconds=anchor.second,
                             microseconds=anchor.microsecond)
    offset = anchor - (day + last)

    out = [None] * len(times)
    cur_day, prev = day, None
    for i in range(len(times) - 1, -1, -1):
        t = times[i]
        if t is None:
            continue
        if prev is not None and t > prev:
            cur_day -= timedelta(days=1)      # walked back past midnight
        out[i] = cur_day + t + offset
        prev = t
    return out, True


def check_log_errors(spec):
    """An error stream must be quiet in the recent window.

    This is the check that catches "alive and failing" -- the shape a process
    check can never see. Timestamps are matched loosely because every daemon
    formats them differently; if none parse, fall back to counting matches in
    the file's tail and require the file to have been written recently.
    """
    path = os.path.expanduser(spec["path"])
    if not os.path.exists(path):
        return False, f"{spec['name']}: log missing at {path}"

    # Silence is a failure mode, not a pass. A daemon whose logger thread dies,
    # or that hangs without erroring, writes nothing -- which scans identically
    # to a healthy quiet daemon. Opt-in per check, and set generously: the bound
    # is for "this has not written in half a day", not for a quiet night.
    mtime = os.path.getmtime(path)
    silent_min = (time.time() - mtime) / 60
    max_silence = spec.get("max_silence_minutes")
    if max_silence and silent_min > max_silence:
        return False, (f"{spec['name']}: log SILENT for {silent_min:.0f}m "
                       f"(limit {max_silence}m) -- wedged, not quiet")

    window = spec.get("window_minutes", 60)
    pattern = re.compile(spec.get("pattern", "error"), re.I)

    # Lines that match the error pattern but are a known, expected condition.
    #
    # Needed because the alternative is narrowing `pattern` until it only
    # matches today's known failures, which is how a check stops catching
    # anything new. An explicit ignore list keeps the pattern broad and states
    # in one place what is deliberately tolerated -- and every entry must say
    # WHY in the spec's comment, or it becomes a way to silence real faults.
    ignore = spec.get("ignore")
    ignore_re = re.compile(ignore, re.I) if ignore else None

    # Read enough lines to plausibly cover the window instead of a flat 400. On
    # a chatty log a real error scrolls out of a fixed tail before a scheduled
    # run ever sees it, and the check reports "quiet" on a log full of errors.
    n_lines = spec.get("lines") or min(20000, max(400, window))
    tail = sh(f"tail -n {n_lines} {path!r}")
    tail_lines = tail.splitlines()

    # Work out what clock the log writes in, instead of assuming local.
    #
    # These daemons stamp in UTC while datetime.now() is local, which put every
    # parsed timestamp seven hours in the FUTURE and made `ts >= cutoff` true
    # for every line in the tail. The window silently did nothing. Calibrating
    # against the file's own mtime handles either clock without hardcoding an
    # offset that a DST change would invalidate.
    parsed, calibrated = _parse_log_times(tail_lines, mtime)

    known = [t for t in parsed if t]
    skew = timedelta(0)
    if known:
        drift = datetime.fromtimestamp(mtime) - max(known)
        if abs(drift) > timedelta(minutes=5):
            skew = drift

    cutoff = datetime.now() - timedelta(minutes=window)
    hits, dated = 0, 0
    ignored = 0
    for line, ts in zip(tail_lines, parsed):
        if not pattern.search(line):
            continue
        if ignore_re and ignore_re.search(line):
            ignored += 1
            continue
        if ts:
            dated += 1
            if ts + skew >= cutoff:
                hits += 1
        elif silent_min <= window:
            # Undated line, and the file has been written inside the window, so
            # it could plausibly be recent. Counting these unconditionally is
            # what kept a check RED on errors that had stopped hours earlier --
            # the line never ages out until it scrolls past the tail.
            hits += 1

    # Did the tail actually reach back across the window? If the oldest dated
    # line is still inside it and we read every line we asked for, the log is
    # busier than the read and the count below is an undercount. Say so rather
    # than reporting a confident number derived from a partial read.
    truncated = (known and len(tail_lines) >= n_lines
                 and min(known) + skew > cutoff)

    limit = spec.get("max", 0)
    streak = bump_streak(spec["name"], hits > 0)
    max_streak = spec.get("max_error_streak")

    if hits > limit:
        sample = next((l for l in reversed(tail_lines) if pattern.search(l)), "")
        note = " [tail did not cover the window]" if truncated else ""
        return False, (f"{spec['name']}: {hits} error lines in last {window}m "
                       f"(limit {limit}){note} -> {sample[:160]}")

    # Under the per-run limit, but erroring on every run for hours. That is a
    # sustained failure that the threshold alone was built to miss.
    if max_streak and streak >= max_streak:
        sample = next((l for l in reversed(tail_lines) if pattern.search(l)), "")
        return False, (f"{spec['name']}: erroring on {streak} consecutive runs "
                       f"(under the {limit}/run limit each time, which is how "
                       f"a slow drip hides) -> {sample[:160]}")

    # Say how much was ignored. A tolerated line is still a line, and an
    # ignore rule that quietly swallows a flood is indistinguishable from a
    # check that stopped working.
    note = f" ({ignored} ignored)" if ignored else ""
    if truncated:
        return True, (f"{spec['name']}: quiet{note}, but the {n_lines}-line "
                      f"tail did not reach back {window}m -- raise 'lines'")
    return True, f"{spec['name']}: quiet{note}"


_TRANSCRIPT_AGE_JS = r"""
const fs = require("fs"), path = require("path");
// `bun -e` argv is [bunPath, ...args] -- slice(1), not slice(2).
const dir = process.argv.slice(1)[0];
let newest = 0;
try {
  for (const f of fs.readdirSync(dir)) {
    if (!f.endsWith(".jsonl")) continue;
    const m = fs.statSync(path.join(dir, f)).mtimeMs;
    if (m > newest) newest = m;
  }
} catch (e) {}
console.log(String(newest));
"""


def transcript_age_hours(cwd):
    """Hours since this session's newest transcript was written, or None.

    The transcript is the only record of what a session actually PROCESSED --
    the project's own killer item is that a pane is a render and cannot show
    what a session received. A PID cannot either.

    Routed through bun because ~/.claude is a symlink onto /Volumes/Data, where
    an Apple-signed interpreter under launchd is denied even a stat. A plain
    os.path.getmtime here would fail in production while passing every test run
    by hand from a terminal.
    """
    if not bun_works():
        return None
    encoded = re.sub(r"[/_.]", "-", cwd)
    d = os.path.expanduser(f"~/.claude/projects/{encoded}")
    try:
        r = subprocess.run([bun_path(), "-e", _TRANSCRIPT_AGE_JS, "--", d],
                           capture_output=True, text=True, cwd="/", timeout=BUN_TIMEOUT)
        ms = float(r.stdout.strip() or 0)
    except Exception:
        return None
    if not ms:
        return None
    return (time.time() - ms / 1000) / 3600


def check_session(spec):
    """A Claude session must be running AND have processed something recently.

    The PID half is not sufficient and never was: a session wedged on a
    permission dialog, or crash-looping, keeps a process with the right cwd and
    reads green indefinitely.

    The honest limit of the freshness half: an idle session and a wedged session
    both have a quiet transcript, and idle is the correct state for most peers.
    So the age is reported always and only fails past a deliberately long bound,
    which catches "has not processed anything in over a day" rather than "is not
    answering right now". Anything tighter would go red on a peer that is
    working exactly as intended, and a check that cries wolf on healthy state is
    worse than no check.
    """
    want = spec["cwd"]
    for pid, _argv in claude_sessions():
        if session_cwd(pid) != want:
            continue
        age = transcript_age_hours(want)
        if age is None:
            return True, f"{spec['name']}: pid {pid} (transcript age unknown)"
        limit = spec.get("max_idle_hours")
        if limit and age > limit:
            return False, (f"{spec['name']}: pid {pid} alive but has processed "
                           f"nothing for {age:.1f}h (limit {limit}h) -- "
                           f"running is not the same as working")
        return True, f"{spec['name']}: pid {pid}, last turn {age:.1f}h ago"
    return False, f"{spec['name']}: NO SESSION at {want}"


def check_state_fresh(spec):
    """A watcher's state file must have been written recently.

    For monitors that run as a tmux loop rather than a launchd job: there is no
    daemon to ask, and the tmux session existing proves only that a shell is
    alive, not that the loop inside it is still iterating. The state file's
    mtime is the one signal that means "a run actually completed".

    A dead budget watcher is worse than no budget watcher -- it reads as
    "nothing is wrong" forever, which is precisely the failure it was built to
    end. So its liveness is checked here rather than trusted.
    """
    path = os.path.expanduser(spec["path"])
    limit = spec.get("max_age_minutes", 60)
    if not os.path.exists(path):
        return False, f"{spec['name']}: MISSING {path} -- watcher has never run"
    age = (time.time() - os.path.getmtime(path)) / 60
    if age > limit:
        return False, (f"{spec['name']}: STALE, last run {age:.0f}m ago "
                       f"(limit {limit}m) -- the loop is not iterating")
    return True, f"{spec['name']}: fresh ({age:.0f}m)"


def _latest_trend_entry(text):
    """Newest dated entry in the trend log, as a naive local datetime.

    Entries look like ``2026-08-29 18:41 PT - ...`` in backticks, sometimes with
    a ``~`` before the time when the minute was approximate. Returns None when
    the log has no dated entry at all.
    """
    # Accept the backticked form, the bullet/bold form, AND the bare one-line
    # form the token-watch skill itself prescribes (`date time PT · pool · ...`).
    # The first two have both been
    # written by hand for months, and the parser only ever read the first --
    # so on 2026-09-07 two live readings sat in the file while this check
    # reported "no reading has been recorded". A staleness check that a format
    # slip can blind is worse than no check: it reports the loop dead when the
    # loop ran, and the operator goes looking for a cron that is fine.
    pat = re.compile(
        r"^(?:`|- \*\*)?(\d{4}-\d{2}-\d{2})\s+~?(\d{1,2}):(\d{2})\s*PT",
        re.M)
    best = None
    for m in pat.finditer(text):
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d").replace(
                hour=int(m.group(2)), minute=int(m.group(3)))
        except ValueError:
            continue
        if best is None or d > best:
            best = d
    return best


_READ_TEXT_JS = r"""
const fs = require("fs");
// `bun -e` argv is [bunPath, ...args] -- slice(1), not slice(2).
try {
  process.stdout.write(fs.readFileSync(process.argv.slice(1)[0], "utf8"));
} catch (e) { process.exit(3); }
"""


def read_text_via_bun(path):
    """Read a file that may live on the secondary volume. (text, err).

    Plain `open()` inside the launchd-exec'd checker gets `Operation not
    permitted` on /Volumes -- the checker's own binary has no Full Disk Access,
    and that is not fixable from here. Every other check that touches the repo
    goes through the resolved bun for this reason; a check that reads directly
    is not "simpler", it is a check that can only ever report the volume.

    Learned by shipping it wrong: check_trend_log used io.open and went RED with
    `Operation not permitted` on its first launchd run, which reads as "the
    quota meter is broken" and is really "the monitor cannot see". Same family
    as the peer-bravo job that failed every run for three months.
    """
    if bun_works():
        try:
            r = subprocess.run([bun_path(), "-e", _READ_TEXT_JS, "--", path],
                               capture_output=True, text=True, cwd="/",
                               timeout=BUN_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"bun read failed ({type(exc).__name__})"
        if r.returncode == 0:
            return r.stdout, None
        return None, f"bun could not read it (exit {r.returncode})"
    try:
        with io.open(path, encoding="utf-8") as fh:
            return fh.read(), None
    except OSError as exc:
        return None, f"{exc} -- {BUN_UNAVAILABLE}"


_RATE_LIMIT_JS = r"""
const fs = require("fs"), path = require("path");
// `bun -e` argv is [bunPath, ...args] -- slice(1), not slice(2).
const [root, sinceMs] = process.argv.slice(1);
const since = Number(sinceMs);
const hits = [];
function walk(dir, depth) {
  if (depth > 4) return;
  let ents = [];
  try { ents = fs.readdirSync(dir, {withFileTypes: true}); } catch (e) { return; }
  for (const e of ents) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) { walk(full, depth + 1); continue; }
    if (!e.name.endsWith(".jsonl")) continue;
    let st; try { st = fs.statSync(full); } catch (e) { continue; }
    if (st.mtimeMs < since) continue;          // cheap skip
    let text; try { text = fs.readFileSync(full, "utf8"); } catch (e) { continue; }
    if (!text.includes('"quotaLimits"')) continue;
    for (const line of text.split("\n")) {
      if (!line.includes('"rateLimitType":"five_hour"')) continue;
      let o; try { o = JSON.parse(line); } catch (e) { continue; }
      const q = (o.message && o.message.quotaLimits) || o.quotaLimits;
      if (!q || q.status !== "rejected") continue;
      const t = Date.parse(o.timestamp || "");
      if (!t || t < since) continue;
      hits.push({t, project: dir.split("/projects/")[1] || dir, resetsAt: q.resetsAt});
    }
  }
}
walk(root, 0);
console.log(JSON.stringify(hits));
"""


def check_rate_limit_hits(spec):
    """Did the fleet actually get rate-limited? Not a proxy -- the record.

    Claude Code writes every 5-hour session-limit rejection into the transcript
    as `quotaLimits{"rateLimitType":"five_hour","status":"rejected"}`. That is
    ground truth: the moment work stopped, on disk, needing no calibration and
    no `/usage` pull.

    Nothing read it. Six episodes between 2026-08-29 and 2026-09-01 -- five of
    them after Monday, one at 13:31 on 2026-09-01 that blocked two projects for
    over two hours -- and every one of them was discovered by Bryan noticing the
    fleet had stopped. Meanwhile the weekly meter was 52h stale and the 5h
    budget watch was reporting a share, so both instruments were green through
    all six.

    This is deliberately a LAGGING check. It cannot prevent the episode it
    reports; `fleet_budget_watch.py`'s token thresholds are the leading
    indicator, and they were calibrated off exactly these events. The value here
    is that "we ran out and nobody said so" stops being possible.
    """
    name = spec["name"]
    hours = spec.get("window_hours", 24)
    since_ms = int((time.time() - hours * 3600) * 1000)
    root = os.path.expanduser(spec.get("root", "~/.claude/projects"))

    if not bun_works():
        return False, f"{name}: cannot scan transcripts -- {BUN_UNAVAILABLE}"
    try:
        r = subprocess.run([bun_path(), "-e", _RATE_LIMIT_JS, "--",
                            root, str(since_ms)],
                           capture_output=True, text=True, cwd="/",
                           timeout=max(BUN_TIMEOUT, 60))
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{name}: scan failed ({type(exc).__name__})"
    if r.returncode != 0:
        return False, f"{name}: scan exited {r.returncode}"
    try:
        hits = json.loads(r.stdout or "[]")
    except ValueError:
        return False, f"{name}: unreadable scan output"

    if not hits:
        return True, f"{name}: no session-limit rejections in {hours}h"

    # Group into episodes: rejections retry in bursts, so raw counts overstate.
    ts = sorted(h["t"] for h in hits)
    episodes = [ts[0]]
    for t in ts[1:]:
        if t - episodes[-1] > 30 * 60 * 1000:
            episodes.append(t)
    last = datetime.fromtimestamp(ts[-1] / 1000)
    projects = sorted({(h.get("project") or "?").split("-dev-")[-1]
                       for h in hits})
    return False, (f"{name}: {len(episodes)} session-limit episode(s) in {hours}h, "
                   f"latest {last:%m-%d %H:%M} -- the fleet was BLOCKED. "
                   f"Hit in: {', '.join(projects[:4])}")


def check_trend_log(spec):
    """The quota meter must have been READ recently, not merely scheduled.

    The token-watch runs as a session-scoped cron, which dies on every respawn.
    Its re-arm is a SessionStart directive -- a prompt asking the agent to arm
    it -- so a single missed read-through leaves the fleet with no quota
    instrument at all, silently and indefinitely. That is what happened between
    2026-08-30 and 2026-09-01: the last entry said "baseline broken, the next
    read establishes a new one" and no next read ever came.

    mtime is NOT the signal here. The trend log lives inside a doc that is
    edited for unrelated reasons, so its mtime says the file was touched, not
    that a reading was taken. Parse the newest entry's own timestamp instead.
    """
    path = os.path.expanduser(spec["path"])
    limit_h = spec.get("max_age_hours", 8)
    if not exists_via_bun(path):
        return False, f"{spec['name']}: MISSING {path}"
    text, err = read_text_via_bun(path)
    if text is None:
        # Distinct wording on purpose: this RED means the MONITOR is blind, not
        # that the meter went unread. Collapsing the two sends whoever reads it
        # to re-arm a cron that was never the problem.
        return False, (f"{spec['name']}: CANNOT BE READ FROM HERE, so this "
                       f"check cannot say whether the meter is current -- {err}")
    latest = _latest_trend_entry(text)
    if latest is None:
        return False, f"{spec['name']}: no dated entry -- the meter has never been read"
    age_h = (datetime.now() - latest).total_seconds() / 3600
    if age_h > limit_h:
        # Do NOT name a cause here. This check reads a file; it has no way to
        # see the scheduler, and the cause it used to assert -- "token-watch
        # cron is probably unarmed" -- was measurably WRONG on 2026-09-01: the
        # job was armed (CronList confirmed it) and had simply not fired in
        # seven days, because a session-scoped cron only fires while the REPL
        # is idle and this session almost never is. A confident wrong cause is
        # worse than no cause: it sends the reader to re-arm a live job and
        # then to report the RED as fixed.
        return False, (f"{spec['name']}: STALE, last reading "
                       f"{latest:%Y-%m-%d %H:%M} ({age_h:.0f}h ago, limit "
                       f"{limit_h}h) -- no reading has been recorded. Check "
                       f"whether the token-watch job is armed AND whether it "
                       f"has actually fired; those are different failures.")
    return True, f"{spec['name']}: read {age_h:.1f}h ago"


def check_monitor_loops(spec):
    """Every tmux monitor loop the guard watches must be up.

    Why this is not covered by the per-loop state checks: only one of the loops
    writes a state file. `fleet-monitor` runs a report every two hours and
    leaves nothing behind, so an age check has no artifact to read and its
    death is invisible to every other instrument here. Both loops died in the
    2026-08-31 reboot and stayed down ~70 minutes; a freshness check on the
    budget watcher alone would have caught half of that.

    The guard already records both loops' status every two minutes. Reading its
    state file covers whichever loops exist without needing each one to grow a
    heartbeat, and it stays correct when a loop is added.

    The guard's own liveness is asserted separately (`launchd_ran` on
    com.fryanpan.fleet-guard) -- but a state file written once and then
    abandoned would still read healthy here, so its age is checked too. A
    monitor that reports on another monitor has to prove it is itself awake.
    """
    path = os.path.expanduser(spec["path"])
    limit = spec.get("max_age_minutes", 15)
    if not os.path.exists(path):
        return False, (f"{spec['name']}: MISSING {path} -- the guard has never "
                       f"written state, so nothing is watching the loops")
    age = (time.time() - os.path.getmtime(path)) / 60
    if age > limit:
        return False, (f"{spec['name']}: guard state STALE, {age:.0f}m old "
                       f"(limit {limit}m) -- loop status below is not current")
    try:
        with open(path) as f:
            loops = json.load(f).get("loops", {})
    except Exception as exc:
        return False, f"{spec['name']}: guard state unreadable ({exc})"
    if not loops:
        return False, f"{spec['name']}: guard state names no loops at all"

    bad = {n: st for n, st in loops.items() if st not in ("up", "revived")}
    if bad:
        detail = ", ".join(f"{n} {st}" for n, st in sorted(bad.items()))
        return False, (f"{spec['name']}: {detail} -- "
                       f"{spec.get('why', 'a monitor loop is not running')}")
    revived = sorted(n for n, st in loops.items() if st == "revived")
    if revived:
        return True, (f"{spec['name']}: {len(loops)} up "
                      f"({', '.join(revived)} was restarted by the guard)")
    return True, f"{spec['name']}: {len(loops)} up"


def check_channel_flags(spec):
    """Every running session's launch line must carry the required channel flags.

    Enabling a plugin gives a session the TOOLS; only a flag on the launch line
    gives it the inbound WAKE. Three channel plugins sat enabled for weeks with
    no flag, so no event could reach anybody -- and from inside a session that
    looks completely normal. This check reads argv, which is where the truth is.
    """
    # A required entry may be a list, meaning "any one of these spellings".
    # That is what carries a plugin rename: during the rollout the old and new
    # install keys are both live -- a session emits the new one only after it
    # restarts onto the new bundle -- so demanding a single exact string would
    # mark every migrated session as missing its wake, fleet-wide, and the red
    # would be loudest exactly when the rollout was working.
    required = [r if isinstance(r, list) else [r] for r in spec["required"]]
    missing = []
    for pid, argv in claude_sessions():
        absent = [alts[0] for alts in required
                  if not any(f in argv for f in alts)]
        if absent:
            cwd = session_cwd(pid) or "?"
            missing.append(f"{os.path.basename(cwd)}({pid}) missing {','.join(absent)}")
    if missing:
        return False, f"{len(missing)} sessions w/o wake: {'; '.join(missing[:3])}"
    return True, f"channel flags: all {len(claude_sessions())} sessions ok"


def check_file_present(spec):
    """A required config/credential FILE must exist (never its contents).

    Catches the inert-but-running shape: a broker listening happily while its
    token file does not exist, so it never polls anything. Presence only -- this
    script must never read a secret value, and `test -e` never opens the file.

    Tested via bun rather than os.path.exists because `~/.config` is a symlink
    onto the secondary volume, where a launchd-invoked Apple interpreter is
    denied even a stat -- and /usr/bin/test is Apple-signed, so it inherits the
    same gate. Left as os.path.exists this would have reported MISSING forever,
    including after the file was created: exactly the permanently-red check this
    monitor exists to avoid.
    """
    path = os.path.expanduser(spec["path"])
    if not exists_via_bun(path):
        return False, f"{spec['name']}: MISSING {path} -- {spec.get('why', '')}"
    return True, f"{spec['name']}: present"


def check_token_resolvable(spec):
    """A credential must be RESOLVABLE the way its consumer resolves it.

    The predecessor of this check asserted that a token FILE existed, which was
    correct only while the file was the sole source. Once the consumer gained a
    keyring fallback, a green file check stopped proving the consumer could
    authenticate, and a red one stopped meaning it could not -- the check was
    measuring a thing that no longer determined the outcome.

    So this mirrors the consumer's own resolution order rather than any one
    source: environment first, then each candidate command. It reports WHICH
    source answered, because "works, via the keyring" and "works, via the file"
    fail in completely different ways later.

    Never reads or prints a value. A command's output is measured for length
    and then dropped; only the source name and a pass/fail leave this function.
    """
    name = spec["name"]
    env_var = spec.get("env")
    if env_var and os.environ.get(env_var, "").strip():
        return True, f"{name}: resolvable from ${env_var}"

    path = spec.get("path")
    if path and exists_via_bun(os.path.expanduser(path)):
        return True, f"{name}: resolvable from {path}"

    for cmd in spec.get("commands", []):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=BUN_TIMEOUT, cwd="/")
        except Exception:
            continue
        if r.returncode == 0 and len(r.stdout.strip()) > 0:
            return True, f"{name}: resolvable from {spec.get('commands_label', cmd[0])}"

    tried = []
    if env_var:
        tried.append(f"${env_var}")
    if path:
        tried.append(path)
    if spec.get("commands"):
        tried.append(spec.get("commands_label", "command fallback"))
    return False, (f"{name}: NO SOURCE ANSWERED (tried {', '.join(tried)}) "
                   f"-- {spec.get('why', '')}")


def _sysctl(name):
    """One kernel read. sysctl is not subject to the secondary-volume gate that
    blocks an Apple-signed interpreter from touching /Volumes/Data -- it reads
    kernel state, not a file, so it works identically under launchd."""
    out = sh(f"/usr/sbin/sysctl -n {name}")
    return out.strip() if out else ""


def check_free_memory(spec):
    """Free-memory percentage, the number the OS itself acts on.

    kern.memorystatus_level is what /usr/bin/memory_pressure prints as
    "System-wide memory free percentage" -- the same value the kernel uses to
    decide when to start killing processes. This asserts an end state: no
    process is named, nothing is assumed about who is using the memory, and it
    goes red when the machine is actually short rather than when some particular
    program is large.
    """
    raw = _sysctl("kern.memorystatus_level")
    if not raw.isdigit():
        return False, f"{spec['name']}: PROBE-FAILED (kern.memorystatus_level -> {raw!r})"
    free = int(raw)
    floor = spec.get("min_free_pct", 15)
    if free < floor:
        return False, f"{spec['name']}: {free}% free (floor {floor}%)"
    return True, f"{spec['name']}: {free}% free"


def _vm_counters():
    """Cumulative swapin/swapout page counts from vm_stat, or None."""
    try:
        raw = subprocess.run(["vm_stat"], capture_output=True, text=True,
                             timeout=10).stdout
    except Exception:
        return None
    out = {}
    for key in ("Swapins", "Swapouts"):
        m = re.search(key + r":\s*(\d+)", raw)
        if not m:
            return None
        out[key] = int(m.group(1))
    m = re.search(r"page size of (\d+) bytes", raw)
    out["page"] = int(m.group(1)) if m else 4096
    return out


def check_swap(spec):
    """Swap ACTIVITY, not the level. The level is not a signal.

    The old check went RED above a flat 8GB of swap in use. Two things are
    wrong with that. macOS does not give swap back -- once a page is written
    out the allocation stays counted long after the pressure is gone, so the
    number ratchets and never returns. And the level is mostly a function of
    how many sessions are up, which the lean-fleet policy already governs:
    measured over 1,887 samples across 2026-09-01..03, median swap in use was
    1.3GB at 5 sessions, 5.8GB at 10, 8.0GB at 11 and 10.5GB at 12. At the
    fleet's normal size the median SAT ON the ceiling, so this check was on
    its way to being furniture like the four reds before it.

    What actually hurts is pages being pushed out under pressure, right now.
    Sample the swapout counter over a short interval and alert on the rate.
    The level still gets printed, as context rather than as a verdict, and the
    old message asserted a cause ("what 'feels slow' is") that the check never
    observed -- same error as 87a0295.
    """
    raw = _sysctl("vm.swapusage")
    m = re.search(r"used\s*=\s*([\d.]+)M", raw)
    if not m:
        return False, f"{spec['name']}: PROBE-FAILED (vm.swapusage -> {raw[:80]!r})"
    used_gb = float(m.group(1)) / 1024.0

    window = spec.get("sample_seconds", 15)
    a = _vm_counters()
    if a is None:
        return False, f"{spec['name']}: PROBE-FAILED (vm_stat unreadable)"
    time.sleep(window)
    b = _vm_counters()
    if b is None:
        return False, f"{spec['name']}: PROBE-FAILED (vm_stat unreadable on resample)"

    out_mb_s = (b["Swapouts"] - a["Swapouts"]) * b["page"] / window / 1e6
    in_mb_s = (b["Swapins"] - a["Swapins"]) * b["page"] / window / 1e6
    ceiling = spec.get("max_swapout_mb_s", 5.0)
    detail = (f"{out_mb_s:.1f}MB/s out, {in_mb_s:.1f}MB/s in, "
              f"{used_gb:.1f}GB in use")
    if out_mb_s > ceiling:
        return False, (f"{spec['name']}: writing {out_mb_s:.1f}MB/s to swap "
                       f"(ceiling {ceiling}MB/s) -- {detail}")
    return True, f"{spec['name']}: {detail}"


def check_load(spec):
    """1-minute load average per core. Sustained >1.0/core means work is
    queueing for CPU rather than running."""
    raw = _sysctl("vm.loadavg")
    m = re.search(r"\{\s*([\d.]+)", raw)
    ncpu = _sysctl("hw.ncpu")
    if not m or not ncpu.isdigit():
        return False, f"{spec['name']}: PROBE-FAILED (vm.loadavg -> {raw[:60]!r})"
    per_core = float(m.group(1)) / int(ncpu)
    ceiling = spec.get("max_per_core", 1.5)
    if per_core > ceiling:
        return False, (f"{spec['name']}: {per_core:.2f} per core over {ncpu} cores "
                       f"(ceiling {ceiling})")
    return True, f"{spec['name']}: {per_core:.2f} per core"


CHECKS = {
    "launchd": check_launchd,
    "launchd_ran": check_launchd_ran,
    "tcc_grant": check_tcc_grant,
    "port": check_port,
    "http": check_http,
    "log_errors": check_log_errors,
    "session": check_session,
    "channel_flags": check_channel_flags,
    "state_fresh": check_state_fresh,
    "monitor_loops": check_monitor_loops,
    "trend_log": check_trend_log,
    "rate_limit_hits": check_rate_limit_hits,
    "file_present": check_file_present,
    "token_resolvable": check_token_resolvable,
    "plugin_version": check_plugin_version,
    "archive_backlog": check_archive_backlog,
    "self_version": check_self_version,
    "free_memory": check_free_memory,
    "swap": check_swap,
    "load": check_load,
}


def notify(title, message):
    """Native macOS notification. No agent, no tokens.

    Reports its own failure loudly. A monitor whose alert path is silently
    broken is the exact shape it exists to catch -- it would run green-looking
    forever while nothing ever reached a human. The RED lines are always on
    stdout and in the status file regardless, so a dead notifier degrades to
    "you have to look" rather than to nothing.
    """
    # Pass text as argv, never interpolated into the script source. Failure
    # detail is raw daemon output -- the first RED it ever fired on contained
    # JSON with escaped quotes, which broke the AppleScript parse outright.
    r = subprocess.run(
        ["osascript",
         "-e", "on run argv",
         "-e", 'display notification (item 1 of argv) with title '
               '(item 2 of argv) sound name "Basso"',
         "-e", "end run",
         message[:240], title[:80]],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  !! NOTIFY FAILED (rc={r.returncode}): {r.stderr.strip()[:200]}")
        return False
    return True


def main():
    quiet = "--quiet" in sys.argv
    if not os.path.exists(CONFIG):
        print(f"no config at {CONFIG} -- run install-healthcheck.sh", file=sys.stderr)
        return 2

    with open(CONFIG) as f:
        cfg = json.load(f)

    red, green = [], []
    for spec in cfg.get("checks", []):
        fn = CHECKS.get(spec.get("type"))
        if not fn:
            red.append(f"unknown check type {spec.get('type')!r}")
            continue
        if spec.get("skip"):
            continue
        try:
            ok, detail = fn(spec)
        except Exception as e:                      # a broken check is a RED,
            tb = traceback.extract_tb(sys.exc_info()[2])
            where = f"{tb[-1].name}:{tb[-1].lineno}" if tb else "?"
            ok, detail = False, (f"{spec.get('name', spec['type'])}: "
                                 f"check raised {e!r} at {where}")
        (green if ok else red).append(detail)       # never a silent pass

    stamp = datetime.now().isoformat(timespec="seconds")
    recovery = reboot_recovery_report(red, green)
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATUS, "w") as f:
        json.dump({"checked_at": stamp, "red": red, "green": green,
                   "streaks": _STREAKS, "tcc": _TCC_RECORD,
                   "reboot_recovery": recovery}, f, indent=2)

    # Printed before everything else, and notified on its own: this line is the
    # answer to a question nobody will be awake to ask.
    if recovery:
        print(recovery)
        if not quiet:
            notify("Fleet: first run after reboot", recovery.splitlines()[0])

    if red:
        header = f"{len(red)} RED / {len(green)} ok"
        print(f"[{stamp}] {header}")
        for r in red:
            print(f"  RED  {r}")
        if "--verbose" in sys.argv:      # what still passes matters most on a
            for g in green:              # red run -- it scopes the blast radius
                print(f"  ok   {g}")
        if not quiet:
            notify(f"Fleet health: {header}", " | ".join(red[:3]))
        return 1

    print(f"[{stamp}] all green ({len(green)} checks)")
    if "--verbose" in sys.argv:
        for g in green:
            print(f"  ok   {g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
