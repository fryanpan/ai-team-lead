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
gets worse, when what the alert says changes (trend bucket, bands, cause), once
when it recovers, and otherwise at most every 2 hours (RENOTIFY_UNCHANGED_SEC).
"""

import getpass
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

HOME = os.path.expanduser("~")
PREFERRED_ROOT = "/opt/fleet"
FALLBACK_ROOT = os.path.join(HOME, "Library", "Application Support", "team-lead")
STATE_DIR = PREFERRED_ROOT if os.path.isdir(PREFERRED_ROOT) else FALLBACK_ROOT
STATE = os.path.join(STATE_DIR, "guard-state.json")
HEALTHCHECK_STATUS = os.path.join(STATE_DIR, "healthcheck-status.json")
HISTORY = os.path.join(STATE_DIR, "guard-history.jsonl")

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
BOOT_FREE_GB = (25.0, 20.0)    # swapfiles live on the boot disk; critical under 20 set by the owner 2026-09-30
ANON_GB = (10.0, 13.0)         # anonymous+compressed demand on a 16GB machine

# Why anonymous pages and free boot disk are here, and RSS is not enough
# --------------------------------------------------------------------
# On 2026-09-09 the machine went from swap 0GB to 41GB in fourteen minutes and
# had to be power-cycled. Nothing in this file could say what took the memory,
# and the one number that looked like it should have -- `claude_gb`, the summed
# RSS of the claude sessions -- FELL from 3.6GB to 1.55GB while it happened.
# That is not noise. RSS counts resident pages, so it drops as the crisis
# deepens and the pages move to swap: the metric is anti-correlated with the
# thing it appears to measure, and reading it as "the sessions were small"
# is the trap.
#
# `Anonymous pages` + the compressor is the population that CAN go to swap, so
# it keeps rising while RSS falls. Free boot-disk space matters because the
# swapfiles are written there: 41GB of swap on a volume with tens of GB free is
# a second wall behind the first one, and nothing was watching it either.


# Trend and paging bands (added 2026-10-23).
#
# On 2026-10-22 swap climbed from ~14GB to 16GB over three hours while load per
# core read 0.4-0.7 in every snapshot, so each alert read "high swap, low load,
# wait". Then the machine stalled (1-min load 25 on 10 cores) with Chrome
# renderers holding ~11GB. A point-in-time number cannot say "rising"; the
# direction needs the history, and active paging needs a rate. Load per core is
# the wrong instrument for a machine that is waiting on the disk.
SWAP_TREND_GB = (1.5, 4.0)            # rise over the window; (warn, critical)
SWAP_TREND_WINDOW_SEC = 2 * 60 * 60
SWAP_TREND_MIN_SPAN_SEC = 40 * 60     # less history than this: say so, not "flat"
PAGING_MB_S = (50.0, 150.0)           # pageins+pageouts; the 21:43 spike was ~200
PAGING_MAX_GAP_SEC = 15 * 60          # older previous sample: no rate, say so
RENOTIFY_UNCHANGED_SEC = 2 * 60 * 60  # an unchanged alert is resent this rarely

# A renderer that has burned this much CPU at this average load is a runaway
# tab, not a busy one. Any user: a renderer in another account still costs the
# shared RAM and CPU.
RUNAWAY_CPU_SEC = 10 * 60 * 60
RUNAWAY_CPU_FRACTION = 0.5
CHROME_FIX = ("Chrome Memory Saver (chrome://settings/performance) or close "
              "tabs; the guard does not kill it")

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

    # Anonymous + compressed pages: the population that can be swapped, and the
    # one number that keeps RISING through a swap event while every RSS falls.
    raw = sysctl("vm.pagesize") or ""
    hit = re.search(r"(\d+)", raw)
    pagesize = int(hit.group(1)) if hit else 16384
    out, _ = sh("/usr/bin/vm_stat")
    pages = {}
    for line in out.splitlines():
        hit = re.match(r'"?([^":]+)"?:\s+(\d+)', line.strip())
        if hit:
            pages[hit.group(1).strip().lower()] = int(hit.group(2))
    anon = pages.get("anonymous pages", 0)
    comp = pages.get("pages occupied by compressor", 0)
    m["anon_gb"] = round(anon * pagesize / 1024 ** 3, 2) if anon else None
    m["compressed_gb"] = round(comp * pagesize / 1024 ** 3, 2) if comp else None

    # Cumulative paging counters: only a DIFFERENCE between two runs means
    # anything, so keep the raw counters and the clock they were read at.
    m["pagesize"] = pagesize
    m["pageins"] = pages.get("pageins")
    m["pageouts"] = pages.get("pageouts")
    m["epoch"] = time.time()

    # Free space on the BOOT disk, which is where the swapfiles are written.
    try:
        st = os.statvfs(STATE_DIR)
        m["boot_free_gb"] = round(st.f_bavail * st.f_frsize / 1024 ** 3, 2)
    except OSError:
        m["boot_free_gb"] = None

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


def parse_cpu_time(raw):
    """ps `time`, `[[dd-]hh:]mm:ss[.cc]`, to seconds. None if it does not parse."""
    m = re.match(r"^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)(?:\.(\d+))?$",
                 (raw or "").strip())
    if not m:
        return None
    dd, hh, mm, ss, frac = m.groups()
    secs = ((int(dd or 0) * 24 + int(hh or 0)) * 60 + int(mm)) * 60 + int(ss)
    return secs + (float("0." + frac) if frac else 0.0)


def parse_process_table(raw):
    """Rows from `ps -axo pid=,user=,rss=,time=,etime=,comm=`.

    `comm` is last because macOS gives the full executable path and it holds
    spaces ("Google Chrome Helper (Renderer)"). Unparseable lines are skipped.
    """
    rows = []
    for line in (raw or "").splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        try:
            pid, rss = int(parts[0]), int(parts[2])
        except ValueError:
            continue
        rows.append({"pid": pid, "user": parts[1], "rss_kb": rss,
                     "cpu_sec": parse_cpu_time(parts[3]),
                     "wall_sec": parse_ps_etime(parts[4]),
                     "comm": parts[5].strip()})
    return rows


def read_process_table():
    """The parsed table, or None if `ps` could not be read (never [] for that)."""
    out, rc = sh("/bin/ps -axo pid=,user=,rss=,time=,etime=,comm=", timeout=15)
    if rc != 0:
        return None
    return parse_process_table(out)


def is_chrome(comm):
    return "google chrome" in comm.lower()


def is_chrome_renderer(comm):
    # Seen live: "Google Chrome Helper (Renderer)" and, in another build,
    # "Google Chrome Helper (Aperitif Renderer)".
    low = comm.lower()
    return "google chrome helper" in low and "renderer" in low


def phys_footprint_mb(pid):
    """Real memory for one pid, compressed pages included. None if unreadable."""
    out, rc = sh(f"/usr/bin/footprint -p {pid}", timeout=10)
    if rc != 0:
        return None
    m = re.search(r"phys_footprint:\s*([\d.]+)\s*([KMG])B", out)
    if not m:
        return None
    v, unit = float(m.group(1)), m.group(2)
    return v / 1024 if unit == "K" else v if unit == "M" else v * 1024


def top_holders(limit=8, candidates=40):
    """Who is holding the memory, by process name, largest first.

    Runs only when the band is not ok, because it is the postmortem that was
    missing: on 2026-09-09 the swap went to 41GB and no instrument anywhere
    recorded a single per-process number, so the culprit could not be named
    afterwards from any log on the machine.

    RANKED BY phys_footprint, NOT RSS. RSS was the instrument here until
    2026-09-24 and it did not merely understate -- it INVERTED the order, which
    is the one thing this line exists to get right. Measured that morning at
    swap 13.9GB: RSS ranked `bun 1.3GB x42` above `java 0.8GB x3`, while the
    two Gradle/Kotlin daemons actually held 11.0GB and 4.8GB between them and
    every bun server together held 1.7GB. A reader who acted on that line would
    have gone after the wrong process, and nobody would have found the 15.8GB.

    RSS still picks the CANDIDATES, because reading footprint for every process
    costs too much at a 120s cadence. A process swapped out so hard it leaves
    the top `candidates` by RSS is the known blind spot of doing it this way.
    """
    out, rc = sh("/bin/ps -axo rss=,pid=,comm=", timeout=15)
    if rc != 0:
        return "ps failed"
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            rss, pid = int(parts[0]), parts[1]
        except ValueError:
            continue
        rows.append((rss, pid, parts[2].strip().rsplit("/", 1)[-1]))
    rows.sort(reverse=True)

    agg, measured = {}, 0
    for rss, pid, name in rows[:candidates]:
        mb = phys_footprint_mb(pid)
        if mb is None:
            mb = rss / 1024.0          # fall back to RSS for this one process
        else:
            measured += 1
        cur = agg.get(name, [0.0, 0])
        cur[0] += mb
        cur[1] += 1
        agg[name] = cur

    if measured == 0:
        # Three states, not two: say the instrument could not run rather than
        # letting an RSS-only ranking read as a footprint ranking.
        return "footprint unavailable -- RSS ranking only: " + " · ".join(
            f"{n} {v[0] / 1024:.1f}GB x{v[1]}"
            for n, v in sorted(agg.items(), key=lambda kv: -kv[1][0])[:limit])

    ranked = sorted(agg.items(), key=lambda kv: -kv[1][0])[:limit]
    return " · ".join(f"{n} {v[0] / 1024:.1f}GB x{v[1]}"
                           for n, v in ranked if v[0] > 100)


def chrome_cause(rows, me):
    """Is Chrome the top memory holder among `me`'s processes?

    All of a user's Chrome processes (main, Helper, Renderer, GPU) count as one
    holder: to the person fixing it Chrome is one program, and split by helper
    name it would rank below a single large claude session. Returns None when
    Chrome is not the top holder. Names only; never acts.
    """
    if not rows:
        return None
    totals = {}
    for r in rows:
        if r["user"] != me:
            continue
        key = "chrome" if is_chrome(r["comm"]) else r["comm"].rsplit("/", 1)[-1]
        totals[key] = totals.get(key, 0) + r["rss_kb"]
    if not totals or max(totals, key=totals.get) != "chrome":
        return None
    renderers = sum(1 for r in rows
                    if r["user"] == me and is_chrome_renderer(r["comm"]))
    return {"gb": round(totals["chrome"] / 1024 / 1024, 1),
            "renderers": renderers}


def find_runaway_renderers(rows):
    """Chrome renderers (any user) with >RUNAWAY_CPU_SEC of CPU time at a
    >RUNAWAY_CPU_FRACTION average. A row with unreadable times is skipped:
    unknown is not runaway."""
    out = []
    for r in rows or []:
        if not is_chrome_renderer(r["comm"]):
            continue
        cpu, wall = r.get("cpu_sec"), r.get("wall_sec")
        if not cpu or not wall:
            continue
        if cpu > RUNAWAY_CPU_SEC and cpu / wall > RUNAWAY_CPU_FRACTION:
            out.append({"pid": r["pid"], "user": r["user"],
                        "cpu_h": round(cpu / 3600, 1),
                        "avg_pct": round(100 * cpu / wall)})
    return out


def row_epoch(row):
    if row.get("epoch"):
        return row["epoch"]
    try:
        return time.mktime(time.strptime(row.get("at") or "", "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def read_history(limit=400):
    """The last `limit` history rows (2h is ~60 rows at the 2-minute cadence).
    [] when there is none yet -- swap_trend reports that as unknown, not flat."""
    try:
        with open(HISTORY) as f:
            lines = f.readlines()[-limit:]
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _span_text(sec):
    mins = int(round(sec / 300.0)) * 5
    return f"{mins // 60}h" if mins % 60 == 0 else f"{mins}m"


def swap_trend(rows, swap_gb, now):
    """Swap's direction over the last ~2h, from history rows.

    Returns {"band", "text"}. band is "unknown" (loud, reason in `text`) when
    there is too little history or no current reading -- never "ok" by
    default, because "flat" and "cannot see" are different states. "Rising"
    means the rise over the window is meaningful AND swap has not started
    falling over the last 30 minutes.
    """
    if swap_gb is None:
        return {"band": "unknown", "text": "swap trend unknown (no swap reading)"}
    pts = []
    for r in rows:
        e, g = row_epoch(r), r.get("swap_gb")
        if e is not None and g is not None and now - SWAP_TREND_WINDOW_SEC <= e <= now:
            pts.append((e, g))
    pts.sort()
    if not pts or now - pts[0][0] < SWAP_TREND_MIN_SPAN_SEC:
        have = _span_text(now - pts[0][0]) if pts else "0m"
        return {"band": "unknown",
                "text": f"swap trend unknown (only {have} of history)"}
    span = now - pts[0][0]
    rise = swap_gb - pts[0][1]
    recent = [g for e, g in pts if e >= now - 30 * 60]
    falling = bool(recent) and swap_gb < max(recent) - 0.3
    bnd = band(rise, *SWAP_TREND_GB)
    if bnd != "ok" and falling:
        bnd = "ok"
    word = "rising" if bnd != "ok" else ("falling" if falling else "flat")
    return {"band": bnd, "rise_gb": round(rise, 2), "span_sec": int(span),
            "text": f"swap {rise:+.1f}GB in {_span_text(span)}, {word}"}


def paging_rate(prev, cur):
    """Page-in + page-out throughput in MB/s between two metric snapshots.

    Returns {"band", "text"}; band "unknown" says why (no previous sample,
    stale sample, counters reset, vm_stat unreadable). A paging storm is the
    one thing load per core does not show: the machine is waiting on the disk,
    not computing.
    """
    def unk(why):
        return {"band": "unknown", "text": f"paging rate unknown ({why})"}
    keys = ("pageins", "pageouts", "epoch")
    if any(cur.get(k) is None for k in keys):
        return unk("no vm_stat reading")
    if not prev or any(prev.get(k) is None for k in keys):
        return unk("no previous sample")
    dt = cur["epoch"] - prev["epoch"]
    if dt <= 0 or dt > PAGING_MAX_GAP_SEC:
        return unk("previous sample stale")
    dins, douts = cur["pageins"] - prev["pageins"], cur["pageouts"] - prev["pageouts"]
    if dins < 0 or douts < 0:
        return unk("counters reset")
    size = cur.get("pagesize") or 16384
    ins, outs = dins * size / dt / 1024 ** 2, douts * size / dt / 1024 ** 2
    return {"band": band(ins + outs, *PAGING_MB_S),
            "in_mb_s": round(ins, 1), "out_mb_s": round(outs, 1),
            "text": f"paging {ins:.0f}MB/s in, {outs:.0f}MB/s out"}


def describe_cause(chrome, runaways):
    """Alert fragments naming the cause. Suggests; never acts."""
    parts = []
    if chrome:
        parts.append(f"Chrome is the top memory holder: {chrome['gb']}GB across "
                     f"{chrome['renderers']} renderers -- try {CHROME_FIX}")
    for r in runaways:
        parts.append(f"runaway tab: Chrome renderer pid {r['pid']} (user "
                     f"{r['user']}) {r['cpu_h']}h CPU at {r['avg_pct']}% average")
    return parts


def alert_signature(worst, bands, trend, chrome, runaways):
    """What an alert SAYS, coarsely: change this and a resend is warranted.
    Swap rise is bucketed to whole GB so jitter does not read as change."""
    rise = trend.get("rise_gb")
    return json.dumps({
        "worst": worst,
        "bands": sorted(k for k, v in bands.items() if v in ("warn", "critical")),
        "trend": None if rise is None or trend["band"] == "ok" else int(rise),
        "chrome": bool(chrome),
        "runaways": sorted(r["pid"] for r in runaways),
    }, sort_keys=True)


def sig_got_worse(old, new):
    """True when `new` says something `old` did not: a band, a higher trend
    bucket, Chrome as cause, or a runaway pid. A fact dropping out is not news.
    Comparing for any difference resent on every flip of Chrome and the claude
    sessions trading first place — four alerts in six minutes on 2026-10-23."""
    if not old:
        return True
    o, n = json.loads(old), json.loads(new)
    return (bool(set(n["bands"]) - set(o["bands"]))
            or (n["trend"] is not None and (o["trend"] is None or n["trend"] > o["trend"]))
            or (n["chrome"] and not o["chrome"])
            or bool(set(n["runaways"]) - set(o["runaways"])))


def should_notify(prev, worst, sig, now):
    """Why to send this run's alert, or None to stay quiet.

    `prev` is the prior state. Sends when the band got worse, the alert content
    changed (trend bucket, which bands, cause, runaways), or
    RENOTIFY_UNCHANGED_SEC has passed. The old rule resent every critical alert
    unchanged each 30 minutes, which on 2026-10-22 buried the one fact that
    was changing.
    """
    rank = {"ok": 0, "unknown": 0, "warn": 1, "critical": 2}
    prev_band = prev.get("band", "ok")
    if worst == "ok":
        return "recovered" if rank.get(prev_band, 0) > 0 else None
    if rank[worst] > rank.get(prev_band, 0):
        return "escalated"
    if sig_got_worse(prev.get("notified_sig"), sig):
        return "changed"
    if now - prev.get("notified_at", 0) > RENOTIFY_UNCHANGED_SEC:
        return "unchanged-resend"
    return None


# --------------------------------------------------------------------------
# Orphaned headless Chrome reaper
# --------------------------------------------------------------------------
#
# A throwaway headless Chrome launched by an agent run (screenshots, a
# ui:shot-style helper) is meant to die with the run that launched it. When
# that run is SIGKILLed -- a Bash tool timeout, a stopped agent session --
# Chrome never gets the signal that would make it clean up after itself, and
# macOS reparents it to launchd (pid 1). It then sits there holding memory
# until somebody notices.
#
# The matching predicate mirrors
# claude-live-feedback-plugin/scripts/chrome-orphans.ts findOrphans() --
# a process is an orphan only if ALL of:
#   - it runs `--headless` (Bryan's own Chrome never does, and must never be
#     touched)
#   - its `--user-data-dir=` sits inside a temp directory (TMPDIR,
#     /var/folders/..., /tmp, /private/tmp) -- never a real profile
#   - its parent is pid 1 -- a live run's Chrome is parented to that run, so
#     no live run's browser can ever match this
#   - it is older than CHROME_ORPHAN_MIN_AGE_SEC -- so the reaper never races
#     a launcher's own cleanup mid-shutdown
#
# Each check is load-bearing on its own; test_fleet_guard.py proves removing
# any one of them changes what gets matched, not just asserts the fixtures.

CHROME_ORPHAN_MIN_AGE_SEC = 10 * 60   # matches chrome-orphans.ts ORPHAN_REAP_AGE_MS
CHROME_HEADLESS_FLAG = "--headless"


def parse_ps_etime(raw):
    """ps `etime`, `[[dd-]hh:]mm:ss`, to seconds. None if it does not parse."""
    m = re.match(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$", (raw or "").strip())
    if not m:
        return None
    dd, hh, mm, ss = m.groups()
    dd, hh = int(dd or 0), int(hh or 0)
    return ((dd * 24 + hh) * 60 + int(mm)) * 60 + int(ss)


def is_temp_dir_path(path):
    """True if `path` sits inside a temp directory -- the user's TMPDIR,
    /var/folders/..., /tmp, or /private/tmp. A profile anywhere else is never
    a reap candidate, however old or headless the process running it is."""
    if not path:
        return False
    roots = {"/tmp", "/private/tmp", "/var/folders"}
    tmpdir_env = os.environ.get("TMPDIR")
    if tmpdir_env:
        roots.add(tmpdir_env.rstrip("/"))
    roots.add(tempfile.gettempdir().rstrip("/"))
    norm = path.rstrip("/")
    return any(norm == r or norm.startswith(r + "/") for r in roots if r)


def find_chrome_orphans(ps_output, min_age_sec=CHROME_ORPHAN_MIN_AGE_SEC):
    """The orphaned headless Chromes in a `ps -Ao pid=,ppid=,etime=,command=`
    listing. Matches the four criteria described above; mirrors
    chrome-orphans.ts findOrphans() field-for-field, minus its
    project-specific PROFILE_PREFIX scoping (there is no equivalent prefix
    here, so every temp-dir profile is in scope).

    `ps_output` being `None` is a caller error, not an empty listing --
    reap_orphaned_chromes is where "ps could not be read" is kept loud and
    distinct from "ps was read and found nothing."
    """
    out = []
    for line in (ps_output or "").splitlines():
        m = re.match(r"^\s*(\d+)\s+(\d+)\s+(\S+)\s+(.*)$", line)
        if not m:
            continue
        pid, ppid, etime, command = m.groups()
        if int(ppid) != 1:
            continue
        if CHROME_HEADLESS_FLAG not in command:
            continue
        if "chrome" not in command.lower():
            continue
        prof = re.search(r"--user-data-dir=(\S+)", command)
        if not prof or not is_temp_dir_path(prof.group(1)):
            continue
        age = parse_ps_etime(etime)
        if age is None or age < min_age_sec:
            continue
        out.append({"pid": int(pid), "profile": prof.group(1), "age_sec": age,
                    "command": command})
    return out


def _list_processes_wide():
    """`ps -Ao pid=,ppid=,etime=,command=`, or None if `ps` could not be read.

    None, not "", on failure -- an empty string would parse to zero orphans,
    indistinguishable from a clean machine. This is the "could not look"
    state and it must stay loud all the way out to the caller.
    """
    out, rc = sh("/bin/ps -Ao pid=,ppid=,etime=,command=", timeout=15)
    return out if rc == 0 else None


def process_rss_mb(pid):
    """RSS for one pid in MB, or None if `ps` has nothing for it (already exited)."""
    out, rc = sh(f"/bin/ps -o rss= -p {pid}", timeout=5)
    if rc != 0 or not out.strip():
        return None
    try:
        return round(int(out.strip().splitlines()[0]) / 1024, 1)
    except ValueError:
        return None


def reap_orphaned_chromes(list_processes=None, min_age_sec=CHROME_ORPHAN_MIN_AGE_SEC,
                          dry_run=False, log=print):
    """Kill every orphaned headless Chrome `find_chrome_orphans` names (its
    helper children go with it -- they are parented to the main process, not
    to pid 1, so killing the main process takes them down too) and log each
    kill with pid, profile dir and memory size.

    Returns `(orphans, killed)`. `orphans` is `None` when `ps` could not be
    read at all, so a caller can tell "looked, found nothing" (`[]`) from
    "could not look" (`None`) -- never collapse the two into a silent zero.
    """
    list_fn = list_processes or _list_processes_wide
    raw = list_fn()
    if raw is None:
        log("CHROME-REAP ps unreadable -- state UNKNOWN, not treating as clean")
        return None, []

    orphans = find_chrome_orphans(raw, min_age_sec=min_age_sec)
    killed = []
    for o in orphans:
        mb = process_rss_mb(o["pid"])
        size = f"{mb}MB" if mb is not None else "size unknown (process already gone?)"
        if dry_run:
            log(f"CHROME-REAP would kill pid {o['pid']} profile={o['profile']} "
                f"age={o['age_sec'] // 60}m rss={size}")
            continue

        # Re-check right before the kill: an orphan that exited since the
        # first listing may have handed its pid to an unrelated process. This
        # narrows the race to one `ps` call instead of the whole loop.
        raw2 = list_fn()
        if raw2 is None:
            log(f"CHROME-REAP could not re-verify pid {o['pid']} before kill -- skipping")
            continue
        still = any(x["pid"] == o["pid"] and x["profile"] == o["profile"]
                   for x in find_chrome_orphans(raw2, min_age_sec=min_age_sec))
        if not still:
            continue

        try:
            os.kill(o["pid"], signal.SIGKILL)
        except ProcessLookupError:
            continue
        except OSError as e:
            log(f"CHROME-REAP kill failed for pid {o['pid']}: {e}")
            continue
        killed.append(o)
        log(f"CHROME-REAP killed pid {o['pid']} profile={o['profile']} "
            f"age={o['age_sec'] // 60}m rss={size}")
    return orphans, killed


def grade(m, trend=None, paging=None, runaways=()):
    bands = {
        "swap": band(m["swap_gb"], *SWAP_GB),
        "memory": band(m["free_pct"], *FREE_PCT, higher_is_worse=False),
        "load": band(m["load_per_core"], *LOAD_PER_CORE),
        "orphan test workers": band(m["orphan_workers"], *ORPHAN_WORKERS),
        "anon+compressed": band(m["anon_gb"], *ANON_GB),
        "boot disk free": band(m["boot_free_gb"], *BOOT_FREE_GB,
                               higher_is_worse=False),
        "healthcheck freshness": band(m["healthcheck_age_min"],
                                      *HEALTHCHECK_STALE_MIN),
        "swap trend": (trend or {}).get("band", "unknown"),
        "paging": (paging or {}).get("band", "unknown"),
        "runaway tab": "warn" if runaways else "ok",
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


HIVE_SEND = "http://127.0.0.1:7900/send-message"
HIVE_PEERS = "http://127.0.0.1:7900/list-peers"


def team_lead_stable_id():
    """The Team Lead session's stable_id, from the hive's own registry.

    Matched on the cwd rather than a hardcoded id, because the id changes when
    the session is respawned and the cwd does not.
    """
    out, rc = sh(f"curl -s -m 5 -X POST {HIVE_PEERS} "
                 f"-H 'content-type: application/json' "
                 f"-d '{{\"scope\":\"machine\"}}'", timeout=10)
    if rc != 0:
        return None
    try:
        peers = json.loads(out)
    except Exception:
        return None
    for peer in peers:
        if "/ai-team-lead" in (peer.get("cwd") or ""):
            return peer.get("stable_id")
    return None


def hive_alert(title, message):
    """Put the alert in front of an AGENT, not only on the desktop.

    `notify` draws a macOS banner. Nothing reads a banner: on 2026-09-24 the
    guard sampled every 120s, correctly graded swap `critical`, and fired a
    banner at 10:43 that nobody saw, while the machine sat at 14.3GB of swap
    until the user said it was slow. The sampling was never the gap -- the
    delivery was.

    Returns "sent", "no-peer", or "failed", and the caller records which. A
    silent failure here reads exactly like a quiet machine.
    """
    sid = team_lead_stable_id()
    if not sid:
        return "no-peer"
    payload = json.dumps({"from_id": "fleet-guard", "to_stable_id": sid,
                          "text": f"[fleet-guard] {title}. {message}"[:1200]})
    out, rc = sh(f"curl -s -m 5 -X POST {HIVE_SEND} "
                 f"-H 'content-type: application/json' "
                 f"-d {json.dumps(payload)}", timeout=10)
    return "sent" if rc == 0 else "failed"


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


def record_history(state):
    """Append one snapshot to a durable log. Never fails the run.

    `guard-state.json` is OVERWRITTEN every pass, so every number in it exists
    for one interval and is then gone. That is fine for "is the machine sick
    right now" and useless for the question actually being asked of it.

    Bryan's hardware decision (2026-09-16) is explicitly gated on a WEEK of
    measurements taken after the cheap fixes. Without this file that week does
    not accumulate anywhere: the metrics get computed on schedule, overwritten
    on schedule, and in seven days the decision would rest on whatever single
    sample happened to be sitting in the state file -- which is how the last
    hosting report ended up citing a 39.7%-degraded figure nobody could
    reproduce. Same failure family as a check that cannot tell "looked and
    found nothing" from "could not look": the instrument reads correctly and
    retains nothing, and the absence is silent.

    One flat line per run, append-only, no rotation here -- rotate_fleet_logs.py
    owns that. Metrics only: no process names, no paths, nothing about what the
    sessions were doing.
    """
    try:
        row = {"at": state.get("checked_at"), "band": state.get("band")}
        row.update(state.get("metrics") or {})
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(HISTORY, "a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as e:
        # Deliberately swallowed and SAID. A recorder that takes the guard down
        # is worse than one that misses a sample, but a recorder that fails
        # quietly leaves the same empty file as one that was never called.
        print(f"  !! could not append history: {e}")


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
    if "--chrome-list" in sys.argv:
        reap_orphaned_chromes(dry_run=True, log=print)
        return 0
    if "--chrome-reap" in sys.argv:
        reap_orphaned_chromes(dry_run=False, log=print)
        return 0

    m = read_metrics()
    now = time.time()
    state = load_state()
    prev = state

    # Trend and cause, from history and one process-table read. "unknown" is a
    # state of its own and is printed, never folded into ok.
    trend = swap_trend(read_history(), m["swap_gb"], now)
    paging = paging_rate(state.get("metrics"), m)
    rows = read_process_table()
    me = getpass.getuser()
    runaways = find_runaway_renderers(rows)
    chrome = chrome_cause(rows, me)
    worst, bands = grade(m, trend, paging, runaways)
    loops = loop_status()
    revived = {}
    for name, status in list(loops.items()):
        if status == "down":
            ok, detail = revive(name, LOOPS[name])
            revived[name] = detail
            loops[name] = "revived" if ok else "revive-failed"
    down = [n for n, s in loops.items() if s not in ("up", "revived")]

    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    detail = (f"swap {m['swap_gb']}GB ({trend['text']}) · {paging['text']} · "
              f"anon+comp {m['anon_gb']}/"
              f"{m['compressed_gb']}GB · free {m['free_pct']}% · "
              f"boot disk {m['boot_free_gb']}GB free · "
              f"load {m['load_per_core']}/core · orphan workers "
              f"{m['orphan_workers']} ({m['orphan_worker_gb']}GB) · "
              f"{m['claude_sessions']} sessions ({m['claude_gb']}GB rss) · "
              f"healthcheck {m['healthcheck_age_min']}m old")
    if rows is None:
        detail += " · process table UNREADABLE (cause unknown)"
    print(f"[{stamp}] {worst.upper()} {detail} · loops {loops}")

    # Every cycle, independent of the swap/memory band: reap any headless
    # Chrome left behind by a killed agent run. Silent when there is nothing
    # to reap (matches the guard's own "silent unless something changed"
    # design); loud when `ps` itself could not be read.
    reap_orphaned_chromes(dry_run=False, log=lambda msg: print(f"[{stamp}] {msg}"))

    # The attribution line. Only on a bad band, and written BEFORE any notify
    # so it survives a machine that wedges before the next run.
    holders = ""
    causes = describe_cause(chrome, runaways)
    if worst in ("warn", "critical"):
        holders = top_holders()
        print(f"[{stamp}] HOLDERS {holders}")
        for c in causes:
            print(f"[{stamp}] CAUSE {c}")
        sys.stdout.flush()

    prev_down = state.get("loops_down", [])
    last_notified = state.get("notified_at", 0)

    sig = alert_signature(worst, bands, trend, chrome, runaways)
    reason = should_notify(prev, worst, sig, now)
    loops_newly_down = sorted(set(down) - set(prev_down))

    fired = False
    if reason in ("escalated", "changed", "unchanged-resend"):
        bad = ", ".join(f"{k} {v}" for k, v in bands.items()
                        if v in ("warn", "critical"))
        trendline = [t["text"] for t in (trend, paging) if t["band"] != "ok"]
        notify(f"Fleet guard: {worst}",
               " · ".join([bad] + trendline + causes) + f" — {detail}")
        # The banner is for the person at the machine; this is for the agent
        # that can act on it. Both, every time -- neither substitutes.
        delivery = hive_alert(f"{worst.upper()} — {bad}",
                              " · ".join(trendline + causes)
                              + f" · {detail} · HOLDERS {holders}")
        print(f"[{stamp}] HIVE-ALERT {delivery}")
        sys.stdout.flush()
        state["hive_alert"] = delivery
        fired = True
    elif reason == "recovered":
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

    snapshot = {
        "band": worst,
        "bands": bands,
        "metrics": m,
        "holders": holders,
        "loops": loops,
        "loops_down": down,
        "revived": revived,
        "checked_at": stamp,
        "notified_at": now if fired else last_notified,
        "notified_sig": sig if reason in ("escalated", "changed", "unchanged-resend")
                        else state.get("notified_sig"),
    }
    save_state(snapshot)
    record_history(snapshot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
