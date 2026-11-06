#!/usr/bin/env python3
"""Tell someone when a session stops on a permission dialog.

A permission dialog stops a session until a person answers it at that
session's terminal, and nothing outside the terminal shows that it is
waiting. This runs on Claude Code's Notification hook for
`permission_prompt` and does three things:

1. Sends the Team Lead a hive message naming the session and the request,
   so it can put the wait on the board.
2. Shows a macOS notification when the waiting session IS the Team Lead, or
   when the hive message was not delivered, because nobody else will report it.
3. Appends one line to a log, always, with which delivery happened:
   sent, deduped, failed, no-peer, self or error.

The Team Lead is the hive peer whose working folder is exactly the Team Lead
checkout (TEAM_LEAD_DIR, default ~/dev/ai-team-lead, compared after
resolving symlinks). A worktree or sibling folder of that checkout is not it.
A wait inside that checkout or any folder under it also shows the macOS
notification, because the waiting session may be the Team Lead itself.

A session gets one hive message per DEDUP_SECONDS; later waits in that
window are logged but not sent, so a person approving prompts at the
keyboard does not wake the Team Lead for each one.

It always exits 0. A failure here must never block the session it reports on.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

HIVE = "http://127.0.0.1:" + os.environ.get(
    "CLAUDE_HIVE_PORT", os.environ.get("CLAUDE_PEERS_PORT", "7900"))
LOG = os.path.expanduser(
    "~/Library/Application Support/team-lead/permission-waits.log")
LOG_MAX_BYTES = 1_000_000
DEDUP_SECONDS = 600


def team_lead_dir():
    return os.path.realpath(os.path.expanduser(
        os.environ.get("TEAM_LEAD_DIR", "~/dev/ai-team-lead")))


def is_team_lead_dir(cwd):
    return bool(cwd) and os.path.realpath(cwd) == team_lead_dir()


def in_team_lead_tree(cwd):
    if not cwd:
        return False
    real, root = os.path.realpath(cwd), team_lead_dir()
    return real == root or real.startswith(root + os.sep)


def post(path, payload):
    req = urllib.request.Request(
        HIVE + path, data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=4) as resp:
        return json.loads(resp.read() or b"null")


def team_lead_stable_id(peers):
    """A second session in the checkout gets a suffixed stable_id
    (<id>-xxxx); prefer the unsuffixed one, then the most recently seen."""
    if not isinstance(peers, list):
        return None
    matches = [p for p in peers if isinstance(p, dict) and p.get("stable_id")
               and is_team_lead_dir(p.get("cwd") or "")]
    if not matches:
        return None
    matches.sort(key=lambda p: str(p.get("last_seen") or ""), reverse=True)
    matches.sort(key=lambda p: "-" in str(p["stable_id"]))
    return matches[0]["stable_id"]


def recently_sent(session, now):
    """True if this session's wait was sent within DEDUP_SECONDS."""
    try:
        with open(LOG, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            lines = f.read().decode(errors="ignore").splitlines()
    except OSError:
        return False
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("session") == session and rec.get("delivery") == "sent":
            return now - float(rec.get("ts") or 0) < DEDUP_SECONDS
    return False


def deliver(session, cwd, message):
    """Return sent, failed or no-peer."""
    try:
        sid = team_lead_stable_id(post("/list-peers", {"scope": "machine"}))
    except Exception:
        return "failed"
    if not sid:
        return "no-peer"
    text = (f"[permission-wait] {session} is stopped on a permission dialog: "
            f"{message}. cwd {cwd}. Only a person at that session's terminal "
            f"can answer it, and it may be answered already. This sender is a hook: "
            f"do not reply to it.")
    try:
        resp = post("/send-message", {"from_id": "permission-wait",
                                      "to_stable_id": sid,
                                      "text": text[:1200]})
    except Exception:
        return "failed"
    return "sent" if isinstance(resp, dict) and resp.get("ok") else "failed"


def banner(title, text):
    subprocess.run(
        ["osascript", "-e", "on run argv",
         "-e", 'display notification (item 1 of argv) with title '
               '(item 2 of argv) sound name "Basso"',
         "-e", "end run", text[:200], title],
        capture_output=True, timeout=5)


def main(raw):
    now = time.time()
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "ts": round(now),
              "delivery": "error"}
    try:
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError("event is not an object")
        cwd = str(event.get("cwd") or "")
        session = (os.environ.get("CW_AGENT_NAME")
                   or os.path.basename(cwd) or "?")
        message = str(event.get("message") or "permission needed").strip()
        record.update(session=session, cwd=cwd, message=message,
                      session_id=event.get("session_id"))
        if is_team_lead_dir(cwd):
            record["delivery"] = "self"
        elif recently_sent(session, now):
            record["delivery"] = "deduped"
        else:
            record["delivery"] = deliver(session, cwd, message)
        if record["delivery"] not in ("sent", "deduped") or in_team_lead_tree(cwd):
            try:
                banner(f"{session} is waiting on a permission dialog", message)
            except Exception:
                pass
    except Exception as exc:
        record["error"] = repr(exc)[:300]
        try:
            banner("A session is waiting on a permission dialog",
                   "The permission-wait hook failed; check the terminals.")
        except Exception:
            pass
    finally:
        try:
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_MAX_BYTES:
                with open(LOG, "rb") as f:
                    f.seek(-LOG_MAX_BYTES // 2, 2)
                    tail = f.read().split(b"\n", 1)[-1]
                with open(LOG, "wb") as f:
                    f.write(tail)
            with open(LOG, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            pass
    return record


if __name__ == "__main__":
    try:
        main(sys.stdin.read())
    finally:
        sys.exit(0)
