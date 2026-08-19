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
CONFIG = os.path.join(STATE_DIR, "healthcheck-config.json")
OVERLAY = os.path.join(HOME, ".config", "team-lead", "healthcheck-extra.json")

LABEL = "com.fryanpan.fleet-healthcheck"
PLIST = os.path.join(HOME, "Library", "LaunchAgents", f"{LABEL}.plist")
HOURS = [8, 13, 18]

# Every check below asserts an END STATE. Each maps to a specific outage that a
# liveness check called green on -- see the module docstring in the checker.
BASE_CHECKS = [
    # --- daemons that must be up and owned by launchd (not by a session) ---
    {"type": "launchd", "label": "com.fryanpan.notion-channel-receiver"},
    {"type": "launchd", "label": "com.fryanpan.github-channel-broker"},
    {"type": "launchd", "label": "com.fryanpan.live-feedback"},
    {"type": "launchd", "label": "live-feedback.cloudflared"},
    {"type": "launchd", "label": "notion-channel.cloudflared"},
    {"type": "launchd", "label": "com.fryanpan.email-channel-watcher"},

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
    {"type": "http", "name": "live-feedback local", "url": "http://127.0.0.1:8787/"},
    {"type": "http", "name": "github broker", "expect": '"ok":true',
     "url": "http://127.0.0.1:7902/health"},

    # --- the machine itself. Added 2026-08-18 after Bryan reported it feeling
    #     slow: 13GB was swapped out on a 16GB machine and nothing anywhere
    #     said so. Every one of these reads kernel state and names no process,
    #     so it goes red when the machine is short rather than when some
    #     particular program is large. ---
    {"type": "swap", "name": "swap", "max_used_gb": 8.0},
    {"type": "free_memory", "name": "free memory", "min_free_pct": 15},
    {"type": "load", "name": "load", "max_per_core": 1.5},

    # --- alive and failing: the shape no process check can see ---
    {"type": "log_errors", "name": "email watcher", "max": 0,
     "path": "~/Library/Logs/email-channel-watcher.log",
     "pattern": r'"level":\s*"error"', "window_minutes": 90},
    {"type": "log_errors", "name": "notion receiver", "max": 3,
     "path": "~/Library/Logs/notion-channel-receiver.log",
     "pattern": r'"level":\s*"error"', "window_minutes": 90},
    {"type": "log_errors", "name": "github broker", "max": 0,
     "path": "~/Library/Logs/github-channel-broker.log",
     "pattern": r"WARNING|error", "window_minutes": 1440},

    # --- running but inert: the broker answers {"ok":true} on /health with no
    #     token and simply never polls, so only the file itself is evidence ---
    {"type": "file_present", "name": "github token", "why": "broker cannot poll without it",
     "path": "~/.config/github-claude-channel/env"},

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
            out.append({"type": "session", "cwd": real,
                        "name": f"session: {name or key}"})

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
        "StartCalendarInterval": [{"Hour": h, "Minute": 20} for h in HOURS],
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
    print(f"scheduled {LABEL} at {', '.join(f'{h}:20' for h in HOURS)} local")
    return 0


if __name__ == "__main__":
    sys.exit(main())
