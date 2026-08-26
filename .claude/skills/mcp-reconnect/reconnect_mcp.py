#!/usr/bin/env python3
"""Drive `/mcp` -> Reconnect in a peer's tmux pane, preserving unsent input.

The dangerous part of this operation is not the reconnect. It is that Bryan
routinely leaves text sitting unsent in a peer's input box, and a slash command
only works at the start of an empty box. So the box has to be cleared and put
back exactly as it was.

Invariants (each learned the expensive way -- see docs/process/learnings.md):

1. NEVER touch a pane whose footer says "Press up to edit queued messages".
   That is a real message QUEUE, not inert box text, and clearing it destroys
   an unprocessed message. Abort instead.
2. NEVER press Enter while Bryan's text is in the box. Restoration is literal
   text only; submitting is his call, never ours.
3. Capture to disk BEFORE clearing, and restore in a `finally`. If any step
   between fails, the text still goes back.
4. VERIFY the highlighted row before pressing Enter in a menu. Server lists
   differ per session, so a fixed number of Downs lands on the wrong entry --
   that happened on a peer session on 2026-08-13.
5. Refuse to act on a busy session. "esc to interrupt" in the footer means a
   task is running and the input box is not ours to drive.

Dry-run by default, matching respawn.py. Pass --execute to actually act.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

STATE_DIR = Path.home() / ".local" / "state" / "team-lead" / "mcp-reconnect"

# Footer marker meaning a task is mid-flight.
BUSY_MARKER = "esc to interrupt"
# Footer marker meaning there is a real queued message, not inert box text.
QUEUE_MARKER = "Press up to edit queued messages"

MENU_STEP_PAUSE = 0.4
DIALOG_WAIT = 1.2
MAX_MENU_STEPS = 40


# --------------------------------------------------------------------------
# Pure helpers -- these are the parts that can silently corrupt Bryan's text,
# so they take a string and return a value and are unit-testable without tmux.
# --------------------------------------------------------------------------

def _is_rule(line: str) -> bool:
    """True for one of the box's horizontal rules (may carry a session title)."""
    stripped = line.strip()
    return len(stripped) >= 8 and stripped.count("─") >= 8


def parse_box_text(pane: str) -> str:
    """Return the text sitting in the input box, or '' if it is empty.

    The box is the region between the last two horizontal rules in the pane.
    Prompt markers are stripped; embedded newlines are preserved so multi-line
    input round-trips.
    """
    lines = pane.rstrip("\n").split("\n")
    rules = [i for i, ln in enumerate(lines) if _is_rule(ln)]
    if len(rules) < 2:
        return ""
    top, bottom = rules[-2], rules[-1]
    out = []
    for line in lines[top + 1:bottom]:
        body = line.rstrip()
        stripped = body.lstrip()
        if stripped.startswith("❯"):
            body = stripped[1:]
            # The prompt separator is U+00A0, not a plain space. Strip exactly
            # one whitespace char so a leading NBSP never rides along into the
            # restored text -- caught by the parser test on 2026-08-13.
            if body[:1].isspace():
                body = body[1:]
        out.append(body)
    return "\n".join(out).strip("\n")


def pane_is_busy(pane: str) -> bool:
    return BUSY_MARKER in pane


def pane_has_queue(pane: str) -> bool:
    return QUEUE_MARKER in pane


def selected_row(pane: str) -> str | None:
    """Return the currently highlighted menu row, or None if no selection."""
    for line in reversed(pane.split("\n")):
        stripped = line.strip()
        if stripped.startswith("❯"):
            return stripped[1:].strip()
    return None


# --------------------------------------------------------------------------
# tmux plumbing
# --------------------------------------------------------------------------

def tmux(*args: str) -> str:
    result = subprocess.run(
        ["tmux", *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"tmux {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def capture(session: str, lines: int = 60) -> str:
    return tmux("capture-pane", "-t", session, "-p", "-S", f"-{lines}")


def send(session: str, *keys: str) -> None:
    tmux("send-keys", "-t", session, *keys)


def send_literal(session: str, text: str) -> None:
    """Type text without submitting it."""
    for i, chunk in enumerate(text.split("\n")):
        if i:
            # Shift+Enter inserts a newline in the editor without submitting.
            send(session, "S-Enter")
        if chunk:
            tmux("send-keys", "-t", session, "-l", chunk)


def probe_box(session: str) -> tuple[str, bool]:
    """Return (real_editor_text, typed_leading_slash).

    THE RENDERED BOX IS NOT THE EDITOR. A pane routinely renders a ghost of
    previously-submitted text over an empty editor. Verified 2026-08-13: typing
    'X' over a box rendering "Cancelled Fantastic" produced "X", and one BSpace
    brought the ghost back. Three kill bindings had done nothing because there
    was nothing to kill.

    So we never trust the render. We type one '/' -- the first character of the
    command we want anyway -- and read back whether it REPLACED the render (the
    editor was empty) or APPENDED to it (there is real text).
    """
    rendered = parse_box_text(capture(session))
    if not rendered:
        return "", False

    tmux("send-keys", "-t", session, "-l", "/")
    time.sleep(MENU_STEP_PAUSE * 2)
    now = parse_box_text(capture(session))

    if now.strip() == "/":
        return "", True  # ghost; the leading slash is already typed
    send(session, "BSpace")  # real text -- undo the probe, touch nothing else
    time.sleep(MENU_STEP_PAUSE)
    return rendered, False


def sessions() -> list[str]:
    return [s for s in tmux("list-sessions", "-F", "#{session_name}").split("\n") if s]


# --------------------------------------------------------------------------
# The operation
# --------------------------------------------------------------------------

def navigate_to(session: str, needle: str, what: str) -> None:
    """Press Down until the highlighted row contains `needle`. Verify, never count."""
    seen = []
    for _ in range(MAX_MENU_STEPS):
        row = selected_row(capture(session, 40))
        if row is None:
            raise RuntimeError(f"no highlighted row while looking for {what}")
        if needle.lower() in row.lower():
            return
        if seen and row == seen[-1]:
            raise RuntimeError(
                f"selection stopped moving at {row!r} without reaching {what}"
            )
        seen.append(row)
        send(session, "Down")
        time.sleep(MENU_STEP_PAUSE)
    raise RuntimeError(f"gave up after {MAX_MENU_STEPS} steps looking for {what}")


def reconnect(session: str, server: str, execute: bool) -> str:
    pane = capture(session)

    if pane_has_queue(pane):
        return "SKIP  queued message present -- clearing it would destroy it"
    if pane_is_busy(pane):
        return "SKIP  session is busy (esc to interrupt)"

    box = parse_box_text(pane)
    if not execute:
        held = f"holds {box!r}" if box else "box empty"
        return f"WOULD reconnect {server} ({held})"

    # The rendered box is NOT proof of editor contents. A pane routinely shows
    # a GHOST of previously-submitted text over an empty editor -- verified
    # 2026-08-13: typing 'X' over a box rendering "Cancelled Fantastic" yielded
    # "X", and one BSpace restored the ghost. Three kill bindings had done
    # nothing precisely because there was nothing to kill. So discriminate with
    # a sentinel keystroke rather than trusting the render.
    box, ghost = probe_box(session)

    stash = None
    if box:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        stash = STATE_DIR / f"{session}.txt"
        stash.write_text(box, encoding="utf-8")

    try:
        if box:
            raise RuntimeError(
                f"box holds REAL text {box!r} and no kill binding works over "
                "tmux; not clobbering it -- clear it yourself, then re-run"
            )

        # probe_box already typed the leading '/' when it proved the box empty.
        tmux("send-keys", "-t", session, "-l", "mcp" if ghost else "/mcp")
        send(session, "Enter")
        time.sleep(DIALOG_WAIT)

        navigate_to(session, server, f"server {server!r}")
        send(session, "Enter")
        time.sleep(DIALOG_WAIT)

        navigate_to(session, "reconnect", "the Reconnect action")
        send(session, "Enter")
        time.sleep(DIALOG_WAIT * 2)

        after = capture(session)
        ok = "reconnected" in after.lower()

        send(session, "Escape")
        time.sleep(MENU_STEP_PAUSE)
        return "OK    reconnected" if ok else "WARN  no 'Reconnected' line in pane"
    finally:
        if box:
            # Belt and braces: only restore into an empty box, never append.
            time.sleep(MENU_STEP_PAUSE)
            if not parse_box_text(capture(session)):
                send_literal(session, box)
                time.sleep(MENU_STEP_PAUSE)
            restored = parse_box_text(capture(session))
            if restored.strip() == box.strip() and stash:
                stash.unlink(missing_ok=True)
            else:
                print(
                    f"  !! {session}: text NOT restored cleanly; saved at {stash}",
                    file=sys.stderr,
                )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", default="live-feedback", help="MCP server name to reconnect")
    ap.add_argument("--only", action="append", default=[], help="restrict to matching sessions")
    ap.add_argument("--exclude", action="append", default=[], help="skip matching sessions")
    ap.add_argument("--execute", action="store_true", help="actually act (default: dry run)")
    args = ap.parse_args()

    self_session = "team-lead"
    targets = []
    for s in sessions():
        if s == self_session:
            continue  # never drive our own pane
        if args.only and not any(o.lower() in s.lower() for o in args.only):
            continue
        if any(x.lower() in s.lower() for x in args.exclude):
            continue
        targets.append(s)

    if not targets:
        print("no matching sessions")
        return 1

    for s in targets:
        try:
            print(f"{s:24s} {reconnect(s, args.server, args.execute)}")
        except Exception as exc:  # noqa: BLE001 - report and continue to next peer
            print(f"{s:24s} FAIL  {exc}")

    if not args.execute:
        print("\nDRY RUN -- pass --execute to do it for real.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
