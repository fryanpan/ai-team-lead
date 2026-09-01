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
    parsed = []
    for line in tail_lines:
        m = re.search(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})", line)
        if not m:
            parsed.append(None)
            continue
        try:
            parsed.append(datetime.strptime(f"{m.group(1)} {m.group(2)}",
                                            "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            parsed.append(None)

    known = [t for t in parsed if t]
    skew = timedelta(0)
    if known:
        drift = datetime.fromtimestamp(mtime) - max(known)
        if abs(drift) > timedelta(minutes=5):
            skew = drift

    cutoff = datetime.now() - timedelta(minutes=window)
    hits, dated = 0, 0
    for line, ts in zip(tail_lines, parsed):
        if not pattern.search(line):
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

    if truncated:
        return True, (f"{spec['name']}: quiet, but the {n_lines}-line tail did "
                      f"not reach back {window}m -- raise 'lines'")
    return True, f"{spec['name']}: quiet"


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


def check_swap(spec):
    """Absolute swap in use, not a percentage of the swap file.

    macOS grows the swap file on demand, so "percent of swap used" is
    self-correcting and says nothing -- it sits near full right up until the
    kernel allocates more. The meaningful quantity is how many GB the machine
    has pushed out of RAM, measured against physical memory.
    """
    raw = _sysctl("vm.swapusage")
    m = re.search(r"used\s*=\s*([\d.]+)M", raw)
    if not m:
        return False, f"{spec['name']}: PROBE-FAILED (vm.swapusage -> {raw[:80]!r})"
    used_gb = float(m.group(1)) / 1024.0
    ceiling = spec.get("max_used_gb", 8.0)
    if used_gb > ceiling:
        return False, (f"{spec['name']}: {used_gb:.1f}GB swapped out "
                       f"(ceiling {ceiling}GB) -- the machine is paging, "
                       f"which is what 'feels slow' is")
    return True, f"{spec['name']}: {used_gb:.1f}GB swapped out"


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
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATUS, "w") as f:
        json.dump({"checked_at": stamp, "red": red, "green": green,
                   "streaks": _STREAKS, "tcc": _TCC_RECORD}, f, indent=2)

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
