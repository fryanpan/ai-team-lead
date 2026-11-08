#!/usr/bin/env python3
"""Restart idle, oversized Claude sessions through respawn.py.

A session's footprint grows with age and never comes back on its own: freshly
restarted sessions sit at 204-285 MB, sessions running 5-7 hours at 465-723 MB.
Restarting the oldest, biggest one recovered 245 MB (34%) on 2026-09-12. This
script finds that session and cycles it -- but only while it is genuinely idle,
because the memory is worth far less than the context a mid-task restart throws
away.

Three things this script refuses to do, each of them a mistake already made
somewhere in this fleet:

1. It never reads a tmux pane. A pane is a render and cannot show what a session
   received; idleness is read from the transcript's last record.
2. It never spawns a session itself. Every restart goes through respawn.py,
   which carries the identity env, the scoped Discord state dir, dialog
   dismissal and the orphan-MCP sweep.
3. It never reports "nothing to do" when it could not look. A vmmap or
   transcript failure exits 2, distinct from both a clean pass and an action.

Dry-run by default, like respawn.py. --execute is required to restart anything.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Sessions below this recover too little to be worth the resume spike. Set from
# the measured spread: a session has to be well past the 204-285 MB fresh range
# before restarting it buys back anything like the 245 MB the prototype saw.
DEFAULT_THRESHOLD_MB = 600

# Long enough that a session pausing between turns is never mistaken for idle.
DEFAULT_IDLE_MINUTES = 45

# Resume reads the whole transcript, and the read buffer grows by doubling: a
# 710 MB transcript peaked at 1.9 GB. Past this, a restart costs more than it
# saves unless the session is resumed fresh (registry `fresh_start: true`).
RESUME_SPIKE_MB = 200

# Respawning several sessions at once stacks their resume spikes on a machine
# that is already swapping. One per run, and let the next run take the next one.
DEFAULT_MAX_RESTARTS = 1

CLAUDE_BIN_MARKER = ".local/bin/claude"
RESPAWN = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "respawn-sessions" / "respawn.py"


@dataclass
class Session:
    pid: int
    cwd: str
    footprint_mb: float | None = None
    transcript: Path | None = None
    transcript_mb: float = 0.0
    idle_minutes: float | None = None
    awaiting_tool: bool = False
    note: str = ""

    @property
    def name(self) -> str:
        return Path(self.cwd).name if self.cwd else f"pid-{self.pid}"


def own_claude_pid() -> int | None:
    """The claude process this script is running under, which must never be killed.

    Walks the parent chain rather than matching on cwd: the team-lead's cwd is
    also a registry path, so a cwd match would exclude a legitimate peer if one
    were ever started there.
    """
    pid = os.getppid()
    for _ in range(12):
        if pid <= 1:
            return None
        out = _run(["ps", "-p", str(pid), "-o", "ppid=,command="])
        if not out:
            return None
        parts = out.strip().split(None, 1)
        if len(parts) < 2:
            return None
        ppid, cmd = parts
        if CLAUDE_BIN_MARKER in cmd:
            return pid
        pid = int(ppid)
    return None


def _run(cmd: list[str], timeout: int = 60) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return r.stdout


def list_sessions(user: str) -> list[Session]:
    out = _run(["ps", "-axww", "-o", "user=,pid=,command="])
    sessions: list[Session] = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        who, pid, cmd = parts
        if who != user or CLAUDE_BIN_MARKER not in cmd or "grep" in cmd:
            continue
        sessions.append(Session(pid=int(pid), cwd=cwd_of(int(pid))))
    return sessions


def cwd_of(pid: int) -> str:
    out = _run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    for line in out.splitlines():
        if line.startswith("n"):
            return line[1:]
    return ""


def footprint_mb(pid: int) -> float | None:
    """Physical footprint, the figure macOS actually counts against RAM.

    Not ps RSS, which counts the ~155 MB of shared program text once per process
    and so reads high enough to push a fresh session over any sane threshold.
    """
    out = _run(["vmmap", "--summary", str(pid)])
    m = re.search(r"Physical footprint:\s+([\d.]+)([MG])", out)
    if not m:
        return None
    mb = float(m.group(1))
    return mb * 1024 if m.group(2) == "G" else mb


def transcript_for(cwd: str) -> Path | None:
    """The session's newest transcript.

    Claude Code encodes the cwd by replacing /, _ and . with -, so the project
    directory name is derivable without guessing.
    """
    if not cwd:
        return None
    encoded = re.sub(r"[/_.]", "-", cwd)
    d = Path.home() / ".claude" / "projects" / encoded
    if not d.is_dir():
        return None
    # Most sessions write <uuid>.jsonl at the top level, but some keep a
    # per-session subdirectory instead, so check one level down before giving up.
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        files = sorted(d.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def tail_records(path: Path, count: int = 40) -> list[dict]:
    """Last few JSON records, read from the end so a 700 MB file costs nothing."""
    size = path.stat().st_size
    window = min(size, 512 * 1024)
    with path.open("rb") as f:
        f.seek(size - window)
        chunk = f.read(window)
    lines = chunk.split(b"\n")[1:] if window < size else chunk.split(b"\n")
    out: list[dict] = []
    for raw in lines[-count:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except ValueError:
            continue
    return out


def _timestamp(rec: dict) -> float | None:
    ts = rec.get("timestamp")
    if not isinstance(ts, str):
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def awaiting_tool(records: list[dict]) -> bool:
    """True if the last assistant turn issued a tool call nothing answered.

    This is the mid-task case that idleness alone cannot distinguish: a session
    stopped on a permission prompt writes nothing for hours and looks exactly
    like one that finished its work.
    """
    pending: set[str] = set()
    for rec in records:
        msg = rec.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("id"):
                pending.add(block["id"])
            elif block.get("type") == "tool_result" and block.get("tool_use_id"):
                pending.discard(block["tool_use_id"])
    return bool(pending)


def inspect(s: Session) -> None:
    s.footprint_mb = footprint_mb(s.pid)
    if s.footprint_mb is None:
        s.note = "could not read footprint"
        return
    s.transcript = transcript_for(s.cwd)
    if s.transcript is None:
        s.note = "no transcript found for this cwd"
        return
    s.transcript_mb = s.transcript.stat().st_size / (1024 * 1024)
    records = tail_records(s.transcript)
    if not records:
        s.note = "transcript unreadable"
        return
    last_ts = next((t for t in (_timestamp(r) for r in reversed(records)) if t), None)
    if last_ts is None:
        s.note = "no timestamp in the last records"
        return
    s.idle_minutes = (time.time() - last_ts) / 60
    s.awaiting_tool = awaiting_tool(records)


def eligible(s: Session, threshold: float, idle_minutes: float) -> tuple[bool, str]:
    if s.note:
        return False, s.note
    if s.footprint_mb is None or s.idle_minutes is None:
        return False, "incomplete reading"
    if s.awaiting_tool:
        return False, "mid-task: a tool call is still unanswered"
    if s.idle_minutes < idle_minutes:
        return False, f"active {s.idle_minutes:.0f}m ago"
    if s.footprint_mb < threshold:
        return False, f"{s.footprint_mb:.0f} MB is under the {threshold:.0f} MB bar"
    if s.transcript_mb > RESUME_SPIKE_MB:
        return False, (
            f"transcript is {s.transcript_mb:.0f} MB: resuming it would spike past what "
            f"the restart saves. Set fresh_start in the registry first."
        )
    return True, f"{s.footprint_mb:.0f} MB, idle {s.idle_minutes:.0f}m"


def restart(s: Session, execute: bool) -> int:
    # `running`, not `missing`: the session IS alive, and missing mode would skip
    # it as already up. running mode also cycles it at its own cwd, which is the
    # only cwd whose --continue restores the work a worktree session is holding.
    #
    # --only takes the directory's basename, not the absolute path: respawn.py
    # matches against the registry's own `~/dev/...` spelling, so a realpath
    # under /Volumes never matches and the run aborts with "matched none".
    cmd = ["python3", str(RESPAWN), "--mode", "running", "--only", s.name]
    if execute:
        cmd.append("--execute")
    print(f"  $ {' '.join(cmd)}")
    r = subprocess.run(cmd, text=True)
    return r.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--threshold-mb", type=float, default=DEFAULT_THRESHOLD_MB)
    ap.add_argument("--idle-minutes", type=float, default=DEFAULT_IDLE_MINUTES)
    ap.add_argument("--max", type=int, default=DEFAULT_MAX_RESTARTS)
    ap.add_argument("--user", default=os.environ.get("USER", ""))
    ap.add_argument("--execute", action="store_true", help="actually restart; otherwise report only")
    args = ap.parse_args()

    self_pid = own_claude_pid()
    sessions = [s for s in list_sessions(args.user) if s.pid != self_pid]
    if not sessions:
        print("COULD NOT LOOK: no Claude sessions found for this user.")
        return 2

    for s in sessions:
        inspect(s)

    unreadable = [s for s in sessions if s.note]
    candidates: list[Session] = []
    print(f"{'session':<24} {'MB':>7} {'idle':>8} {'transcript':>11}  verdict")
    for s in sorted(sessions, key=lambda x: -(x.footprint_mb or 0)):
        ok, why = eligible(s, args.threshold_mb, args.idle_minutes)
        mb = f"{s.footprint_mb:.0f}" if s.footprint_mb is not None else "?"
        idle = f"{s.idle_minutes:.0f}m" if s.idle_minutes is not None else "?"
        tr = f"{s.transcript_mb:.0f} MB" if s.transcript_mb else "-"
        print(f"{s.name:<24} {mb:>7} {idle:>8} {tr:>11}  {'RESTART' if ok else 'keep'} - {why}")
        if ok:
            candidates.append(s)

    if unreadable:
        print(f"\nCOULD NOT LOOK at {len(unreadable)} session(s): " + ", ".join(f"{s.name} ({s.note})" for s in unreadable))

    if not candidates:
        print("\nNothing over the bar." if not unreadable else "\nNothing over the bar among the sessions that could be read.")
        return 2 if unreadable else 0

    for s in candidates[: args.max]:
        print(f"\nRestarting {s.name} ({s.footprint_mb:.0f} MB, idle {s.idle_minutes:.0f}m):")
        rc = restart(s, args.execute)
        if rc != 0:
            print(f"  respawn.py exited {rc}")
            return rc
    if len(candidates) > args.max:
        print(f"\n{len(candidates) - args.max} more over the bar; left for the next run so resume spikes don't stack.")
    if not args.execute:
        print("\nDry run. Pass --execute to restart.")
    return 2 if unreadable else 0


if __name__ == "__main__":
    sys.exit(main())
