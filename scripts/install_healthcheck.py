#!/usr/bin/env python3
"""Deploy fleet_healthcheck.py to the boot disk and schedule it.

Run this from an interactive session (which CAN read /Volumes/Data) whenever
the checker or the registry changes. It does three things:

  1. copies fleet_healthcheck.py to ~/Library/Application Support/team-lead/,
     because a launchd-invoked Apple interpreter cannot open a file on the
     secondary volume at all (verified 2026-08-11);
  2. generates healthcheck-config.json there -- infra checks inline below, one
     session check per respawn:true registry entry, plus any local overlay;
  3. installs a LaunchAgent that runs it three times a day.

Project names live only in the generated config on the boot disk, never in this
repo -- ai-team-lead is public.

Usage:
    python3 scripts/install_healthcheck.py            # deploy + schedule
    python3 scripts/install_healthcheck.py --no-agent # deploy config only
"""

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "scripts", "fleet_healthcheck.py")
GUARD_SRC = os.path.join(REPO, "scripts", "fleet_guard.py")
REGISTRY = os.path.join(REPO, "registry.yaml")

HOME = os.path.expanduser("~")

# Boot-disk deploy root, shared by every launchd-run thing in this fleet.
#
# "Put it in $HOME" is NOT sufficient on this machine: ~/.claude, ~/.config,
# ~/.local and ~/.bun are each a symlink into /Volumes/Data, and a launchd-
# invoked Apple-signed binary is denied every operation on that volume -- exec,
# read, even a stat. /opt is genuinely disk3s5, is not shadowed by a symlink
# anyone might repoint later, and matches where /opt/homebrew already lives.
#
# It needs one manual step, because /opt is root-owned:
#     sudo mkdir -p /opt/fleet && sudo chown "$USER":admin /opt/fleet
# Until that exists we fall back to ~/Library/Application Support/team-lead,
# which is real boot disk too -- just a stranger home for executables.
PREFERRED_ROOT = "/opt/fleet"
FALLBACK_ROOT = os.path.join(HOME, "Library", "Application Support", "team-lead")


def deploy_root():
    if os.path.isdir(PREFERRED_ROOT) and os.access(PREFERRED_ROOT, os.W_OK):
        return PREFERRED_ROOT
    print(f"  ! {PREFERRED_ROOT} not writable -- deploying to {FALLBACK_ROOT}")
    print(f"  ! to use it: sudo mkdir -p {PREFERRED_ROOT} && "
          f'sudo chown "$USER":admin {PREFERRED_ROOT}')
    return FALLBACK_ROOT


STATE_DIR = deploy_root()
DEST = os.path.join(STATE_DIR, "fleet_healthcheck.py")
GUARD_DEST = os.path.join(STATE_DIR, "fleet_guard.py")
CONFIG = os.path.join(STATE_DIR, "healthcheck-config.json")
OVERLAY = os.path.join(HOME, ".config", "team-lead", "healthcheck-extra.json")

LABEL = "com.fryanpan.fleet-healthcheck"
PLIST = os.path.join(HOME, "Library", "LaunchAgents", f"{LABEL}.plist")
# Hourly, at :20. It was 3x/day until 2026-09-01, when the log showed the
# cost: the 18:20 run was green, the machine froze around 21:00, and the
# 23:22 run that caught it -- load 13.70/core, both ports down, two tunnels
# unreachable, two sessions dead -- only happened because a human triggered
# it by hand. The next scheduled run at 08:20 would have found everything
# recovered and green, and a multi-hour outage would exist in no record
# anywhere. A green run costs no tokens, so the schedule was the only thing
# buying that blindness.
HOURLY_MINUTE = 20

# The guard is a second, much faster agent -- see the module docstring in
# fleet_guard.py. It exists because HOURS above cannot be tightened: the full
# check does network round-trips and 3x/day is the right cadence for those.
# Machine pressure needs minutes, so it gets its own job with its own budget.
GUARD_LABEL = "com.fryanpan.fleet-guard"
GUARD_PLIST = os.path.join(HOME, "Library", "LaunchAgents", f"{GUARD_LABEL}.plist")
GUARD_INTERVAL = 120

# Every check below asserts an END STATE. Each maps to a specific outage that a
# liveness check called green on -- see the module docstring in the checker.
BASE_CHECKS = [
    # --- daemons that must be up and owned by launchd (not by a session) ---
    {"type": "launchd", "label": "com.fryanpan.notion-channel-receiver"},
    {"type": "launchd", "label": "com.fryanpan.github-channel-broker"},
    # Both spellings, per the fleet rename rule: the job is live under either
    # during the transition and pinning to one produces a false RED.
    {"type": "launchd", "label": ["com.fryanpan.claude-workspaces",
                                 "com.fryanpan.live-feedback"]},
    {"type": "launchd", "label": "live-feedback.cloudflared"},
    {"type": "launchd", "label": "notion-channel.cloudflared"},

    # --- the scheduled half: these have no PID between runs, so check_launchd
    #     would be red at every quiet moment. What they owe us is proof they
    #     have ever fired. A job that has never run has never written its
    #     output, so every freshness check on it reads as one ambiguous
    #     missing file -- `runs = 0` is the only signal that separates
    #     "never started" from "wrong path" from "not deployed yet". ---
    {"type": "launchd_ran", "label": "com.fryanpan.fleet-healthcheck",
     "why": "this checker itself would report nothing at all"},
    # --- the observation neither agent could make on the day: an FDA grant
    #     lapsed silently and a reboot restored it, so nobody knows whether a
    #     grant survives a restart. This records boot session + readability
    #     every run and compares across the reboot automatically. ---
    {"type": "tcc_grant", "name": "secondary-volume access"},

    {"type": "launchd_ran", "label": "com.fryanpan.fleet-guard",
     "why": "nothing would notice a downed loop between hourly checks"},

    # --- exactly one listener per port: two in different address families
    #     both bind successfully and silently steal each other's traffic ---
    {"type": "port", "port": 8791, "name": "notion receiver"},
    {"type": "port", "port": 8787, "name": "live-feedback"},
    {"type": "port", "port": 7902, "name": "github broker"},
    {"type": "port", "port": 7900, "name": "claude-hive"},

    # --- end-to-end paths. The tunnel probe is the only check that proves a
    #     webhook can actually reach the daemon; a local port proves nothing
    #     about what the public hostname routes to. ---
    {"type": "http", "name": "notion tunnel", "expect": '"status":"ok"',
     "url": "https://notion-bridge.fryanpan.com/health"},
    # An expect marker, because without one this passed on ANY non-empty body
    # -- a wrong service on the port, an error page, or a stale static response
    # all read green.
    {"type": "http", "name": "live-feedback local", "expect": "<title>Workspaces</title>",
     "url": "http://127.0.0.1:8787/"},
    # End-to-end through the cloudflared tunnel, mirroring the notion one. The
    # launchd check on that tunnel only asserts a live PID, so a wedged or
    # misrouted tunnel -- precisely the bug class this checker exists for -- read
    # green forever. This hostname has no Access application in front of it, so
    # the response comes from the origin app rather than from Cloudflare: that
    # JSON body is only producible by the server at the far end of the tunnel,
    # and Cloudflare's own failure pages (1033, 502) are HTML that cannot match.
    {"type": "http", "name": "live-feedback tunnel", "expect": '"error":"not_found"',
     "url": "https://recall.fryanpan.com/"},
    # `polling`, not `ok`. The token spec below documents why: /health answers
    # {"ok":true} with no token and no polling at all, so matching on ok made
    # this check pass in precisely the state it existed to catch. Dropping the
    # log-silence bound above is only safe because this one now asserts the
    # broker is actually working.
    {"type": "http", "name": "github broker", "expect": '"polling":true',
     "url": "http://127.0.0.1:7902/health"},

    # --- the machine itself. Added 2026-08-18 after Bryan reported it feeling
    #     slow: 13GB was swapped out on a 16GB machine and nothing anywhere
    #     said so. Every one of these reads kernel state and names no process,
    #     so it goes red when the machine is short rather than when some
    #     particular program is large. ---
    # Swap ACTIVITY, not level. The old 8.0GB level ceiling sat on the median
    # at the fleet's normal size (1,887 fleet-guard samples, 2026-09-01..03:
    # 8.0GB median at 11 sessions, 10.5GB at 12), and macOS never gives the
    # allocation back, so it ratcheted. Rate of swapout is the thing that
    # actually costs a working day.
    {"type": "swap", "name": "swap", "max_swapout_mb_s": 5.0,
     "sample_seconds": 15},
    {"type": "free_memory", "name": "free memory", "min_free_pct": 15},
    {"type": "load", "name": "load", "max_per_core": 1.5},

    # --- alive and failing: the shape no process check can see ---
    # Email watcher checks RETIRED 2026-09-01. Its Google Cloud project was
    # deleted, so it 401s on every poll -- 72 error lines per 90m, red on every
    # run for days. A check that is red every single run is furniture: it costs
    # attention at each review and carries no information. Restoring it needs
    # Bryan's Google account, so it cannot be fixed from here. Re-add both
    # entries (git history has them) if the GCP project is ever recreated.
    # No silence bound here, deliberately. This receiver logs on Notion events,
    # so its quiet measures Bryan's Notion activity, not the daemon -- 720m
    # fired at 726m on 2026-09-01 while the process was listening and
    # answering /health with 200. A liveness signal you cannot separate from a
    # quiet weekend is not a liveness signal. Responsiveness is asserted by the
    # http check below instead, which a wedged process fails and an idle one
    # passes; the port check alone would not, since a hung process keeps its
    # socket.
    {"type": "log_errors", "name": "notion receiver", "max": 3,
     "path": "~/Library/Logs/notion-channel-receiver.log",
     "pattern": r'"level":\s*"error"', "window_minutes": 90,
     "max_error_streak": 6},
    # An `expect` marker, for the same reason the tunnel check above has one.
    # 2026-09-03: a peer's dev workspaces server bound *:8791 while the receiver
    # was down, answered this URL, and the check went GREEN -- a port that
    # replies is not the daemon that is supposed to reply. Notion comments were
    # dropped for an unknown stretch behind a green check.
    {"type": "http", "name": "notion receiver", "expect": '"status":"ok"',
     "url": "http://127.0.0.1:8791/health"},
    # `ignore` covers the broker start race, and nothing else. Every session's
    # MCP server tries to start a broker if one is not already up; when one is,
    # the attempt fails and logs at error level. It is the expected outcome of
    # a correct design, and it was 70 of the 74 lines keeping this check red --
    # so the genuine token warning arrived as 4 lines in a flood of 74. The
    # honest fix is upstream (do not log an expected condition as an error);
    # until that lands this states the tolerance in one place instead of
    # narrowing the pattern until it catches nothing new.
    {"type": "log_errors", "name": "github broker", "max": 0,
     "path": "~/Library/Logs/github-channel-broker.log",
     "pattern": r"WARNING|error", "window_minutes": 1440,
     "ignore": r"Failed to start server\. Is port \d+ in use\?",
     # NO max_silence_minutes. THIS LOG IS EVENT-DRIVEN: the broker writes on
     # session register/expire and on watches, and nothing else. A quiet fleet
     # writes nothing, so silence has never distinguished wedged from idle here.
     #
     # It was 360m, then raised to 900m for exactly that reason, and on
     # 2026-09-08 it fired again at 1034m -- a Sunday-into-Monday with no
     # session churn -- while /health returned
     # {"ok":true,"degraded":false,"polling":true,"sessions":9}. Three strikes
     # on a bound that was never measuring the thing it claimed to.
     #
     # Liveness for this daemon comes from the http check plus the token check
     # below, NOT from silence. Note the next spec's warning: /health answers
     # {"ok":true} even with no token and no polling, so bare ok proves nothing.
     # The fields that do are `polling` and `tokenSource`, and on 2026-09-08
     # they read true and "gh auth token" with 9 sessions attached while this
     # check called the broker wedged.
     #
     # A recurring false RED is not free -- it is the one that teaches everyone
     # to skim past the RED list, including the real ones.
     "max_error_streak": 6},

    # --- running but inert: the broker answers {"ok":true} on /health with no
    #     token and simply never polls, so /health is not evidence of anything.
    #     This used to assert the token FILE existed. That stopped being the
    #     question once the broker gained a keyring fallback: the file can be
    #     absent and the broker perfectly healthy. Assert what actually
    #     determines the outcome -- that SOME source answers, in the broker's
    #     own order -- and name which one, since a keyring-backed token and a
    #     file-backed one expire and break differently. ---
    {"type": "token_resolvable", "name": "github token",
     "why": "broker cannot poll without a token from any source",
     "env": "GITHUB_TOKEN",
     "path": "~/.config/github-claude-channel/env",
     "commands": [["/opt/homebrew/bin/gh", "auth", "token"],
                  ["/usr/local/bin/gh", "auth", "token"]],
     "commands_label": "gh keyring"},

    # --- the budget watcher is a tmux loop, not a launchd job, so nothing
    #     restarts it and nothing notices it stopped. Its tmux session can be
    #     alive while the loop inside is dead. The state file's mtime is the
    #     only proof a run completed. Interval is 15m; 45m is three missed
    #     runs, which is a real stall rather than a slow pass. ---
    {"type": "state_fresh", "name": "budget watch",
     "why": "5h-window burn watch is not iterating; a quota wall would go unseen",
     "path": "~/Library/Application Support/team-lead/budget-watch.json",
     "max_age_minutes": 45},

    # --- both tmux monitor loops, read out of the guard's own state. Only the
    #     budget watcher leaves an artifact to age-check; fleet-monitor writes
    #     nothing, so its death was invisible to every other check here. The
    #     guard grades both every 2 minutes, so 15m is seven missed passes. ---
    #     The path is RESOLVED, not spelled out: fleet_guard.py picks its own
    #     state dir the same way this installer picks its deploy root, so a
    #     literal here goes stale the moment the root moves. It did -- the guard
    #     migrated to /opt/fleet and this check spent hours aging an abandoned
    #     file. Only the mtime guard below turned that into a RED; without it
    #     the frozen copy would have reported both loops up forever. ---
    {"type": "monitor_loops", "name": "monitor loops",
     "why": "a downed loop means the fleet is unwatched and nothing says so",
     "path": os.path.join(STATE_DIR, "guard-state.json"),
     "max_age_minutes": 15},

    # --- did anyone actually READ the quota meter? The token-watch is a
    #     session-scoped cron: it dies on every team-lead respawn, and its
    #     re-arm is a SessionStart directive asking the agent to arm it. One
    #     missed read-through and the fleet has no quota instrument, silently
    #     and indefinitely -- exactly what happened 08-30 to 09-01. The budget
    #     watch stayed green throughout because it measures the share split
    #     between projects, not the absolute pool. 8h spans two of the three
    #     daily runs, so a single skipped firing does not cry wolf. ---
    {"type": "rate_limit_hits", "name": "session-limit hits",
     "why": "six episodes in four days were each found by Bryan, not by us",
     "window_hours": 24},
    {"type": "trend_log", "name": "quota trend log",
     "why": "a dead token-watch reads exactly like a fleet that is fine",
     "path": os.path.join(REPO, "docs/process/token-control.md"),
     # 16h, not 8h. The token-watch fires at 8:07, 13:07 and 18:07, so the
     # OVERNIGHT gap is structurally ~14h. An 8h bound therefore went RED every
     # single morning no matter how healthy the meter was -- furniture, and the
     # kind that trains a reader to skip the whole section.
     "max_age_hours": 16},

    # --- the monitor auditing itself. Editing the repo copy changes nothing;
    #     launchd execs the deployed copy. Without this, a forgotten redeploy
    #     means every check below silently runs the OLD file, three green times
    #     a day, with no surface saying the new one never ran. ---
    {"type": "self_version", "name": "checker version",
     "source": "~/dev/ai-team-lead/scripts/fleet_healthcheck.py"},

    # --- delivery, not schedule: the archiver's launchd job died 2026-08-08 and
    #     nothing surfaced it for 17 days, because running the analysis pipeline
    #     by hand copied the same files and kept the folder looking current. This
    #     asserts transcripts ARRIVED, so it fails whichever path stops. Age is
    #     the signal, not count -- a fresh pipeline run drives count to ~0 by
    #     construction, while age is monotonic until something actually copies. ---
    {"type": "archive_backlog", "name": "transcript archive",
     "live_root": "~/.claude/projects",
     "archive_root": "~/dev/weekly-review-transcripts",
     "max_age_days": 21,
     "owner": "Weekly Review (owns the pipeline + archive-transcripts.sh)"},

    # --- the wake half. A plugin enabled without a launch flag has the tools
    #     and receives no events; argv is the only place that shows. ---
    {"type": "channel_flags", "name": "channel flags", "required": [
        "server:claude-hive",
        ["plugin:claude-workspaces@claude-workspaces",
         "plugin:live-feedback@claude-live-feedback"],
        "plugin:notion-channel-mcp@notion-channel-mcp",
        "plugin:github-claude-channel@github-claude-channel",
    ]},
]


def registry_sessions():
    """One session check per `always_up: true` project, keyed on its real path.

    Deliberately NOT `respawn: true`. That flag means "bring this back on a
    fleet restart", not "must be running now" -- the fleet is lean by default
    and most peers are correctly down between tasks. Keying on it made the very
    first run report five intended-idle projects as failures, which is how a
    monitor teaches you to ignore it.

    Minimal regex parse, same approach as respawn.py -- no PyYAML dependency.
    """
    if not os.path.exists(REGISTRY):
        print(f"  ! no registry at {REGISTRY}; skipping session checks")
        return []

    with open(REGISTRY) as f:
        lines = f.read().splitlines()

    out, key, path, name, always_up = [], None, None, None, False

    def flush():
        if key and always_up and path:
            real = os.path.realpath(os.path.expanduser(path))
            # max_idle_hours is long on purpose: idle is the correct state
            # for a peer between tasks, so a tight bound would go red on a
            # session working exactly as intended. This catches "alive and
            # processing nothing at all", not "quiet this hour".
            out.append({"type": "session", "cwd": real,
                        "name": f"session: {name or key}",
                        "max_idle_hours": 36})

    for line in lines:
        m = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
        if m:
            flush()
            key, path, name, always_up = m.group(1), None, None, False
            continue
        m = re.match(r"^    path:\s*(.+?)\s*$", line)
        if m:
            path = m.group(1).strip('"\'')
        m = re.match(r"^    always_up:\s*true\b", line)
        if m:
            always_up = True
        m = re.match(r"^    session_name:\s*(.+?)\s*$", line)
        if m:
            name = m.group(1).split("#")[0].strip().strip('"\'')
    flush()
    return out


def plugin_version_checks():
    """One staleness check per directory-source plugin.

    Generated here rather than hand-listed because this runs interactively and
    can actually read the plugin repos; the checker itself runs under launchd
    and cannot open anything on the secondary volume (it delegates those reads
    to bun at runtime).

    Only directory-source marketplaces are checked — those are the ones whose
    source tree is a local repo that can silently get ahead of the installed
    cache. A remote marketplace has no local source to drift from.
    """
    known = os.path.join(HOME, ".claude", "plugins", "known_marketplaces.json")
    if not os.path.exists(known):
        print("  ! no known_marketplaces.json; skipping plugin checks")
        return []

    with open(known) as f:
        data = json.load(f)
    markets = data.get("marketplaces", data)

    out = []
    for mkt, meta in (markets.items() if isinstance(markets, dict) else []):
        src = (meta or {}).get("source") or {}
        if src.get("source") != "directory":
            continue
        root = os.path.realpath(os.path.expanduser(src.get("path", "")))
        cache_root = os.path.join(HOME, ".claude", "plugins", "cache", mkt)
        if not os.path.isdir(root) or not os.path.isdir(cache_root):
            continue
        for plugin in sorted(os.listdir(cache_root)):
            if not os.path.isdir(os.path.join(cache_root, plugin)):
                continue
            manifest = find_manifest(root, plugin)
            if not manifest:
                print(f"  ! {plugin}: no plugin.json found under {root}")
                continue
            out.append({
                "type": "plugin_version", "name": f"plugin: {plugin}",
                "plugin": plugin, "marketplace": mkt,
                "cache_dir": os.path.join(cache_root, plugin),
                "source_manifest": manifest,
            })
    return out


def find_manifest(root, plugin):
    """Locate `<...>/.claude-plugin/plugin.json` whose name matches `plugin`.

    Skips git worktrees and takes the SHALLOWEST match. Both matter: the
    live-feedback repo keeps worktrees under `.claude/worktrees/`, each with a
    full copy of the plugin pinned at whatever version that branch forked from.
    A plain walk hit a worktree copy at 0.0.2 first and reported the plugin as
    current while the fleet ran four versions behind — a staleness check that
    is silently always-green, which is worse than not having one.
    """
    candidates = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "node_modules", "__pycache__",
                                    "worktrees", ".claude-worktrees")]
        if os.path.basename(dirpath) != ".claude-plugin":
            continue
        if "plugin.json" not in filenames:
            continue
        candidates.append(os.path.join(dirpath, "plugin.json"))

    # Shallowest first, so the main tree beats any nested copy.
    candidates.sort(key=lambda p: (p.count(os.sep), p))
    for path in candidates:
        try:
            with open(path) as f:
                if json.load(f).get("name") == plugin:
                    return path
        except Exception:
            continue
    return candidates[0] if candidates else None


def main():
    os.makedirs(STATE_DIR, exist_ok=True)

    shutil.copy2(SRC, DEST)
    shutil.copy2(GUARD_SRC, GUARD_DEST)
    os.chmod(DEST, 0o755)
    print(f"deployed checker -> {DEST}")

    # Leave no second copy behind after a root change -- a stale config in the
    # old root is a monitor reporting on a config nobody maintains any more.
    if STATE_DIR != FALLBACK_ROOT:
        for stale in ("fleet_healthcheck.py", "healthcheck-config.json",
                      "healthcheck-status.json"):
            p = os.path.join(FALLBACK_ROOT, stale)
            if os.path.exists(p):
                os.remove(p)
                print(f"  removed stale {p}")

    checks = list(BASE_CHECKS)
    sessions = registry_sessions()
    plugins = plugin_version_checks()
    checks.extend(sessions)
    checks.extend(plugins)
    print(f"  {len(BASE_CHECKS)} infra + {len(sessions)} session "
          f"+ {len(plugins)} plugin checks")

    # An overlay entry replaces a base entry with the same name/label, so a
    # daemon can be silenced or retuned locally without editing this file.
    if os.path.exists(OVERLAY):
        with open(OVERLAY) as f:
            extra = json.load(f).get("checks", [])
        def ident(c):
            return c.get("label") or c.get("name") or f"{c.get('type')}:{c.get('port')}"
        by_id = {ident(c): c for c in checks}
        for c in extra:
            by_id[ident(c)] = c
        checks = list(by_id.values())
        print(f"  merged {len(extra)} overlay checks from {OVERLAY}")

    with open(CONFIG, "w") as f:
        json.dump({"checks": checks}, f, indent=2)
    print(f"wrote config -> {CONFIG} ({len(checks)} checks)")

    if "--no-agent" in sys.argv:
        return 0

    plist = {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/python3", DEST],
        # Minute with no Hour means every hour, at that minute.
        "StartCalendarInterval": [{"Minute": HOURLY_MINUTE}],
        "RunAtLoad": True,
        "StandardOutPath": os.path.join(HOME, "Library/Logs/fleet-healthcheck.log"),
        "StandardErrorPath": os.path.join(HOME, "Library/Logs/fleet-healthcheck.log"),
        "EnvironmentVariables": {
            "HOME": HOME,
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        },
    }
    with open(PLIST, "wb") as f:
        plistlib.dump(plist, f)

    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", PLIST],
                       capture_output=True, text=True)
    if r.returncode:
        print(f"bootstrap FAILED: {r.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"scheduled {LABEL} hourly at :{HOURLY_MINUTE:02d} local")

    guard_plist = {
        "Label": GUARD_LABEL,
        "ProgramArguments": ["/usr/bin/python3", GUARD_DEST],
        "StartInterval": GUARD_INTERVAL,
        "RunAtLoad": True,
        "StandardOutPath": os.path.join(HOME, "Library/Logs/fleet-guard.log"),
        "StandardErrorPath": os.path.join(HOME, "Library/Logs/fleet-guard.log"),
        "EnvironmentVariables": {
            "HOME": HOME,
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        },
    }
    with open(GUARD_PLIST, "wb") as f:
        plistlib.dump(guard_plist, f)
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{GUARD_LABEL}"],
                   capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", GUARD_PLIST],
                       capture_output=True, text=True)
    if r.returncode:
        print(f"guard bootstrap FAILED: {r.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"scheduled {GUARD_LABEL} every {GUARD_INTERVAL}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
