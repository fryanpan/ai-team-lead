#!/usr/bin/env python3
"""
Respawn Bryan's long-running Claude Code sessions in detached tmux sessions.

Reads `registry.yaml` from the parent ai-team-lead repo. For each
project with `respawn: true`, depending on --mode:
  - `missing` (default): spawn only if no claude is running with that cwd.
  - `plugin`: kill + respawn any session that does not have the
    `claude-live-feedback` plugin loaded (canonical fleet plugin).
  - `all`: kill + respawn every respawn=true session (skipping the team-lead's
    own claude — never kill self).

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

Safety: this script is **dry-run by default**. Pass `--execute` to actually
do anything destructive (kill/spawn/sweep). Without `--execute` it just
prints what it WOULD do.

No PyYAML dependency — uses a minimal regex-based parser that handles the
registry's specific structure (2-space project keys, 4-space scalar fields).
Ignores nested blocks like `docs:`, `linear:`, `notion:` etc.
"""

from __future__ import annotations

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

# Marker substring in claude argv that indicates the live-feedback plugin
# is loaded. Used by --mode=plugin to detect sessions that need an upgrade.
PLUGIN_MARKER = "plugin:live-feedback@claude-live-feedback"

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

# Plugin MCP servers we expect a healthy session to have spoken to. Used by the
# post-spawn health check, which is advisory: it reports, it does not fail the run.
EXPECTED_MCP_MARKERS = {
    "claude-hive": "claude-hive-mcp",
    "live-feedback": "claude-live-feedback-plugin/packages/plugin/mcp",
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
    would cause one Discord post to fan out to every peer)."""
    tmux_name = to_tmux_session_name(session_name)
    # Idempotent: kill any pre-existing tmux session with the same name
    subprocess.run([TMUX_BIN, "kill-session", "-t", tmux_name],
                   capture_output=True, timeout=3.0)
    # `-n <name>` sets the session display name (agent picker, Remote Control, terminal
    # title) so a respawned agent is identifiable on launch. Forwards through the `claude`
    # zsh function (it passes "$@" after appending channel flags). shlex.quote handles the
    # spaces in display names like "App Dev For All".
    name_flag = f" -n {shlex.quote(session_name)}"
    claude_invocation = ("claude --continue" if has_prior_session(path) else "claude") + name_flag

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
         "/bin/zsh", "-ic", claude_invocation],
        capture_output=True, text=True, timeout=5.0,
    )
    if r.returncode != 0:
        print(f"  [warn] tmux spawn failed for {tmux_name}: {r.stderr.strip()}", file=sys.stderr)
        return False
    return True


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
            if any(p in content for p in DIALOG_PATTERNS):
                is_resume = any(p in content for p in RESUME_DIALOG_PATTERNS)
                ok = (tmux_send_keys(s, "Down", "Enter")
                      if (no_compact and is_resume)
                      else tmux_send_enter(s))
                if ok:
                    sent[s] += 1
                still_pending.add(s)  # dialog may chain into another dialog
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
        elif mode == "all":
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
                if PLUGIN_MARKER not in running[existing_pid]["argv"]:
                    print(f"  [self] {name}: team-lead lacks plugin — restart this session manually to upgrade")
                skipped.append((name, path))
            elif PLUGIN_MARKER in running[existing_pid]["argv"]:
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
  --mode plugin    Kill+respawn any session lacking the `claude-live-feedback`
                   plugin (canonical fleet plugin). Team Lead (self) is never
                   killed; flagged if it lacks the plugin.
  --mode all       Kill+respawn every respawn=true session, except the
                   team-lead (self).

Flags:
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

    mode = "missing"
    if "--mode" in args:
        idx = args.index("--mode")
        if idx + 1 >= len(args):
            sys.exit("--mode requires a value (missing|plugin|all)")
        mode = args[idx + 1]

    targets = collect_targets()
    if not targets:
        print("No projects with respawn: true found in registry.yaml", file=sys.stderr)
        return 1

    running = get_running_claude_processes()
    self_pid = get_self_pid()

    print(f"Mode: {mode}")
    print(f"Registry has {len(targets)} respawn-enabled projects.")
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
                time.sleep(SPAWN_STAGGER_SEC)
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
