#!/usr/bin/env python3
"""
Respawn Bryan's long-running Claude Code sessions in detached tmux sessions.

Reads `registry.yaml` from the parent ai-team-lead repo. For each
project with `respawn: true`, depending on --mode:
  - `missing` (default): spawn only if no claude is running with that cwd.
  - `plugin`: kill + respawn any session that does not have the
    canonical fleet plugin loaded, under either install key
    (`claude-workspaces@claude-workspaces` or the pre-rename
    `live-feedback@claude-live-feedback`).
  - `all`: kill + respawn every respawn=true session (skipping the team-lead's
    own claude — never kill self).
  - `running`: kill + respawn every session that is ACTUALLY running, at the
    path holding its transcript, ignoring the registry entirely. This is the
    account-switch mode — after a /login, a session keeps the old account's
    MCP connections, RC registration, and startup env until it restarts.

Launches via tmux (`zsh -ic 'claude --continue'`). The interactive zsh shell
sources ~/.zshrc, where the `claude` shell function injects the canonical
channel + dev-plugin flags. Bare /Users/.../bin/claude does NOT get those
flags.

When --execute is passed and any sessions are spawned, the script also (by
default):
  - Polls each new tmux pane for known startup dialogs (resume-from-summary,
    dev-channel approval, MCP-server approval, generic "Enter to confirm")
    and sends Enter to dismiss them — the safe default in every dialog we
    ship is "yes/accept".
  - Sweeps orphaned `claude-hive-mcp/server.ts` processes (re-parented to
    PID 1) left behind by killed claudes — without this, new sessions can't
    register with claude-hive cleanly.

Pass --no-auto-accept to skip the post-spawn dialog acceptance + cleanup.

Pass --fresh to spawn every session with EMPTY context (no --continue). The
previous conversation is not lost -- it stays on disk and is resumable with
/resume. For per-agent behaviour set `fresh_start: true` on a registry entry
instead; that is how an assistant clears at task boundaries while a builder
keeps its history. There is no agent-callable /clear, so this is the only
mechanical way to clear a session.

Safety: this script is **dry-run by default**. Pass `--execute` to actually
do anything destructive (kill/spawn/sweep). Without `--execute` it just
prints what it WOULD do.

No PyYAML dependency — uses a minimal regex-based parser that handles the
registry's specific structure (2-space project keys, 4-space scalar fields).
Ignores nested blocks like `docs:`, `linear:`, `notion:` etc.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
REGISTRY_PATH = os.path.join(REPO_ROOT, "registry.yaml")
LSOF_TIMEOUT_SEC = 5.0
PS_TIMEOUT_SEC = 5.0

# Marker substrings in claude argv indicating the canonical fleet plugin is
# loaded. Used by --mode=plugin to detect sessions that need an upgrade.
#
# BOTH spellings are accepted, and this is load-bearing during the
# live-feedback -> claude-workspaces rename (2026-08-18). A session emits the
# new install key only after it restarts onto the new bundle, so the two
# coexist across the fleet for the whole transition. Matching only one spelling
# would make --mode=plugin classify every session on the other side of the
# rollout as "missing the plugin" and kill+respawn the entire fleet -- the most
# expensive possible false positive, since the fix looks exactly like the work.
PLUGIN_MARKERS = (
    "plugin:claude-workspaces@claude-workspaces",
    "plugin:live-feedback@claude-live-feedback",
)


def has_fleet_plugin(argv: str) -> bool:
    """True if argv carries the fleet plugin under EITHER name."""
    return any(m in argv for m in PLUGIN_MARKERS)

TMUX_BIN = "/opt/homebrew/bin/tmux"

# Per-peer DISCORD_STATE_DIR. Gotcha: if you don't scope this per-session, every
# peer claude inherits the spawner's DISCORD_STATE_DIR (via env propagation),
# which means they all read the team-lead's access.json + bot token and all
# subscribe to the team-lead's channel. A single Discord post to the team-lead's
# channel then fans out to every peer.
#
# Encoded fix:
#   - Peers WITH a local `.claude/discord/access.json` use their own state.
#   - Peers WITHOUT one use the shared "no-discord" state dir at
#     `~/.config/team-lead/no-discord/` — discord plugin loads (the channel
#     flag is in Bryan's zsh `claude` function and isn't easily removed from
#     a per-peer basis) but has no token + empty access, so nothing connects.
#   - Per-session env is passed via `tmux new-session -e VAR=VAL`, NOT via the
#     spawner's environment. Reason: tmux's `new-session` reuses the existing
#     tmux server's env if a server is already running; the client's env at
#     spawn time gets ignored. The `-e` flag overrides per-session.
NO_DISCORD_STATE_DIR = os.path.expanduser("~/.config/team-lead/no-discord")


def ensure_no_discord_state() -> None:
    """Create the shared no-discord state dir if it doesn't exist. The empty
    allowlist + missing .env means the discord plugin loads but doesn't connect."""
    os.makedirs(NO_DISCORD_STATE_DIR, exist_ok=True)
    access_json = os.path.join(NO_DISCORD_STATE_DIR, "access.json")
    if not os.path.isfile(access_json):
        with open(access_json, "w") as f:
            f.write('{"dmPolicy":"allowlist","allowFrom":[],"groups":{},"pending":{}}\n')


def discord_state_dir_for(path: str) -> str:
    """Return the DISCORD_STATE_DIR a peer should use.

    - If the peer has its own `.claude/discord/access.json` (e.g., octoturtle
      with a family-bot setup), use the peer's local state dir.
    - Otherwise, use the shared no-discord state — the plugin loads but
      doesn't connect because there's no token + empty access.
    """
    local = os.path.join(os.path.realpath(path), ".claude", "discord")
    if os.path.isfile(os.path.join(local, "access.json")):
        return local
    return NO_DISCORD_STATE_DIR


# Project-scoped MCP servers that stand in for a broken `plugin:` channel.
# Background (2026-08-09): every plugin-provided MCP server stopped connecting
# fleet-wide — Claude Code resolved them into the config and then never
# attempted the connection. The workaround is to register the same server as an
# ordinary MCP server (`claude mcp add <name> --scope local -- <same command>`),
# which uses the code path that still works. But `--channels` in the `claude`
# zsh function names the PLUGIN server, so the direct copy provides the tools
# and no inbound push: Discord messages stop waking the session. Declaring the
# direct server as a channel too is what restores the wake.
#
# Keyed off ~/.claude.json rather than hardcoded, so a peer gets the flag only
# if it actually has the direct registration.
#
# live-feedback was briefly on this list (2026-08-10) and has been removed. Its
# outage was NOT a plugin-path fault: the plugin shipped `"command": "node"`,
# and node comes from nvm, so it existed only in an interactive shell. Plugin
# 0.1.0 fixed that in the right place — a /bin/sh launcher committed in the
# plugin itself — and a cold session with node absent from PATH now connects the
# PLUGIN server in 244ms. Reaching for a direct registration was the wrong
# instinct: check that the server's own spawn command works from a
# non-interactive shell before concluding the plugin mechanism is at fault.
DIRECT_CHANNEL_SERVERS = ["plugin_discord_discord"]


def project_config_keys(path: str) -> List[str]:
    """The ~/.claude.json `projects` keys that could hold this cwd's config.

    Usually just the realpath. But for a GIT WORKTREE, Claude Code keys project
    config by the MAIN repo rather than the worktree — running `claude mcp add`
    from inside a worktree writes the entry under the repo that owns the git
    common dir. Measured 2026-08-10: looking up only the realpath silently found
    nothing and dropped the channel flag for every worktree session.
    """
    keys = [os.path.realpath(path)]
    common = subprocess.run(["git", "-C", path, "rev-parse", "--git-common-dir"],
                            capture_output=True, text=True)
    if common.returncode == 0:
        main_repo = os.path.dirname(os.path.realpath(
            os.path.join(path, common.stdout.strip())))
        if main_repo not in keys:
            keys.append(main_repo)
    return keys


def direct_channel_flags(path: str) -> str:
    """Return `--dangerously-load-development-channels server:<name>` flags for
    any DIRECT_CHANNEL_SERVERS registered project-scoped for this path."""
    try:
        with open(os.path.expanduser("~/.claude.json")) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return ""
    projects = cfg.get("projects") or {}
    registered = {}
    for key in project_config_keys(path):
        registered.update((projects.get(key) or {}).get("mcpServers") or {})
    return "".join(
        f" --dangerously-load-development-channels server:{name}"
        for name in DIRECT_CHANNEL_SERVERS if name in registered
    )

# Substrings that identify known interactive startup dialogs. If any of these
# appear in a session's captured pane, we send Enter to accept the default
# (which is always the safe option: "resume from summary", "approve channel",
# "trust workspace"). Pattern list intentionally narrow — false positives
# would send unwanted Enters into an interactive session.
DIALOG_PATTERNS = [
    "Resume from summary",                    # auto-compact-on-resume dialog
    "Resume full session as-is",              # same dialog (different line)
    "I am using this for local development",  # dev-channel approval (--dangerously-load-development-channels)
    "Use this and all future MCP servers",    # MCP-server approval dialog
    "Enter to confirm",                       # generic confirm dialog footer (catches any dialog with this footer)
]

# The resume dialog specifically. Its DEFAULT option is "Resume from summary",
# i.e. a bare Enter COMPACTS the session. Under --no-compact we arrow down one
# and take "Resume full session as-is" instead, preserving full context.
RESUME_DIALOG_PATTERNS = [
    "Resume from summary",
    "Resume full session as-is",
]
DIALOG_POLL_INTERVAL_SEC = 3.0
DIALOG_POLL_MAX_ITER = 25  # ~75s of polling — zsh -ic boot is slower than direct binary, dialogs can take 30-50s to appear

# Seconds to wait between spawns.
#
# WHY THIS EXISTS (2026-07-13): spawning the whole fleet at once made every session
# race to start its plugin MCP servers simultaneously. Some handshakes lost the race,
# Claude gave up on those servers, and their tools were never registered — leaving an
# MCP child process alive but orphaned and idle. The peer looked healthy from the
# outside and had silently lost a channel (Octoturtle lost Discord; two peers lost
# live-feedback). Nobody noticed for days, because "the process is running" was the
# only thing anyone checked.
#
# Staggering is cheap insurance. Do not set this to 0 to "speed up" a fleet respawn.
SPAWN_STAGGER_SEC = 8.0
# Discord-bearing sessions start last and slower — see the spawn loop.
DISCORD_STAGGER_SEC = 25.0

# Plugin MCP servers we expect a healthy session to have spoken to. Used by the
# post-spawn health check, which is advisory: it reports, it does not fail the run.
EXPECTED_MCP_MARKERS = {
    "claude-hive": "claude-hive-mcp",
    # Matches either checkout dir name across the rename: the repo is
    # claude-workspaces-plugin, the older local clone is
    # claude-live-feedback-plugin. The tail is identical in both.
    "workspaces": "-plugin/packages/plugin/mcp",
    "discord": "claude-plugins-official/discord",
}


# --- registry.yaml parsing ---------------------------------------------------

def parse_registry(path: str) -> Dict[str, Dict[str, str]]:
    if not os.path.isfile(path):
        sys.exit(f"registry.yaml not found at {path}")
    projects: Dict[str, Dict[str, str]] = {}
    current: Optional[str] = None
    with open(path, "r") as f:
        for raw in f:
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = re.match(r"^  ([a-zA-Z][a-zA-Z0-9_-]*):\s*$", line)
            if m:
                current = m.group(1)
                projects[current] = {}
                continue
            if current is None:
                continue
            m = re.match(r"^    ([a-z_]+):\s*(.*)$", line)
            if m:
                key = m.group(1)
                value = m.group(2).strip().strip('"').strip("'")
                if value:
                    projects[current][key] = value
    return projects


def humanize(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").title()


# --- fresh-start (clear-on-respawn) ------------------------------------------
#
# There is NO agent-callable /clear or /compact. Verified against the 2.1.229
# binary: both are `type:"local"` commands dispatched from the input line, and
# every programmatic route into a session's queue (hive/peer, MCP channel, the
# report path, /loop wake-ups, cron fires) is enqueued with
# `skipSlashCommands: true` -- the text arrives as literal characters. So a
# session cannot clear itself and cannot be told to, by anyone.
#
# Respawning WITHOUT --continue is therefore the only mechanical clear we have.
# It is not destructive: the previous conversation stays on disk and is
# resumable with /resume. What it costs is the session's working context, so
# this is right for assistants that should start each task clean and wrong for
# a builder mid-goal -- use --exclude for anything in flight.
FORCE_FRESH = False


def fresh_start_paths() -> set:
    """Realpaths of registry projects marked `fresh_start: true`.

    Per-agent rather than global, because the whole point is that assistants
    clear at task boundaries while builders keep their history. A project with
    no `fresh_start` field keeps the existing --continue behaviour.
    """
    out = set()
    for fields in parse_registry(REGISTRY_PATH).values():
        # The registry parser does not strip inline comments, so a field
        # written `fresh_start: true   # why` arrives with the comment still
        # attached. Compare on the first token, never on the whole value.
        if fields.get("fresh_start", "").split("#")[0].strip().lower() != "true":
            continue
        raw = fields.get("path", "")
        if raw:
            out.add(os.path.realpath(os.path.expanduser(raw)))
    return out


def wants_fresh(path: str) -> bool:
    """True if this session should come back with empty context."""
    if FORCE_FRESH:
        return True
    return os.path.realpath(path) in fresh_start_paths()


def collect_targets() -> List[Tuple[str, str]]:
    """Return list of (session_name, expanded_path) for projects with respawn: true."""
    projects = parse_registry(REGISTRY_PATH)
    targets: List[Tuple[str, str]] = []
    for name, fields in projects.items():
        if fields.get("respawn", "").lower() != "true":
            continue
        raw_path = fields.get("path", "")
        if not raw_path:
            print(f"[skip] {name}: no path field", file=sys.stderr)
            continue
        path = os.path.expanduser(raw_path)
        if not os.path.isdir(path):
            print(f"[skip] {name}: path missing on disk: {path}", file=sys.stderr)
            continue
        session_name = fields.get("session_name", "") or humanize(name)
        targets.append((session_name, path))
    return targets


# --- claude process discovery ------------------------------------------------

def get_running_claude_processes() -> Dict[int, Dict[str, str]]:
    """Return {pid: {"cwd": str, "argv": str}} for each top-level claude
    process currently running. Filters strictly to processes whose first
    argv element basename is exactly `claude`.
    """
    out: Dict[int, Dict[str, str]] = {}
    try:
        ps_out = subprocess.run(
            ["ps", "-axww", "-o", "pid=,command="],
            capture_output=True, text=True, timeout=PS_TIMEOUT_SEC,
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return out

    pids: List[Tuple[int, str]] = []
    for line in ps_out.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\s+(.+)$", line)
        if not m:
            continue
        pid, cmd = int(m.group(1)), m.group(2).strip()
        if not cmd:
            continue
        first_arg = cmd.split()[0]
        if os.path.basename(first_arg) == "claude":
            pids.append((pid, cmd))

    if not pids:
        return out

    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", ",".join(str(p[0]) for p in pids), "-d", "cwd", "-Fpn"],
            capture_output=True, text=True, timeout=LSOF_TIMEOUT_SEC,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return out

    pid_to_cwd: Dict[int, str] = {}
    cur_pid: Optional[int] = None
    for ln in result.stdout.split("\n"):
        if ln.startswith("p") and len(ln) > 1:
            try:
                cur_pid = int(ln[1:])
            except ValueError:
                cur_pid = None
        elif ln.startswith("n") and len(ln) > 1 and cur_pid is not None:
            cwd = ln[1:].strip()
            if cwd:
                try:
                    pid_to_cwd[cur_pid] = os.path.realpath(cwd)
                except Exception:
                    pid_to_cwd[cur_pid] = cwd

    for pid, cmd in pids:
        out[pid] = {"cwd": pid_to_cwd.get(pid, ""), "argv": cmd}
    return out


def _tmux_session_for_pids() -> Dict[int, str]:
    """Map every pid living under a tmux pane -> that pane's session name.

    Walks each claude's parent chain rather than matching pane_pid directly,
    because the pane's own process is the `zsh -ic` wrapper, not claude.
    """
    r = subprocess.run([TMUX_BIN, "list-panes", "-a", "-F", "#{session_name}|#{pane_pid}"],
                       capture_output=True, text=True, timeout=3.0)
    pane_owner: Dict[int, str] = {}
    for ln in r.stdout.splitlines():
        if "|" in ln:
            sess, pid = ln.rsplit("|", 1)
            try:
                pane_owner[int(pid)] = sess
            except ValueError:
                pass
    return pane_owner


def _ancestor_tmux_session(pid: int, pane_owner: Dict[int, str]) -> Optional[str]:
    cur = pid
    for _ in range(8):
        if cur in pane_owner:
            return pane_owner[cur]
        try:
            r = subprocess.run(["ps", "-p", str(cur), "-o", "ppid="],
                               capture_output=True, text=True, timeout=2.0)
            cur = int(r.stdout.strip() or "0")
        except Exception:
            return None
        if cur <= 1:
            return None
    return None


def resolve_home_path(cwd: str) -> str:
    """Return the path a session must be respawned from to resume its own history.

    Claude Code keys transcripts on the cwd it was launched from, and a worktree
    session does NOT reliably get its own key: one project's worktree session
    stores its transcript under the MAIN repo's encoding, while another's
    stores it under the worktree's. Respawning at the wrong one makes
    `--continue` find nothing and silently start a BRAND NEW session — the
    running one's entire context, gone with no error.

    So don't guess from the shape of the path: walk up from the cwd and respawn
    at the first ancestor that actually has a transcript.
    """
    p = os.path.realpath(cwd)
    stop = os.path.realpath(os.path.expanduser("~/dev"))
    while True:
        if has_prior_session(p):
            return p
        parent = os.path.dirname(p)
        if parent == p or p == stop or not p.startswith(stop):
            return os.path.realpath(cwd)
        p = parent


_REGISTRY_PATHS_CACHE: Optional[List[str]] = None


def registry_home_for(path: str) -> Optional[str]:
    """Return the registry path of the project that CONTAINS `path`, if any.

    A peer's home is its registry path; a worktree is temporary scratch. Nothing
    else in this script enforces that, and `resolve_home_path` above cannot: it
    is defined to find where the transcript LIVES, so if a session relocated its
    own transcript into a worktree, the walk stops there on the first iteration
    and faithfully returns the worktree. Both behaviours are correct in
    isolation and the outcome — a peer staying at its registered home — was
    owned by no step, so a misplacement got copied forward on every respawn.
    Measured 2026-08-17: a peer had lived in a doubly-nested worktree since
    09:20 and the account-switch respawn reproduced it exactly.

    This does the ownership half. It deliberately does NOT relocate anything:
    moving a transcript is how the misplacement happened in the first place, and
    respawning at a path whose transcript is elsewhere silently starts a blank
    session. It reports, and a human decides.

    Matches every registry project, not just `respawn: true` ones — a peer with
    `respawn: false` is exactly the kind that gets cycled by `--mode running`.
    """
    global _REGISTRY_PATHS_CACHE
    if _REGISTRY_PATHS_CACHE is None:
        paths = []
        for fields in parse_registry(REGISTRY_PATH).values():
            raw = fields.get("path", "")
            if not raw:
                continue
            p = os.path.realpath(os.path.expanduser(raw))
            if os.path.isdir(p):
                paths.append(p)
        # Longest first so a nested project wins over its parent.
        _REGISTRY_PATHS_CACHE = sorted(paths, key=len, reverse=True)
    real = os.path.realpath(path)
    for project in _REGISTRY_PATHS_CACHE:
        if real == project or real.startswith(project + os.sep):
            return project
    return None


def collect_running_targets(running: Dict[int, Dict[str, str]],
                            self_pid: Optional[int]) -> List[Tuple[str, str]]:
    """Return (display_name, cwd) for every live claude except the team-lead.

    Registry-independent by design. `--mode all` respawns what the registry
    SAYS should be up, at the registry's canonical path — which is what you
    want for a fleet reset. An account switch is the opposite problem: you need
    every session that is *actually* running to cycle, including ones with
    `respawn: false`, ones in a worktree, and ones the registry never got.
    Missing any of them leaves that peer bound to the old account's MCP
    handshakes and running on the previous session's env.

    Order puts a Discord-bearing session LAST so it starts alone, after the
    fleet has settled — its gateway handshake is the one that loses the
    startup race (2026-07-24).
    """
    pane_owner = _tmux_session_for_pids()
    targets: List[Tuple[str, str]] = []
    for pid, info in running.items():
        if self_pid is not None and pid == self_pid:
            continue
        # Other OS users' claude sessions are not ours to touch.
        if not info["argv"].startswith(os.path.expanduser("~/.local/bin/claude")):
            continue
        cwd = info["cwd"]
        if not cwd or not os.path.isdir(cwd):
            print(f"[skip] PID {pid}: cwd missing on disk ({cwd or '?'})", file=sys.stderr)
            continue
        # Stop at the next flag, not at end-of-line. `-n` is not necessarily the
        # last argument: spawn_session_tmux appends direct_channel_flags AFTER
        # it, so a greedy match swallowed them into the display name. That name
        # becomes the tmux session name, the -n display name, and
        # FEEDBACK_AGENT_NAME -- so the corrupted value meant the pre-existing
        # tmux session was NOT matched and killed, and the respawn produced a
        # SECOND session in the same cwd (two writers, colliding stable_ids)
        # with a garbage agent name and the channel flag applied twice.
        # Only sessions carrying a direct channel registration were affected,
        # which is why it survived: the dry run prints the name, and every
        # session that had ever been checked had -n last.
        m = re.search(r"\s-n\s+(.+?)(?=\s+--|$)", info["argv"])
        display = m.group(1).strip() if m else (
            _ancestor_tmux_session(pid, pane_owner) or humanize(os.path.basename(cwd)))
        home = resolve_home_path(cwd)
        if home != cwd:
            print(f"[home] {display}: running in {cwd}\n"
                  f"       but its transcript lives under {home} — respawning there "
                  f"so --continue resumes it.")
        registry_home = registry_home_for(home)
        if registry_home and registry_home != os.path.realpath(home):
            print(f"[misplaced] {display}: home is {home}\n"
                  f"            but its registry path is {registry_home}.\n"
                  f"            Respawning where it is, so --continue keeps its context —\n"
                  f"            but this session lives in a worktree and every respawn will\n"
                  f"            reproduce that until its transcript is migrated. See\n"
                  f"            'Misplaced session home' in the respawn-sessions SKILL.md.",
                  file=sys.stderr)
        targets.append((display, home))

    targets.sort(key=lambda t: discord_state_dir_for(t[1]) != NO_DISCORD_STATE_DIR)
    return targets


def get_self_pid() -> Optional[int]:
    """Walk parent chain to find the team-lead's claude PID, so we never kill it."""
    pid = os.getppid()
    for _ in range(20):
        if pid <= 1:
            return None
        try:
            r = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                               capture_output=True, text=True, timeout=2.0)
            cmd = r.stdout.strip()
            if cmd and os.path.basename(cmd.split()[0]) == "claude":
                return pid
            r = subprocess.run(["ps", "-p", str(pid), "-o", "ppid="],
                               capture_output=True, text=True, timeout=2.0)
            pid = int(r.stdout.strip() or "0")
        except Exception:
            return None
    return None


# --- tmux spawning + interaction ---------------------------------------------

def has_prior_session(path: str) -> bool:
    # Use realpath so a symlinked ~/dev (e.g., ~/dev -> /Volumes/Data/Users/...)
    # encodes to the same dir name Claude Code uses for transcripts when started
    # from a symlinked cwd. Without this, has_prior_session would miss every
    # session and we'd fall back to plain `claude` (no --continue).
    real = os.path.realpath(path)
    encoded = real.replace("/", "-").replace("_", "-").replace(".", "-")
    transcript_dir = os.path.join(os.path.expanduser("~"), ".claude", "projects", encoded)
    if not os.path.isdir(transcript_dir):
        return False
    for entry in os.listdir(transcript_dir):
        if entry.endswith(".jsonl"):
            return True
    return False


def to_tmux_session_name(display_name: str) -> str:
    """Convert 'My Project' -> 'my-project'. tmux session names should be
    shell-friendly: lowercase + spaces/underscores -> hyphens, drop leading/trailing -."""
    s = display_name.lower().replace(" ", "-").replace("_", "-")
    return s.strip("-")


def spawn_session_tmux(session_name: str, path: str) -> bool:
    """Spawn claude in a detached tmux session via interactive zsh so the
    `claude` shell function in ~/.zshrc applies (channel + dev-channel flags).
    Returns True on success.

    Critical: bare /Users/.../bin/claude does NOT pick up the channel flags.
    Bryan's ~/.zshrc defines `claude()` as a function, not an alias, so it
    only loads when zsh sources .zshrc — i.e., interactive shell (-i).

    DISCORD_STATE_DIR is scoped per-session via `tmux new-session -e` so the
    peer doesn't accidentally inherit the team-lead's discord state (which
    would cause one Discord post to fan out to every peer).

    FEEDBACK_AGENT_NAME rides the same mechanism. Live-feedback attributes a
    peer's comments and suggested edits to this name with a stable colour;
    without it every peer posts as the shared "Agent" and a thread with three
    participants is unreadable. An agent CANNOT set this for itself — the
    plugin reads it at launch — so it has to come from the launcher, which is
    here. `session_name` is already the friendly display name the registry
    carries for each project, so it is the right value with no new field.

    Note the reconnect/restart split: an MCP reconnect re-spawns the child and
    picks up new tool schemas, but the child inherits the PARENT session's env,
    which was fixed at session launch. So a reconnected peer can have a new
    plugin's tools while still reporting the old (or no) agent name. Only a
    full session restart delivers both."""
    tmux_name = to_tmux_session_name(session_name)
    # Idempotent: kill any pre-existing tmux session with the same name
    subprocess.run([TMUX_BIN, "kill-session", "-t", tmux_name],
                   capture_output=True, timeout=3.0)
    # TWO names, and NEITHER flag reliably names the session on the phone.
    #
    # `-n <name>` is the LOCAL display name only -- pane title, agent picker,
    # terminal title. `claude --help` says exactly that, and an older comment here
    # claiming it also covered Remote Control cost a fleet that came back on the
    # phone as a set of unnamed strangers (`bryans-mac-mini-ticklish-pie` and
    # friends) sitting beside the previous, still-correctly-named records. The
    # pane read the right name the whole time, so from the terminal it looked fine.
    #
    # `--remote-control <name>` is documented to take a name and MEASURED NOT TO
    # APPLY IT (2026-08-11): a session launched with
    # `--remote-control 'ADFA 4128 Quick Build'` registered as
    # `bryans-mac-mini-abstract-sunbeam`. Proof is the RC title for the session id
    # printed in that pane -- not the argv, which of course shows what we passed.
    # Untested whether a name with spaces is what gets rejected; single-token
    # names were never isolated. `/remote-control` offers no rename either
    # (Disconnect / QR / Continue only), so the ONLY rename today is by hand in
    # the claude.ai session list.
    #
    # Keep the flag anyway, for the half that DOES work: it connects RC at launch.
    # Without it every respawned session came up `/rc failed` and had to be
    # reconnected by hand before it appeared on the phone at all.
    #
    # Consequence to warn about before any respawn: a restart always creates a
    # NEW RC record with a NEW auto name, so it discards whatever the operator
    # renamed that row to.
    name_flag = f" -n {shlex.quote(session_name)}"
    rc_flag = f" --remote-control {shlex.quote(session_name)}"
    # A fresh session is spawned as bare `claude`. Note this also skips the
    # resume-from-summary dialog, so the post-spawn auto-accept has one fewer
    # prompt to answer -- it polls for whatever appears and is unbothered.
    fresh = wants_fresh(path)
    claude_invocation = (
        ("claude --continue" if (has_prior_session(path) and not fresh) else "claude")
        + name_flag
        + rc_flag
        + direct_channel_flags(path)
    )

    # Scope DISCORD_STATE_DIR per-peer to prevent discord-channel fan-out.
    ensure_no_discord_state()
    discord_dir = discord_state_dir_for(path)

    # `zsh -ic` sources ~/.zshrc and runs the inline command. The shell function
    # `claude` resolves to the full binary path + channel flags inside zsh.
    # `-e DISCORD_STATE_DIR=…` overrides the tmux server's inherited env for
    # this session only (the server may already be running with a different value).
    r = subprocess.run(
        [TMUX_BIN, "new-session", "-d", "-s", tmux_name, "-c", path,
         "-e", f"DISCORD_STATE_DIR={discord_dir}",
         # Both spellings. The plugin dual-reads permanently and lets the
         # new name win, so setting both is correct before AND after the
         # rename -- no flag-day ordering dependency on this file.
         "-e", f"CW_AGENT_NAME={session_name}",
         "-e", f"FEEDBACK_AGENT_NAME={session_name}",
         "/bin/zsh", "-ic", claude_invocation],
        capture_output=True, text=True, timeout=5.0,
    )
    if r.returncode != 0:
        print(f"  [warn] tmux spawn failed for {tmux_name}: {r.stderr.strip()}", file=sys.stderr)
        return False
    return True


# A live, ready-for-input session draws this footer under its prompt box. No
# startup dialog draws it — dialogs take the screen. Its presence is the signal
# that the dialog phase is over and the poller must stop sending Enter.
LIVE_PROMPT_MARKERS = [
    "auto mode on",
    "shift+tab to cycle",
    "bypass permissions on",
]

# How many trailing lines of the pane count as "where a dialog would be".
# Anything above this is scrollback — history, not a live prompt.
DIALOG_REGION_LINES = 20


def session_is_live(pane: str) -> bool:
    """True when the pane shows a session ready for input, not a dialog."""
    tail = "\n".join(pane.rstrip().splitlines()[-6:])
    return any(m in tail for m in LIVE_PROMPT_MARKERS)


def dialog_region(pane: str) -> str:
    """Only the bottom of the pane, where an ACTIVE dialog is drawn."""
    return "\n".join(pane.rstrip().splitlines()[-DIALOG_REGION_LINES:])


def tmux_capture_pane(session: str) -> str:
    r = subprocess.run([TMUX_BIN, "capture-pane", "-t", session, "-p"],
                       capture_output=True, text=True, timeout=3.0)
    return r.stdout if r.returncode == 0 else ""


def tmux_send_keys(session: str, *keys: str) -> bool:
    r = subprocess.run([TMUX_BIN, "send-keys", "-t", session, *keys],
                       capture_output=True, timeout=3.0)
    return r.returncode == 0


def tmux_send_enter(session: str) -> bool:
    return tmux_send_keys(session, "Enter")


def auto_accept_dialogs_tmux(session_names: List[str],
                             no_compact: bool = False) -> Dict[str, int]:
    """Poll each tmux session for known startup dialogs and dismiss them by
    sending Enter (which accepts the safe default in every dialog we know about:
    "resume from summary", "use this MCP server", "trust this folder").

    With no_compact=True, the resume dialog is answered with Down+Enter instead
    — selecting "Resume full session as-is" rather than the default "Resume from
    summary", so the peer comes back with its full context intact.

    Polls up to DIALOG_POLL_MAX_ITER * DIALOG_POLL_INTERVAL_SEC seconds total.
    Returns {session: enters_sent}."""
    sent: Dict[str, int] = {s: 0 for s in session_names}
    pending = set(session_names)
    for _ in range(DIALOG_POLL_MAX_ITER):
        if not pending:
            break
        time.sleep(DIALOG_POLL_INTERVAL_SEC)
        still_pending = set()
        for s in pending:
            content = tmux_capture_pane(s)
            # STOP as soon as the session is live, whatever the scrollback says.
            # A dismissed dialog's text stays visible in the pane, so matching
            # DIALOG_PATTERNS against the whole capture keeps returning True
            # long after the dialog is gone — the poller then sends Enter every
            # DIALOG_POLL_INTERVAL_SEC for the rest of the window. On a resumed
            # session that pre-fills `/compact` in its input box, each surplus
            # Enter SUBMITS it: 2-4 queued compactions in 5 of 10 sessions on
            # 2026-08-03, defeating the --no-compact flag that was passed
            # specifically to preserve context.
            #
            # Same root error as the fleet's killer item: a pane is a render,
            # not state. Scrollback is history, not a live dialog.
            if session_is_live(content):
                continue  # drop from pending permanently
            if any(p in dialog_region(content) for p in DIALOG_PATTERNS):
                is_resume = any(p in dialog_region(content)
                                for p in RESUME_DIALOG_PATTERNS)
                ok = (tmux_send_keys(s, "Down", "Enter")
                      if (no_compact and is_resume)
                      else tmux_send_enter(s))
                if ok:
                    sent[s] += 1
                still_pending.add(s)  # dialog may chain into another dialog
            else:
                still_pending.add(s)  # not live yet, still booting
        pending = still_pending
    return sent


def mcp_health_report() -> List[str]:
    """List which plugin MCP server processes exist under each claude session.

    READ THIS BEFORE TRUSTING THE OUTPUT: presence in this list is NOT evidence that
    a plugin's tools registered. It is only evidence that a process was started.

    When Claude fails to complete the MCP handshake with a plugin server, the child
    process is still spawned and still sits there — it just never gets spoken to. From
    outside, a broken server is INDISTINGUISHABLE from a healthy one:

      - CPU time?  No. A connected Discord gateway heartbeats at ~0.01s CPU, same as a
                   process that did nothing. (Tried it; false-positived the healthy peer.)
      - Sockets?   No. The healthy Discord child held zero ESTABLISHED sockets too.
      - Logs?      No. The failure is completely silent — no error, no warning, nothing.

    This is exactly how a peer lost its Discord channel for three days while looking
    perfectly healthy from every external angle (2026-07-13): process up, config valid,
    token valid, no errors — and simply unreachable.

    **The only reliable check is from INSIDE the session: ask the peer whether its tools
    actually surfaced.** Use this report to see what SHOULD be there, then confirm.
    """
    lines: List[str] = []
    try:
        ps = subprocess.run(["ps", "-axww", "-o", "pid=,ppid=,time=,command="],
                            capture_output=True, text=True, timeout=PS_TIMEOUT_SEC).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return lines

    kids: List[tuple] = []
    for row in ps.splitlines():
        f = row.split(None, 3)
        if len(f) < 4:
            continue
        try:
            pid, ppid = int(f[0]), int(f[1])
        except ValueError:
            continue
        kids.append((pid, ppid, f[2], f[3]))

    claude = get_running_claude_processes()   # {pid: {"cwd", "argv"}}

    for cpid in sorted(claude):
        cwd = claude[cpid].get("cwd") or "?"
        name = os.path.basename(cwd.rstrip("/")) or str(cpid)
        found = [label
                 for label, marker in EXPECTED_MCP_MARKERS.items()
                 if any(ppid == cpid and marker in cmd for (_p, ppid, _t, cmd) in kids)]
        if found:
            lines.append(f"  {name:<24} started: {', '.join(found)}")
    return lines


# --- destructive ops ---------------------------------------------------------

def kill_claude(pid: int) -> bool:
    """SIGTERM then SIGKILL the given claude PID. Returns True if killed."""
    try:
        subprocess.run(["kill", str(pid)], capture_output=True, timeout=3.0)
    except Exception:
        pass
    time.sleep(1.5)
    try:
        r = subprocess.run(["ps", "-p", str(pid), "-o", "pid="], capture_output=True, text=True, timeout=2.0)
        if r.stdout.strip():
            subprocess.run(["kill", "-9", str(pid)], capture_output=True, timeout=3.0)
            time.sleep(0.5)
    except Exception:
        pass
    try:
        r = subprocess.run(["ps", "-p", str(pid), "-o", "pid="], capture_output=True, text=True, timeout=2.0)
        return not r.stdout.strip()
    except Exception:
        return True


def sweep_orphan_hive_servers() -> int:
    """Kill any `bun … claude-hive-mcp/server.ts` process whose PPID is 1
    (re-parented to launchd after its parent claude died). Returns kill count."""
    try:
        ps_out = subprocess.run(
            ["ps", "-axww", "-o", "pid=,ppid=,command="],
            capture_output=True, text=True, timeout=PS_TIMEOUT_SEC,
        ).stdout
    except Exception:
        return 0
    killed = 0
    for line in ps_out.split("\n"):
        if "claude-hive-mcp/server.ts" not in line:
            continue
        m = re.match(r"^\s*(\d+)\s+(\d+)\s+", line)
        if not m:
            continue
        pid, ppid = int(m.group(1)), int(m.group(2))
        if ppid != 1:
            continue
        try:
            subprocess.run(["kill", "-9", str(pid)], capture_output=True, timeout=2.0)
            killed += 1
        except Exception:
            pass
    return killed


# --- target selection by mode -----------------------------------------------

def select_targets(mode: str, targets: List[Tuple[str, str]],
                   running: Dict[int, Dict[str, str]],
                   self_pid: Optional[int]) -> Tuple[List[Tuple[str, str]], List[int], List[Tuple[str, str]]]:
    """Return (to_spawn, to_kill, skipped_already_running).

    `to_spawn` is the list of (session_name, path) we'll launch new tabs for.
    `to_kill` is the list of PIDs we'll terminate before spawning.
    `skipped_already_running` is informational — projects we left alone.
    """
    # Map cwd → pid for direct path match.
    cwd_to_pid: Dict[str, int] = {}
    for pid, info in running.items():
        if info["cwd"]:
            cwd_to_pid[info["cwd"]] = pid

    def find_claude_for_project(project_path: str) -> Optional[int]:
        """Return the PID of a claude whose cwd is the project_path itself OR
        a subdirectory of it (e.g. a `.claude/worktrees/<branch>` worktree).
        Treats a worktree claude as "the project is alive" — see
        `feedback_respawn_scope.md` for the bug this avoids."""
        # Direct match wins.
        if project_path in cwd_to_pid:
            return cwd_to_pid[project_path]
        # Otherwise check for any cwd under the project path.
        prefix = project_path.rstrip("/") + "/"
        for cwd, pid in cwd_to_pid.items():
            if cwd.startswith(prefix):
                return pid
        return None

    to_spawn: List[Tuple[str, str]] = []
    to_kill: List[int] = []
    skipped: List[Tuple[str, str]] = []

    for name, path in targets:
        normalized = os.path.realpath(path)
        existing_pid = find_claude_for_project(normalized)

        if mode == "missing":
            if existing_pid is not None:
                skipped.append((name, path))
            else:
                to_spawn.append((name, path))
        elif mode in ("all", "running"):
            if existing_pid == self_pid and self_pid is not None:
                skipped.append((name, path))
            else:
                if existing_pid is not None:
                    to_kill.append(existing_pid)
                to_spawn.append((name, path))
        elif mode == "plugin":
            if existing_pid is None:
                to_spawn.append((name, path))
            elif existing_pid == self_pid:
                # Team Lead: never kill ourselves; flag if we lack the plugin
                if not has_fleet_plugin(running[existing_pid]["argv"]):
                    print(f"  [self] {name}: team-lead lacks plugin — restart this session manually to upgrade")
                skipped.append((name, path))
            elif has_fleet_plugin(running[existing_pid]["argv"]):
                skipped.append((name, path))
            else:
                to_kill.append(existing_pid)
                to_spawn.append((name, path))
        else:
            sys.exit(f"unknown mode: {mode}")

    return to_spawn, to_kill, skipped


# --- entrypoint -------------------------------------------------------------

HELP_TEXT = """\
respawn.py — Respawn Bryan's long-running Claude Code sessions in tmux.

Usage:
  python3 respawn.py [--mode MODE] [--execute] [--no-auto-accept]

Modes:
  --mode missing   (default) Spawn only for projects whose claude isn't running.
  --mode plugin    Kill+respawn any session lacking the fleet workspaces
                   plugin (canonical fleet plugin). Team Lead (self) is never
                   killed; flagged if it lacks the plugin.
  --mode all       Kill+respawn every respawn=true session, except the
                   team-lead (self).
  --mode running   Kill+respawn every session that is ACTUALLY running, at its
                   own cwd, ignoring the registry. This is the account-switch
                   mode: after Bryan runs /login, every session must restart or
                   it keeps the old account's MCP handshakes and the previous
                   session's env (compaction ceilings included). `--mode all`
                   is not a substitute — it misses respawn:false sessions and
                   relocates worktree sessions to the registry path.

Flags:
  --only <substr>  (repeatable) Restrict the mode to matching targets. Matches
                   the display name and the path, case-insensitively. Use it to
                   respawn ONE session — the peer the user is about to work in,
                   or a session that needs a solo retry after losing its
                   channels to the MCP handshake race.
  --exclude <substr> (repeatable) Leave a matching session alone even though the
                   mode would restart it. For peers that are mid-flight.
  --execute        Actually perform kills/spawns. Without this it's dry-run.
  --no-auto-accept After spawning, skip the post-spawn cleanup pass:
                     - polling new tmux panes for startup dialogs and sending
                       Enter to dismiss them (dev-channel, MCP approval,
                       resume-from-summary),
                     - sweeping orphan claude-hive-mcp servers.
                   Use this if you want to walk through panes by hand.
  --no-compact     Answer the resume dialog with "Resume full session as-is"
                   instead of the default "Resume from summary". Peers come back
                   with their full context intact — nothing is summarized away.
                   Default (without this flag) is to compact on resume.

Behavior:
  - Reads ~/dev/ai-team-lead/registry.yaml and acts on every project
    with `respawn: true`.
  - Default behavior is safe (dry-run + missing-only). The destructive options
    require both --mode and --execute.
  - Team Lead's own claude session is identified by walking the parent process
    chain and is never killed.
  - Always launches via tmux. Names sessions after the project (lowercase +
    hyphens). Attach later with `tmux a -t <session>`. The `zsh -ic` form
    is required: bare `claude` does NOT pick up the channel/plugin flags
    because `claude` is a zsh function in ~/.zshrc, not an alias.
"""


def main() -> int:
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(HELP_TEXT)
        return 0

    execute = "--execute" in args
    auto_accept = "--no-auto-accept" not in args
    no_compact = "--no-compact" in args

    # --fresh: force EVERY spawned session to start with empty context, on top
    # of whatever the registry marks. Use it for a deliberate fleet-wide reset;
    # for the normal per-agent behaviour set `fresh_start: true` in the
    # registry instead and let this stay off.
    global FORCE_FRESH
    FORCE_FRESH = "--fresh" in args

    mode = "missing"
    if "--mode" in args:
        idx = args.index("--mode")
        if idx + 1 >= len(args):
            sys.exit("--mode requires a value (missing|plugin|all|running)")
        mode = args[idx + 1]

    # --exclude <substring> (repeatable): leave a session alone even though the
    # mode would otherwise restart it. Needed because a peer mid-flight should
    # not be killed for a fleet-wide config rollout — the rollout can wait for
    # it, and killing it costs whatever it had not checkpointed. Matches
    # case-insensitively against both the display name and the path, so
    # `--exclude live-feedback` catches "Live Feedback" and its repo path.
    excludes: List[str] = []
    for i, a in enumerate(args):
        if a == "--exclude":
            if i + 1 >= len(args):
                sys.exit("--exclude requires a value (session name or path substring)")
            excludes.append(args[i + 1].lower())

    # --only <substring> (repeatable): restrict the mode to these targets.
    #
    # This is how you respawn ONE session, and it exists because the two
    # situations that most need a single restart are the two where a fleet-wide
    # pass does damage. A peer the user is about to work in should come back
    # first rather than in stagger order; and a session that lost its channels
    # to the MCP handshake race has to be retried alone, since retrying it
    # inside another simultaneous wave just re-runs the race that broke it.
    # Before this flag both were done by hand, which is how a worktree session
    # gets respawned at the wrong path and silently starts blank.
    #
    # Matches case-insensitively against the display name and the path, same as
    # --exclude. With --mode running the path is the session's live cwd, so a
    # worktree substring selects the worktree session specifically.
    onlys: List[str] = []
    for i, a in enumerate(args):
        if a == "--only":
            if i + 1 >= len(args):
                sys.exit("--only requires a value (session name or path substring)")
            onlys.append(args[i + 1].lower())

    running = get_running_claude_processes()
    self_pid = get_self_pid()

    if mode == "running":
        if self_pid is None:
            print("\n[abort] --mode running kills every live session except the "
                  "team-lead, and get_self_pid() returned None — it cannot tell "
                  "which one is itself. Run from inside a claude session.",
                  file=sys.stderr)
            return 2
        targets = collect_running_targets(running, self_pid)
        if not targets:
            print("No running peer sessions found.", file=sys.stderr)
            return 1
    else:
        targets = collect_targets()
        if not targets:
            print("No projects with respawn: true found in registry.yaml", file=sys.stderr)
            return 1

    print(f"Mode: {mode}")
    print(f"{len(targets)} target(s) "
          f"{'from the live process table' if mode == 'running' else 'from registry.yaml'}.")
    print(f"Detected {len(running)} running Claude Code session(s); self_pid={self_pid}")

    # Self-protection: --mode all and --mode plugin can kill peers. If we
    # can't identify the team-lead's own PID, refuse to proceed in those
    # modes — the team-lead is in registry.yaml as `respawn: true` itself,
    # so without self_pid the kill loop would target it.
    if mode in ("all", "plugin") and self_pid is None:
        print(
            f"\n[abort] --mode {mode} requires identifying the team-lead's "
            "own claude PID via parent-process walk, but get_self_pid() "
            "returned None. Refusing to proceed because the team-lead is in "
            "registry.yaml with respawn:true and would be killed.\n"
            "Run from inside a claude session, or use --mode missing.",
            file=sys.stderr,
        )
        return 2

    if onlys:
        def is_only(name: str, path: str) -> bool:
            hay = f"{name} {path}".lower()
            return any(x in hay for x in onlys)
        kept = [t for t in targets if is_only(t[0], t[1])]
        if not kept:
            print(f"\n[abort] --only {onlys} matched none of the "
                  f"{len(targets)} target(s). Matched against display name and "
                  "path; run without --execute to see the target list.",
                  file=sys.stderr)
            return 1
        for name, path in kept:
            print(f"  [only] {name} — {path}")
        targets = kept

    if excludes:
        def is_excluded(name: str, path: str) -> bool:
            hay = f"{name} {path}".lower()
            return any(x in hay for x in excludes)
        kept = [t for t in targets if not is_excluded(t[0], t[1])]
        dropped = [t for t in targets if is_excluded(t[0], t[1])]
        for name, path in dropped:
            print(f"  [exclude] {name} — left running by request")
        if not kept:
            print("\n[abort] every target was excluded; nothing to do.", file=sys.stderr)
            return 1
        targets = kept

    to_spawn, to_kill, skipped = select_targets(mode, targets, running, self_pid)

    if skipped:
        print(f"\nLeaving alone ({len(skipped)}):")
        for name, path in skipped:
            print(f"  ✓ {name:25s}  {path}")

    if to_kill:
        print(f"\nWill KILL ({len(to_kill)} PIDs):")
        for pid in to_kill:
            info = running.get(pid, {})
            print(f"  - PID {pid}  cwd={info.get('cwd','?')}")

    if to_spawn:
        print(f"\nWill SPAWN ({len(to_spawn)}):")
        for name, path in to_spawn:
            print(f"  + {name:25s}  {path}")
    else:
        print("\nNothing to spawn.")

    if not (to_kill or to_spawn):
        return 0

    if not execute:
        print(f"\nDRY RUN — pass --execute to do it for real.")
        return 0

    # ---- destructive phase ----

    if to_kill:
        print("\n[kill] Terminating sessions...")
        for pid in to_kill:
            info = running.get(pid, {})
            if kill_claude(pid):
                print(f"  killed PID {pid}  cwd={info.get('cwd','?')}")
            else:
                print(f"  WARN: PID {pid} may still be alive")
        time.sleep(2.0)

    spawn_failures = 0
    spawned_tmux_sessions: List[str] = []

    if to_spawn:
        print(f"\n[spawn] Creating {len(to_spawn)} detached tmux session(s) via `zsh -ic` (so the `claude` shell function applies channel flags)...")
        for i, (name, path) in enumerate(to_spawn):
            # Stagger: never start two sessions' plugin MCP servers at once.
            # See SPAWN_STAGGER_SEC — simultaneous starts lose the handshake race
            # and silently strip channels off peers.
            if i > 0:
                # A Discord-bearing session has a gateway handshake to win on top
                # of the usual plugin MCP handshakes, and it is the one observed
                # losing the race (2026-07-13, 2026-07-24). Give it a quiet start.
                has_discord = discord_state_dir_for(path) != NO_DISCORD_STATE_DIR
                time.sleep(DISCORD_STAGGER_SEC if has_discord else SPAWN_STAGGER_SEC)
            if spawn_session_tmux(name, path):
                tmux_name = to_tmux_session_name(name)
                spawned_tmux_sessions.append(tmux_name)
                print(f"  spawned tmux:{tmux_name}  cwd={path}")
            else:
                spawn_failures += 1

    if not auto_accept:
        print("\n[done] --no-auto-accept set; skipping post-spawn cleanup.")
        return 1 if spawn_failures else 0

    # ---- post-spawn auto-accept + cleanup ----

    if spawned_tmux_sessions:
        resume_mode = ("full session as-is (--no-compact)" if no_compact
                       else "from summary (compacts)")
        print(f"\n[accept] Polling {len(spawned_tmux_sessions)} tmux session(s) for startup dialogs "
              f"(MCP approval, dev-channel; resume -> {resume_mode})...")
        sent = auto_accept_dialogs_tmux(spawned_tmux_sessions, no_compact=no_compact)
        for s, n in sent.items():
            if n:
                print(f"  keys x{n} -> tmux:{s}")

    # Post-spawn MCP health. "The process is running" is NOT evidence a plugin's
    # tools registered — a never-handshaken MCP server looks identical to a healthy
    # one from the process table. Report what each server has actually DONE.
    if spawned_tmux_sessions:
        print("\n[health] Plugin MCP servers STARTED per session:")
        for line in mcp_health_report():
            print(line)
        print("\n  ⚠ This proves a process started — NOT that its tools registered.")
        print("    A failed MCP handshake leaves the child process running and silent:")
        print("    no error, no log, same CPU, same sockets as a healthy one. A peer in")
        print("    that state looks fine from every external angle and is simply unreachable")
        print("    on its channel. (Cost us a dead Discord channel for 3 days, 2026-07-13.)")
        print("\n    VERIFY FROM INSIDE: ask each peer whether its tools actually surfaced")
        print("    (e.g. hive-message it: \"do you have your discord / live-feedback tools?\").")
        print("    That is the only check that means anything. Do not skip it.")

    # Sweep orphan hive servers (only relevant if we killed something).
    if to_kill:
        print("\n[sweep] Killing orphan claude-hive-mcp servers (PPID=1)...")
        n = sweep_orphan_hive_servers()
        print(f"  killed {n} orphan(s)")

    print("\nDone." if not spawn_failures else f"\nDone with {spawn_failures} spawn failure(s).")
    return 1 if spawn_failures else 0


if __name__ == "__main__":
    sys.exit(main())
