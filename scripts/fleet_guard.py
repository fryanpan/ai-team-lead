#!/usr/bin/env python3
"""Minutes-scale machine-pressure guard. Runs under launchd; costs no tokens.

Why this exists separately from fleet_healthcheck.py
----------------------------------------------------
The healthcheck already carries swap / free-memory / load checks (added
2026-08-18) -- coverage was never the gap. It runs three times a day, and on
2026-08-31 the machine went from healthy to a hard freeze in about two hours
between two scheduled runs. Every one of those three checks would have been RED
and not one of them executed. An instrument sampled slower than the failure it
watches cannot see the failure.

So: same metrics, 2-minute cadence, and an EARLIER threshold than the
healthcheck's. A warning at 95% swap is useless -- by then the machine is
already unresponsive and the notification will not paint.

Boot-disk discipline
--------------------
Everything here reads sysctl, ps, or the tmux socket in /private/tmp. Nothing
touches /Volumes/Data, because a launchd-invoked Apple-signed binary is denied
every operation on that volume (see docs/process/fleet-ops.md). That constraint
is the reason this file is deliberately dumb: no transcripts, no registry, no
repo. The moment it needs any of those it stops working under launchd and starts
lying about it.

Notify on the CROSSING, not on the state
----------------------------------------
Firing every two minutes while a condition persists trains you to ignore it,
which is how the email watcher became furniture. This notifies when the band
gets worse, once when it recovers, and at most every 30 minutes while critical.
"""

import json
import os
import re
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
PREFERRED_ROOT = "/opt/fleet"
FALLBACK_ROOT = os.path.join(HOME, "Library", "Application Support", "team-lead")
STATE_DIR = PREFERRED_ROOT if os.path.isdir(PREFERRED_ROOT) else FALLBACK_ROOT
STATE = os.path.join(STATE_DIR, "guard-state.json")
HEALTHCHECK_STATUS = os.path.join(STATE_DIR, "healthcheck-status.json")

# The healthcheck runs hourly. If its status file is older than this, a
# scheduled cycle was missed -- which is exactly what happens when the
# machine is too wedged to run it.
HEALTHCHECK_STALE_MIN = (100, 240)

# Bands are (warn, critical). Warn sits below the healthcheck's RED line on
# purpose -- this instrument's job is lead time, not agreement.
SWAP_GB = (6.0, 12.0)          # healthcheck goes RED at 8.0
FREE_PCT = (25, 12)            # healthcheck goes RED under 15
LOAD_PER_CORE = (1.2, 3.0)     # healthcheck goes RED over 1.5
ORPHAN_WORKERS = (4, 10)       # PPID-1 test workers: a leak, not a run

RENOTIFY_CRITICAL_SEC = 30 * 60

# tmux loops that must be alive, and the script each one runs.
#
# These paths are on /Volumes/Data, which this process cannot read -- and does
# not need to. It hands the path to tmux; the tmux SERVER forks the child, so
# the loop inherits the server's disk access rather than launchd's. That is the
# whole trick, and it is why revival works from here at all.
#
# It does NOT follow that a server has to already exist. Access attaches to the
# tmux binary, not to a running server, so a server launchd starts itself reads
# the volume exactly as well as one started from a Terminal -- measured with a
# fresh socket under `launchctl submit`, which is the cold-boot case. The guard
# therefore revives from no-server too, and `--selftest --cold` is what proves
# it on this machine rather than by argument.
LOOPS = {
    "fleet-budget":
        "/Volumes/Data/Users/bryanchan/dev/ai-team-lead/scripts/fleet_budget_loop.sh",
    "fleet-monitor":
        "/Volumes/Data/Users/bryanchan/dev/ai-team-lead/scripts/fleet_monitor_loop.sh",
}
TMUX = "/opt/homebrew/bin/tmux"
# Socket override, used only by `--selftest --cold`. A named socket that no
# server is listening on forces tmux to START one, which is the cold-boot
# condition; the default socket almost always has a server already and would
# quietly test the easy case instead.
TMUX_SOCKET = ""


def tmux():
    return f"{TMUX} {TMUX_SOCKET}".rstrip()


def sh(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout)
        return r.stdout.strip(), r.returncode
    except Exception as e:
        return f"EXC:{e}", 1


def sysctl(name):
    out, rc = sh(f"/usr/sbin/sysctl -n {name}")
    return out if rc == 0 else None


def band(value, warn, crit, higher_is_worse=True):
    if value is None:
        return "unknown"
    if higher_is_worse:
        if value >= crit:
            return "critical"
        if value >= warn:
            return "warn"
    else:
        if value <= crit:
            return "critical"
        if value <= warn:
            return "warn"
    return "ok"


def read_metrics():
    m = {}

    raw = sysctl("vm.swapusage")
    hit = re.search(r"used\s*=\s*([\d.]+)M", raw or "")
    m["swap_gb"] = round(float(hit.group(1)) / 1024, 2) if hit else None

    lvl = sysctl("kern.memorystatus_level")
    m["free_pct"] = int(lvl) if lvl and lvl.isdigit() else None

    raw = sysctl("vm.loadavg")
    hit = re.search(r"([\d.]+)", raw or "")
    ncpu = sysctl("hw.ncpu")
    if hit and ncpu and ncpu.isdigit():
        m["ncpu"] = int(ncpu)
        m["load_per_core"] = round(float(hit.group(1)) / int(ncpu), 2)
    else:
        m["ncpu"], m["load_per_core"] = None, None

    # Orphaned test workers. PPID 1 means the parent runner is gone and launchd
    # adopted them -- that is a permanent leak, not a test run in progress. This
    # was half the cause of the 2026-08-31 freeze (19 workers, 14 minutes old).
    out, _ = sh("/bin/ps -axo ppid=,rss=,args= | /usr/bin/grep -E "
                "'vitest|jest' | /usr/bin/grep -v grep")
    orphans, orphan_rss = 0, 0
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            ppid, rss = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if ppid == 1:
            orphans += 1
            orphan_rss += rss
    m["orphan_workers"] = orphans
    m["orphan_worker_gb"] = round(orphan_rss / 1024 / 1024, 2)

    out, _ = sh("/bin/ps -axo rss=,args= | /usr/bin/grep -E '/bin/claude' "
                "| /usr/bin/grep -v grep")
    rss_total, sessions = 0, 0
    for line in out.splitlines():
        parts = line.split(None, 1)
        try:
            rss_total += int(parts[0])
            sessions += 1
        except (ValueError, IndexError):
            continue
    m["claude_sessions"] = sessions
    m["claude_gb"] = round(rss_total / 1024 / 1024, 2)

    # Age of the hourly healthcheck's last completed run.
    #
    # A job that does not run cannot report that it did not run. That is not a
    # gap in the healthcheck, it is a property of self-reporting, and the only
    # cure is a second job on a different schedule asserting the first one's
    # freshness. This guard is that second job. The 2026-08-31 freeze is the
    # case: the machine was too wedged to run anything, and every instrument
    # that could have said so was one of the things not running.
    try:
        age = (time.time() - os.path.getmtime(HEALTHCHECK_STATUS)) / 60
        m["healthcheck_age_min"] = round(age, 1)
    except OSError:
        m["healthcheck_age_min"] = None

    return m


def grade(m):
    bands = {
        "swap": band(m["swap_gb"], *SWAP_GB),
        "memory": band(m["free_pct"], *FREE_PCT, higher_is_worse=False),
        "load": band(m["load_per_core"], *LOAD_PER_CORE),
        "orphan test workers": band(m["orphan_workers"], *ORPHAN_WORKERS),
        "healthcheck freshness": band(m["healthcheck_age_min"],
                                      *HEALTHCHECK_STALE_MIN),
    }
    rank = {"ok": 0, "unknown": 0, "warn": 1, "critical": 2}
    worst = max(bands.values(), key=lambda b: rank[b])
    return worst, bands


def loop_status():
    """Which monitor loops are alive.

    A missing server reports "down", not a separate un-revivable state. After a
    cold boot there is no server and both loops are down -- that is precisely
    the moment the guard exists for, and `new-session` starts a server on its
    own. Reporting it as "no-server" made the one case that matters the one
    case the guard refused to act on.
    """
    if not os.path.exists(TMUX):
        return {name: "no-tmux" for name in LOOPS}
    out, rc = sh(f"{tmux()} ls")
    if rc != 0:
        return {name: "down" for name in LOOPS}
    live = {line.split(":", 1)[0] for line in out.splitlines() if ":" in line}
    return {name: ("up" if name in live else "down") for name in LOOPS}


def revive(name, command):
    """Start one loop and VERIFY it took. Returns (ok, detail).

    The verification is the point. A revive that only fires the command and
    reports success is the same false green this whole file exists to remove --
    it would report "restarted" forever while the session died on every attempt.
    """
    sh(f"{tmux()} kill-session -t {name}")        # best effort; may not exist
    out, rc = sh(f"{tmux()} new-session -d -s {name} {command}")
    if rc != 0:
        return False, f"new-session rc={rc} {out[:120]}"
    time.sleep(3)                                  # let it fail if it is going to
    out, rc = sh(f"{tmux()} has-session -t {name}")
    if rc != 0:
        return False, "session did not survive 3s -- loop script exited"
    pane, _ = sh(f"{tmux()} capture-pane -p -t {name}")
    return True, (pane.strip().splitlines() or ["(no output yet)"])[-1][:120]


def selftest(cold=False):
    """Prove the revival path from whatever context this is running in.

    Uses a decoy session name so it can be run against the live fleet without
    firing a real "monitor loop down" alert -- testing a monitor by breaking the
    thing it watches produces a false alarm someone has to chase.

    `cold=True` moves to a private socket with no server on it, so tmux has to
    start one. Without that the test rides an existing server started from a
    Terminal and proves nothing about the case this guard is for.
    """
    global TMUX_SOCKET
    name = "guard-selftest"
    if cold:
        TMUX_SOCKET = "-L guard-cold-probe"
        sh(f"{tmux()} kill-server")   # ensure no server on this socket
    probe_cmd = ("/bin/bash -c 'ls /Volumes/Data/Users/bryanchan/dev "
                 ">/dev/null 2>&1 && echo SECONDARY-VOLUME-READABLE "
                 "|| echo SECONDARY-VOLUME-DENIED; sleep 30'")
    print(f"selftest {time.strftime('%Y-%m-%dT%H:%M:%S')} euid={os.geteuid()}")
    ok, detail = revive(name, probe_cmd)
    print(f"  revive({name}) -> ok={ok} detail={detail!r}")
    print("  VERDICT: launchd CAN revive a loop with disk access"
          if ok and "READABLE" in detail else
          "  VERDICT: revival from this context will NOT give the loop disk "
          "access -- guard must notify instead of self-healing")
    sh(f"{tmux()} kill-server" if cold else f"{tmux()} kill-session -t {name}")
    return 0


def notify(title, message):
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


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(STATE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"  !! could not write state: {e}")


def probe():
    """Report exactly what this process can and cannot do under launchd.

    Run once from a real LaunchAgent and read the log. Nothing in this file may
    depend on a capability that has not appeared in a probe run -- the whole
    class of bug this guards against is a monitor that assumes access it does
    not have and reports green forever.
    """
    print(f"probe {time.strftime('%Y-%m-%dT%H:%M:%S')}  euid={os.geteuid()}")
    print(f"  state dir writable: {os.access(STATE_DIR, os.W_OK)} ({STATE_DIR})")
    for name in ("vm.swapusage", "kern.memorystatus_level", "vm.loadavg", "hw.ncpu"):
        print(f"  sysctl {name}: {str(sysctl(name))[:70]!r}")
    out, rc = sh("/bin/ps -axo pid= | /usr/bin/wc -l")
    print(f"  ps: rc={rc} procs={out}")
    print(f"  tmux binary present: {os.path.exists(TMUX)}")
    out, rc = sh(f"{tmux()} ls")
    print(f"  tmux ls: rc={rc} out={out[:200]!r}")
    out, rc = sh("/bin/ls /Volumes/Data/Users/bryanchan/dev >/dev/null")
    print(f"  secondary volume readable (expected NO): rc={rc}")
    print(f"  notify path: {notify('fleet guard', 'probe run -- ignore')}")


def main():
    if "--probe" in sys.argv:
        probe()
        return 0
    if "--selftest" in sys.argv:
        return selftest(cold="--cold" in sys.argv)

    m = read_metrics()
    worst, bands = grade(m)
    loops = loop_status()
    revived = {}
    for name, status in list(loops.items()):
        if status == "down":
            ok, detail = revive(name, LOOPS[name])
            revived[name] = detail
            loops[name] = "revived" if ok else "revive-failed"
    down = [n for n, s in loops.items() if s not in ("up", "revived")]

    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    detail = (f"swap {m['swap_gb']}GB · free {m['free_pct']}% · "
              f"load {m['load_per_core']}/core · orphan workers "
              f"{m['orphan_workers']} ({m['orphan_worker_gb']}GB) · "
              f"{m['claude_sessions']} sessions ({m['claude_gb']}GB) · "
              f"healthcheck {m['healthcheck_age_min']}m old")
    print(f"[{stamp}] {worst.upper()} {detail} · loops {loops}")

    state = load_state()
    prev = state.get("band", "ok")
    prev_down = state.get("loops_down", [])
    last_notified = state.get("notified_at", 0)
    now = time.time()

    rank = {"ok": 0, "unknown": 0, "warn": 1, "critical": 2}
    escalated = rank[worst] > rank.get(prev, 0)
    recovered = worst == "ok" and rank.get(prev, 0) > 0
    stale_critical = (worst == "critical"
                      and now - last_notified > RENOTIFY_CRITICAL_SEC)
    loops_newly_down = sorted(set(down) - set(prev_down))

    fired = False
    if escalated or stale_critical:
        bad = ", ".join(f"{k} {v}" for k, v in bands.items() if v != "ok")
        notify(f"Fleet guard: {worst}", f"{bad} — {detail}")
        fired = True
    elif recovered:
        notify("Fleet guard: recovered", detail)
        fired = True
    if revived:
        ok = [n for n in revived if loops[n] == "revived"]
        if ok:
            notify("Monitor loop restarted", f"{', '.join(ok)} was down, back up")
            fired = True
    if loops_newly_down:
        notify("Monitor loop down",
               f"{', '.join(loops_newly_down)} not running and could not be "
               f"restarted")
        fired = True

    save_state({
        "band": worst,
        "bands": bands,
        "metrics": m,
        "loops": loops,
        "loops_down": down,
        "revived": revived,
        "checked_at": stamp,
        "notified_at": now if fired else last_notified,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
