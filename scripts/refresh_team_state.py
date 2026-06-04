#!/usr/bin/env python3
"""
Refresh Team Lead's snapshot of every peer's state.

Writes `docs/state/team-state.md` — a moving snapshot so Team Lead doesn't
have to ask peers (or rely on stale memories) before making decisions.

For each project in `registry.yaml` with `respawn: true`:
  - Locates the peer's most recent transcript JSONL at
    `~/.claude/projects/<encoded-realpath>/<session-id>.jsonl`
    (encoding: replace `/`, `_`, `.` with `-`; pick file with newest mtime).
  - Reads only the tail of the JSONL (transcripts are 40-80MB) and extracts:
      * latest gitBranch
      * last 3 user message contents (filtered: no system-reminder, no
        command-name, no channel events, no Caveat: lines)
      * last 3 assistant text contents (skip tool_use payloads)
      * latest 5 tool_use names + descriptions (name + description param only;
        never full inputs, which may contain secrets)
      * most recent timestamp
  - Reads git state via `git -C <path>`: current branch, last 3 commits,
    short status (first 5 lines).

Output is local-only — `docs/state/` is gitignored. Re-runnable; clobbers the
prior snapshot.

Secret-scrub: drops any extracted string containing patterns that look like
API keys / OAuth tokens (sk-, ghp_, gho_, Bearer, Authorization:).

Registry parser is the same minimal regex pattern as
`.claude/skills/respawn-sessions/respawn.py` — no PyYAML dep.

Usage:
  python3 scripts/refresh_team_state.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
REGISTRY_PATH = os.path.join(REPO_ROOT, "registry.yaml")
OUTPUT_DIR = os.path.join(REPO_ROOT, "docs", "state")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "team-state.md")

# Bytes read from the tail of a transcript. JSONL files are 40-80MB so we
# never want to read the whole file. ~512KB is plenty to find the last few
# user/assistant turns even on a session that's just dumped a huge tool result.
TRANSCRIPT_TAIL_BYTES = 512 * 1024

# How many recent items to surface per category.
N_USER_MSGS = 3
N_ASST_MSGS = 3
N_TOOL_USES = 5

# Truncate snippet to keep the report scannable.
MSG_PREVIEW_CHARS = 200

# Secret-shaped patterns. If any extracted string matches, we drop the string
# entirely (rather than partial-redact — partial values can enable re-lookup).
SECRET_RES = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"ghp_[A-Za-z0-9]{8,}"),
    re.compile(r"gho_[A-Za-z0-9]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{8,}"),
    re.compile(r"Authorization:\s*\S+", re.IGNORECASE),
]

# Filter prefixes for user messages — these aren't actual user prompts, they're
# harness/system noise we don't want surfaced as "what the user just said".
USER_NOISE_PREFIXES = (
    "<system-reminder>",
    "<command-name>",
    "<command-message>",
    "<local-command-stdout>",
    "<local-command-caveat>",
    "Caveat:",
    "This session is being continued from a previous conversation",
    "<<autonomous-loop",
)
USER_NOISE_TOKENS = (
    # Any `<channel source="..."` block is a harness-routed event, not a typed
    # user prompt. Filter all channel sources (hive, live-feedback, discord,
    # notion, sentry, github, plugin:* variants).
    '<channel source=',
)


# --- registry.yaml parsing (mirrors respawn.py) -----------------------------

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


def collect_peers() -> List[Dict[str, str]]:
    """Return list of {name, session_name, path} for projects with respawn:true."""
    projects = parse_registry(REGISTRY_PATH)
    peers: List[Dict[str, str]] = []
    for name, fields in projects.items():
        if fields.get("respawn", "").lower() != "true":
            continue
        raw_path = fields.get("path", "")
        if not raw_path:
            continue
        path = os.path.expanduser(raw_path)
        peers.append({
            "name": name,
            "session_name": fields.get("session_name") or humanize(name),
            "path": path,
            # No `repo` → local-only folder (e.g. a synced Google Drive dir);
            # the metaproject skips all git for it.
            "repo": fields.get("repo", ""),
        })
    return peers


# --- transcript discovery ----------------------------------------------------

def encode_path_for_transcripts(path: str) -> str:
    """`/foo/bar_baz.qux` -> `-foo-bar-baz-qux`. Matches Claude Code's
    transcript directory naming under ~/.claude/projects/."""
    real = os.path.realpath(path)
    return real.replace("/", "-").replace("_", "-").replace(".", "-")


def find_latest_transcript(path: str) -> Optional[str]:
    """Return the path to the most recent .jsonl transcript for a project,
    or None if there's no transcript dir or no .jsonl files."""
    encoded = encode_path_for_transcripts(path)
    transcript_dir = os.path.join(os.path.expanduser("~"), ".claude", "projects", encoded)
    if not os.path.isdir(transcript_dir):
        return None
    candidates: List[Tuple[float, str]] = []
    try:
        for entry in os.listdir(transcript_dir):
            if not entry.endswith(".jsonl"):
                continue
            full = os.path.join(transcript_dir, entry)
            try:
                candidates.append((os.path.getmtime(full), full))
            except OSError:
                continue
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


# --- transcript parsing ------------------------------------------------------

def read_tail_lines(filepath: str, nbytes: int) -> List[str]:
    """Read the last `nbytes` of a file and return complete JSONL lines.
    Drops the first chunk (likely a partial line)."""
    try:
        size = os.path.getsize(filepath)
    except OSError:
        return []
    to_read = min(size, nbytes)
    try:
        with open(filepath, "rb") as f:
            f.seek(size - to_read)
            data = f.read(to_read)
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    # If we didn't start at byte 0, the first line is likely partial.
    if to_read < size and lines:
        lines = lines[1:]
    return [ln for ln in lines if ln.strip()]


def looks_like_secret(text: str) -> bool:
    return any(r.search(text) for r in SECRET_RES)


def is_user_noise(text: str) -> bool:
    s = text.lstrip()
    if not s:
        return True
    for pfx in USER_NOISE_PREFIXES:
        if s.startswith(pfx):
            return True
    for tok in USER_NOISE_TOKENS:
        if tok in s:
            return True
    return False


def collapse_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def truncate(s: str, n: int = MSG_PREVIEW_CHARS) -> str:
    s = collapse_whitespace(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def extract_user_text(message: dict) -> Optional[str]:
    """Pull the user-facing text out of a 'user' record. Returns None if it's
    a tool_result or otherwise not a real user prompt."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Only count it as a "real" user message if it has a text item AND no
        # tool_result item (tool_result is the harness reporting tool output
        # back to the model, not the user typing).
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                return None
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                t = item.get("text", "")
                if t:
                    texts.append(t)
        if texts:
            return "\n".join(texts)
    return None


def extract_assistant_text(message: dict) -> Optional[str]:
    """Pull assistant prose out of an 'assistant' record. Skips thinking and
    tool_use items."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                t = item.get("text", "")
                if t:
                    texts.append(t)
        if texts:
            return "\n".join(texts)
    return None


def extract_tool_uses(message: dict) -> List[Tuple[str, str]]:
    """Return [(name, description)] for tool_use items in this assistant record.
    Description is `input.description` if present (Bash tool convention) else ''."""
    out: List[Tuple[str, str]] = []
    content = message.get("content")
    if not isinstance(content, list):
        return out
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "tool_use":
            continue
        name = item.get("name", "?")
        inp = item.get("input", {})
        desc = ""
        if isinstance(inp, dict):
            desc = inp.get("description", "") or ""
            if not isinstance(desc, str):
                desc = ""
        out.append((name, desc))
    return out


def parse_transcript_tail(filepath: str) -> Dict[str, object]:
    """Parse the tail of a JSONL transcript and return a summary dict."""
    lines = read_tail_lines(filepath, TRANSCRIPT_TAIL_BYTES)
    git_branch: Optional[str] = None
    last_ts: Optional[str] = None
    user_msgs: List[str] = []
    asst_msgs: List[str] = []
    tool_uses: List[Tuple[str, str]] = []

    for raw in lines:
        try:
            rec = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue

        gb = rec.get("gitBranch")
        if gb:
            git_branch = gb
        ts = rec.get("timestamp")
        if ts:
            last_ts = ts

        t = rec.get("type")
        if t == "user":
            msg = rec.get("message")
            if isinstance(msg, dict):
                text = extract_user_text(msg)
                if text and not is_user_noise(text) and not looks_like_secret(text):
                    user_msgs.append(text)
        elif t == "assistant":
            msg = rec.get("message")
            if isinstance(msg, dict):
                text = extract_assistant_text(msg)
                if text and not looks_like_secret(text):
                    asst_msgs.append(text)
                for name, desc in extract_tool_uses(msg):
                    if desc and looks_like_secret(desc):
                        desc = ""
                    tool_uses.append((name, desc))

    return {
        "git_branch": git_branch,
        "last_ts": last_ts,
        "user_msgs": user_msgs[-N_USER_MSGS:],
        "asst_msgs": asst_msgs[-N_ASST_MSGS:],
        "tool_uses": tool_uses[-N_TOOL_USES:],
    }


# --- git state ---------------------------------------------------------------

def _git(path: str, *args: str, timeout: float = 5.0) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", path, *args],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            return ""
        return r.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def get_git_state(path: str, has_repo: bool = True) -> Dict[str, object]:
    if not has_repo:
        # Local-only project (no `repo` in registry): intentionally not a git
        # repo — a plain folder, possibly synced (e.g. Google Drive). Skip all
        # git commands entirely.
        return {"available": False, "local": True, "reason": "local folder (no repo)"}
    if not os.path.isdir(path):
        return {"available": False, "reason": "path missing"}
    if not os.path.isdir(os.path.join(path, ".git")):
        # Worktrees use a .git file (not dir), so also accept that.
        if not os.path.isfile(os.path.join(path, ".git")):
            return {"available": False, "reason": "not a git repo"}

    branch = _git(path, "branch", "--show-current").strip()
    log = _git(path, "log", "-n", "3", "--pretty=%h %s").strip()
    status = _git(path, "status", "--short").strip()
    status_lines = [ln for ln in status.split("\n") if ln.strip()]
    truncated = len(status_lines) > 5
    status_lines = status_lines[:5]

    return {
        "available": True,
        "branch": branch,
        "commits": [ln for ln in log.split("\n") if ln.strip()],
        "status_lines": status_lines,
        "status_truncated": truncated,
        "clean": len(status_lines) == 0 and not truncated,
        "dirty_count": len(status_lines) + (1 if truncated else 0),
    }


# --- rendering ---------------------------------------------------------------

def format_peer_section(peer: Dict[str, str], data: Dict[str, object]) -> str:
    out: List[str] = []
    out.append(f"## {peer['session_name']}")
    out.append(f"- **Path:** {peer['path']}")

    transcript = data.get("transcript")
    git = data.get("git", {})
    error = data.get("error")

    if error:
        out.append(f"- **Status:** error — {error}")
        out.append("")
        return "\n".join(out)

    if not transcript:
        out.append("- **Transcript:** no recent transcript")
    elif not git.get("local"):
        tx = transcript
        tx_branch = tx.get("git_branch") or "?"
        git_branch = git.get("branch") if git.get("available") else "?"
        out.append(f"- **Active branch:** {tx_branch} · git: {git_branch}")

    if git.get("local"):
        out.append("- **Folder:** local (no git repo — git skipped)")
    elif git.get("available"):
        commits = git.get("commits") or []
        if commits:
            out.append("- **Last 3 commits:**")
            for c in commits:
                out.append(f"    - `{c}`")
        else:
            out.append("- **Last 3 commits:** (none)")

        if git.get("clean"):
            out.append("- **Working tree:** clean")
        else:
            n = git.get("dirty_count", 0)
            suffix = "+" if git.get("status_truncated") else ""
            out.append(f"- **Working tree:** {n}{suffix} files dirty")
            for ln in git.get("status_lines", []):
                out.append(f"    - `{ln}`")
    else:
        out.append(f"- **Git:** unavailable ({git.get('reason','?')})")

    if transcript:
        tx = transcript
        last_ts = tx.get("last_ts") or "?"
        out.append(f"- **Last activity:** {last_ts}")

        tool_uses = tx.get("tool_uses") or []
        if tool_uses:
            parts = []
            for name, desc in tool_uses:
                if desc:
                    parts.append(f"{name} ({truncate(desc, 60)})")
                else:
                    parts.append(name)
            out.append(f"- **Recent tool uses:** {', '.join(parts)}")
        else:
            out.append("- **Recent tool uses:** (none in tail)")

        user_msgs = tx.get("user_msgs") or []
        if user_msgs:
            out.append(f"- **Last user msg:** {truncate(user_msgs[-1])}")
            if len(user_msgs) > 1:
                for older in user_msgs[:-1]:
                    out.append(f"    - prior: {truncate(older)}")
        else:
            out.append("- **Last user msg:** (none in tail)")

        asst_msgs = tx.get("asst_msgs") or []
        if asst_msgs:
            out.append(f"- **Last assistant msg:** {truncate(asst_msgs[-1])}")
            if len(asst_msgs) > 1:
                for older in asst_msgs[:-1]:
                    out.append(f"    - prior: {truncate(older)}")
        else:
            out.append("- **Last assistant msg:** (none in tail)")

    out.append("")
    return "\n".join(out)


def main() -> int:
    t0 = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    peers = collect_peers()
    if not peers:
        print("No respawn:true peers found in registry.yaml.", file=sys.stderr)
        return 1

    sections: List[str] = []
    error_count = 0
    no_transcript_count = 0

    for peer in peers:
        data: Dict[str, object] = {}
        try:
            transcript_path = find_latest_transcript(peer["path"])
            if transcript_path:
                data["transcript"] = parse_transcript_tail(transcript_path)
            else:
                data["transcript"] = None
                no_transcript_count += 1
            data["git"] = get_git_state(peer["path"], has_repo=bool(peer.get("repo")))
        except Exception as e:
            data["error"] = f"{type(e).__name__}: {e}"
            error_count += 1
        sections.append(format_peer_section(peer, data))

    ts_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = (
        f"# Team state — {ts_iso} (refresh every 15 min)\n\n"
        f"_{len(peers)} peer(s) tracked. Snapshot is local-only; "
        f"`docs/state/` is gitignored._\n\n"
    )
    body = "\n".join(sections)

    with open(OUTPUT_PATH, "w") as f:
        f.write(header)
        f.write(body)

    elapsed = time.time() - t0
    print(
        f"Wrote {OUTPUT_PATH} — {len(peers)} peers, "
        f"{no_transcript_count} without transcripts, "
        f"{error_count} error(s), {elapsed:.2f}s elapsed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
