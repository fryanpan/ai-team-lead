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


BUN = os.path.expanduser("~/.bun/bin/bun")


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
    if not os.path.exists(BUN):
        return os.path.exists(path)
    r = subprocess.run(
        [BUN, "-e",
         # `bun -e` argv is [bunPath, ...args] -- the path is argv[1], not [2].
         'process.exit(require("fs").existsSync(process.argv[1]) ? 0 : 1)',
         "--", path],
        capture_output=True, cwd="/", timeout=30)
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
    if not os.path.exists(BUN):
        return None, None
    r = subprocess.run([BUN, "-e", _PLUGIN_PROBE_JS, "--",
                        cache_dir, source_manifest],
                       capture_output=True, text=True, cwd="/", timeout=30)
    if r.returncode != 0:
        return None, None
    try:
        data = json.loads(r.stdout)
    except Exception:
        return None, None
    return data.get("live"), data.get("source")


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

def check_launchd(spec):
    """A LaunchAgent must have a live PID and a zero last-exit.

    `launchctl list` prints "PID  LAST_EXIT  LABEL"; a '-' PID means not
    running. Note the last-exit column is HISTORICAL -- a live process can show
    a nonzero code from a previous run, which is why the PID is checked first
    and reported separately. Reading only the exit column is how a daemon that
    had been up for days got reported as dead.
    """
    label = spec["label"]
    for line in sh("launchctl list").splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[2] == label:
            pid, last_exit = parts[0], parts[1]
            if pid == "-":
                return False, f"{label}: NOT RUNNING (last exit {last_exit})"
            return True, f"{label}: pid {pid}"
    return False, f"{label}: not loaded in launchd at all"


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
    window = spec.get("window_minutes", 60)
    cutoff = datetime.now() - timedelta(minutes=window)
    pattern = re.compile(spec.get("pattern", "error"), re.I)
    tail = sh(f"tail -n {spec.get('lines', 400)} {path!r}")

    hits, dated = 0, 0
    for line in tail.splitlines():
        if not pattern.search(line):
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})", line)
        if m:
            dated += 1
            try:
                ts = datetime.strptime(f"{m.group(1)} {m.group(2)}",
                                       "%Y-%m-%d %H:%M:%S")
                if ts >= cutoff:
                    hits += 1
            except ValueError:
                pass
        else:
            hits += 1  # undated match in the tail: count it

    limit = spec.get("max", 0)
    if hits > limit:
        sample = next((l for l in reversed(tail.splitlines())
                       if pattern.search(l)), "")
        return False, (f"{spec['name']}: {hits} error lines in last {window}m "
                       f"(limit {limit}) -> {sample[:160]}")
    return True, f"{spec['name']}: quiet"


def check_session(spec):
    """A Claude session must be running with the expected working directory."""
    want = spec["cwd"]
    for pid, _argv in claude_sessions():
        if session_cwd(pid) == want:
            return True, f"{spec['name']}: pid {pid}"
    return False, f"{spec['name']}: NO SESSION at {want}"


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
    "port": check_port,
    "http": check_http,
    "log_errors": check_log_errors,
    "session": check_session,
    "channel_flags": check_channel_flags,
    "file_present": check_file_present,
    "plugin_version": check_plugin_version,
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
        json.dump({"checked_at": stamp, "red": red, "green": green}, f, indent=2)

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
