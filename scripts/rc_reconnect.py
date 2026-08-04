#!/usr/bin/env python3
"""Re-register peer sessions' Remote Control WITHOUT restarting them.

USE THIS ONLY WHEN A SESSION MUST NOT BE KILLED
-----------------------------------------------
For an account switch, the right tool is
`respawn.py --mode running --execute --no-compact`, not this script.

A `/login` leaves three things bound to the old account — MCP server
connections, the Remote Control registration, and the env read at startup.
This script fixes exactly one of them. Running it *instead of* a restart
produces a fleet where each session disagrees about which account it is on:
observed 2026-08-03, when peers cycled here on 2026-07-31 were still talking
to the Team account's MCP servers three days later. Reach for it only when a
peer is mid-flight and killing it would lose work.

WHY IT EXISTS AT ALL
--------------------
`/login` flips the machine Keychain and every running session follows it for
billing on its next request — but their Remote Control registrations do NOT
follow. They stay bound to the previous account, so the fleet silently vanishes
from the phone's session list. Observed 2026-07-31 on the Team -> Max switch:
the ADFA session had to be disconnected and reconnected by hand before it
reappeared under the personal account.

There is no CLI for this; `/remote-control` is an interactive menu. This script
drives that menu over tmux.

THE FIX PER SESSION
-------------------
  /remote-control  ->  Up Up  ->  Enter (Disconnect this session)  ->  /remote-control

A successful reconnect issues a NEW session id, which is the proof it
re-registered rather than resumed.

TEXT SITTING IN THE PROMPT IS NOT A DRAFT
-----------------------------------------
tmux send-keys APPENDS to whatever is in the prompt, so injecting a command on
top of existing text would concatenate into garbage. By default this script
SKIPS any session whose prompt is non-empty.

Do not describe that text as the operator's draft. Bryan has corrected this
repeatedly and he is right: he is not sitting on half-typed messages. What
lands in the box is a message Remote Control delivered but never submitted, or
a `/compact` the session pre-filled on resume. Either way it is a symptom, and
the useful response is to record what was there and say so — not to protect it
as if someone were mid-sentence, and not to ask permission to press Enter on
his behalf.

--force clears the box with C-u and proceeds. Prefer it once you have captured
the contents; prefer skipping when nobody has looked yet.

Usage:
    python3 scripts/rc_reconnect.py                  # dry run, all sessions
    python3 scripts/rc_reconnect.py --execute        # do it, skip non-empty prompts
    python3 scripts/rc_reconnect.py --execute --force  # clear prompts and do it anyway
    python3 scripts/rc_reconnect.py --execute <session-name> <session-name>
"""
import re
import subprocess
import sys
import time

RC_MARKER = "/rc"          # footer marker; present only while RC is connected
MENU_MARKER = "Remote Control"
DISCONNECT_ITEM = "Disconnect this session"


def tmux(*args, check=False):
    return subprocess.run(["tmux", *args], capture_output=True, text=True, check=check)


def sessions():
    out = tmux("ls", "-F", "#{session_name}").stdout
    return [s for s in out.splitlines() if s.strip()]


def pane(name, lines=30):
    return tmux("capture-pane", "-t", name, "-p", "-S", f"-{lines}").stdout


def draft_text(name):
    """Whatever the operator has typed but not sent. Empty string if clean."""
    prompt_lines = [l for l in pane(name).splitlines() if l.startswith("❯")]
    if not prompt_lines:
        return ""
    return re.sub(r"^❯\s*", "", prompt_lines[-1]).strip()


def awaiting_operator(name):
    """True if the session is showing an interactive prompt awaiting a human.

    --force clears stale text, but an open question/menu is NOT stale text: it is
    the session blocked on an answer only the operator can give. Blowing through
    it would pick an option on his behalf or cancel the question outright. Found
    the hard way 2026-07-31, when a live-feedback session displaying a
    multi-select question was misread as holding a draft.
    """
    tail = pane(name, 12)
    return ("Enter to select" in tail) or ("Esc to cancel" in tail)


def rc_connected(name):
    return RC_MARKER in pane(name, 8)


def session_id(name):
    m = re.findall(r"session_[A-Za-z0-9]+", pane(name, 40))
    return m[-1] if m else None


def clear_prompt(name):
    """C-u wipes the input line so an injected command doesn't concatenate."""
    tmux("send-keys", "-t", name, "C-u")
    time.sleep(1)


def reconnect(name, force=False):
    """Returns (ok, message)."""
    if force:
        clear_prompt(name)
    tmux("send-keys", "-t", name, "/remote-control", "Enter")
    time.sleep(6)

    if MENU_MARKER not in pane(name, 25):
        return False, "remote-control menu never appeared"
    before = session_id(name)

    # cursor lands on "Continue"; the disconnect item is two above it
    tmux("send-keys", "-t", name, "Up", "Up")
    time.sleep(2)

    cursor_line = next((l for l in pane(name, 25).splitlines()
                        if l.strip().startswith("❯") and DISCONNECT_ITEM in l), None)
    if not cursor_line:
        tmux("send-keys", "-t", name, "Escape")
        return False, "could not put cursor on 'Disconnect this session' — menu shape changed"

    tmux("send-keys", "-t", name, "Enter")
    time.sleep(5)
    if rc_connected(name):
        return False, "still shows connected after disconnect"

    tmux("send-keys", "-t", name, "/remote-control", "Enter")
    time.sleep(8)
    if not rc_connected(name):
        return False, "did not come back connected"

    after = session_id(name)
    if before and after and before == after:
        return True, f"reconnected but session id unchanged ({after}) — verify on phone"
    return True, f"reconnected · new session id {after}"


def main():
    execute = "--execute" in sys.argv
    force = "--force" in sys.argv
    named = [a for a in sys.argv[1:] if not a.startswith("--")]
    targets = named or sessions()

    skipped, done, failed = [], [], []

    for name in targets:
        # never overridable by --force: the session is blocked on a human answer
        if awaiting_operator(name):
            skipped.append((name, "interactive prompt open — awaiting your answer"))
            print(f"[hold]  {name:<26} interactive prompt open — not touching it")
            continue

        draft = draft_text(name)
        if draft and not force:
            skipped.append((name, draft[:50]))
            print(f"[skip]  {name:<26} text in prompt — pass --force to clear it")
            continue

        if not execute:
            state = "connected" if rc_connected(name) else "NOT connected"
            extra = " (would clear prompt first)" if draft else ""
            print(f"[dry]   {name:<26} would reconnect (currently {state}){extra}")
            continue

        ok, msg = reconnect(name, force=force)
        (done if ok else failed).append(name)
        print(f"[{'ok' if ok else 'FAIL':<4}] {name:<26} {msg}")

    print()
    if not execute:
        print("DRY RUN — pass --execute to apply.")
    else:
        print(f"{len(done)} reconnected, {len(failed)} failed, {len(skipped)} skipped for drafts.")
    if skipped:
        print("\nStill on the old account until their drafts clear:")
        for name, d in skipped:
            print(f"  {name:<26} {d}")


if __name__ == "__main__":
    main()
