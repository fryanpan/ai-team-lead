#!/usr/bin/env python3
"""Tell someone when a session stops on a permission dialog.

A permission dialog stops a session until a person answers it at that
session's terminal, and nothing outside the terminal shows that it is
waiting. This runs on Claude Code's Notification hook for
`permission_prompt` and does three things:

1. Appends one line to a log, so later checks can see every wait.
2. Sends the Team Lead a hive message naming the session and the request,
   so it can put the wait on the board.
3. When the waiting session IS the Team Lead, or the hive message cannot be
   sent, shows a macOS notification, because nobody else will report it.

It always exits 0. A failure here must never block the session it reports on.
The log line records which delivery happened: sent, no-peer, failed, or self.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

HIVE = "http://127.0.0.1:7900"
LOG = os.path.expanduser(
    "~/Library/Application Support/team-lead/permission-waits.log")
TEAM_LEAD_CWD_MARK = "/ai-team-lead"


def post(path, payload):
    req = urllib.request.Request(
        HIVE + path, data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=4) as resp:
        return json.loads(resp.read() or b"null")


def team_lead_stable_id():
    try:
        peers = post("/list-peers", {"scope": "machine"})
    except Exception:
        return None
    for peer in peers or []:
        cwd = peer.get("cwd") or ""
        if cwd.endswith(TEAM_LEAD_CWD_MARK) or (TEAM_LEAD_CWD_MARK + "/") in cwd:
            return peer.get("stable_id")
    return None


def banner(title, text):
    subprocess.run(
        ["osascript", "-e", "on run argv",
         "-e", 'display notification (item 1 of argv) with title '
               '(item 2 of argv) sound name "Basso"',
         "-e", "end run", text[:200], title],
        capture_output=True, timeout=5)


def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        return
    cwd = event.get("cwd") or ""
    session = os.environ.get("CW_AGENT_NAME") or os.path.basename(cwd) or "?"
    message = (event.get("message") or "permission needed").strip()
    is_team_lead = TEAM_LEAD_CWD_MARK in cwd and "/.claude/worktrees/" not in cwd

    if is_team_lead:
        delivery = "self"
    else:
        sid = team_lead_stable_id()
        if not sid:
            delivery = "no-peer"
        else:
            text = (f"[permission-wait] {session} is stopped on a permission "
                    f"dialog: {message}. cwd {cwd}. Only a person at that "
                    f"session's terminal can answer it.")
            try:
                post("/send-message", {"from_id": "permission-wait",
                                       "to_stable_id": sid,
                                       "text": text[:1200]})
                delivery = "sent"
            except Exception:
                delivery = "failed"
    if delivery != "sent":
        try:
            banner(f"{session} is waiting on a permission dialog", message)
        except Exception:
            pass

    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(json.dumps({
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "session": session, "cwd": cwd,
                "session_id": event.get("session_id"),
                "message": message, "delivery": delivery}) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
