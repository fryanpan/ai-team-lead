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
from datetime import datetime, timedelta

STATE_DIR = os.path.expanduser("~/Library/Application Support/team-lead")
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
    required = spec["required"]
    missing = []
    for pid, argv in claude_sessions():
        absent = [f for f in required if f not in argv]
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
    script must never read a secret value.
    """
    path = os.path.expanduser(spec["path"])
    if not os.path.exists(path):
        return False, f"{spec['name']}: MISSING {path} -- {spec.get('why', '')}"
    return True, f"{spec['name']}: present"


CHECKS = {
    "launchd": check_launchd,
    "port": check_port,
    "http": check_http,
    "log_errors": check_log_errors,
    "session": check_session,
    "channel_flags": check_channel_flags,
    "file_present": check_file_present,
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
            ok, detail = False, f"{spec.get('name', spec['type'])}: check raised {e!r}"
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
