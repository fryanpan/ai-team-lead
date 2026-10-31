#!/usr/bin/env python3
"""Daily interface-feedback ping for the plugins and MCP services a session uses.

Subcommands
  record            PostToolUse hook. Notes which service a tool call belonged to,
                    and also asks (same rules) if one is due, so sessions woken by
                    channel events rather than typed prompts are covered.
  ask [EVENT]       Prompt hook. If a service was used today, was last used at
                    least an hour ago and has not been asked about today, inject
                    ONE additionalContext asking for short feedback on all of them.
  add  ...          Append one feedback line for a service (run by the agent).
  read ...          Print feedback lines for a service (run by the owner).

Design notes
  * No network, stdlib only, and the hook paths fail open and silent: a broken
    hook must never wedge a session or add a turn. Only `add` reports failure.
  * No Stop-hook blocking: that forces an extra full-context turn. The ask rides
    on a turn that is happening anyway.
  * Subagents are skipped (payload carries agent_id); they report to their parent.
  * Sessions with neither CW_AGENT_NAME nor FEEDBACK_AGENT_NAME are skipped:
    those are interactive or headless sessions outside the fleet, with nobody
    to attribute feedback to and nobody expected to write it.
"""
import argparse
import datetime
import fcntl
import json
import os
import re
import sys
import time

GAP_SECONDS = 3600
STATE_KEEP_SECONDS = 7 * 86400


def base_dir():
    return os.environ.get("INTERFACE_FEEDBACK_DIR") or os.path.expanduser(
        "~/Library/Application Support/team-lead/interface-feedback")


def safe(name):
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def day_of(epoch):
    return datetime.date.fromtimestamp(epoch).isoformat()


def service_from_tool(tool_name, tool_input=None):
    """Map a tool call to one service name, or None if it is not one we track."""
    if tool_name.startswith("mcp__"):
        server = tool_name.split("__")[1] if tool_name.count("__") >= 2 else ""
        if not server:
            return None
        if server.startswith("plugin_"):
            rest = server[len("plugin_"):]
            plugin, _, srv = rest.partition("_")
            if not srv or srv == plugin:
                return plugin
            return plugin + "/" + srv
        return server
    if tool_name == "Skill":
        skill = (tool_input or {}).get("skill") or (tool_input or {}).get("name") or ""
        if ":" in skill:
            return skill.split(":", 1)[0]
    return None


def is_fleet_session():
    return bool(os.environ.get("CW_AGENT_NAME") or os.environ.get("FEEDBACK_AGENT_NAME"))


def agent_name():
    return os.environ.get("CW_AGENT_NAME") or os.environ.get("FEEDBACK_AGENT_NAME") or ""


def state_path(session_id):
    return os.path.join(base_dir(), "state", safe(session_id) + ".json")


def load_state(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def locked_update(path, fn):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        state = load_state(path)
        fn(state)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)


def prune(state_dir, now):
    try:
        for n in os.listdir(state_dir):
            p = os.path.join(state_dir, n)
            if now - os.path.getmtime(p) > STATE_KEEP_SECONDS:
                os.remove(p)
    except OSError:
        pass


def record(payload, now):
    if payload.get("agent_id") or not is_fleet_session():
        return
    sid = payload.get("session_id")
    svc = service_from_tool(payload.get("tool_name") or "", payload.get("tool_input"))
    if not sid or not svc:
        return

    # Ask BEFORE stamping this call: a session woken by a channel event may
    # never see a typed prompt, but it makes tool calls. The gap is measured
    # against the previous use, so the call that ends a long gap triggers it.
    out = ask(payload, now, "PostToolUse")

    def upd(state):
        entry = state.setdefault(svc, {})
        entry["last_used"] = now

    locked_update(state_path(sid), upd)
    prune(os.path.join(base_dir(), "state"), now)
    return out


def build_ask(services, script):
    names = ", ".join(services)
    return (
        "Interface feedback (about once a day, so the tools you use can improve). "
        "You used these plugins/services today and have not given feedback on them yet: "
        + names + ". For EACH, in 2-4 lines: what went well, what was confusing or "
        "broke, and one concrete improvement. Record it with one command per service "
        "(batch them in a single Bash call, before or alongside your next step; do not "
        "message anyone and do not file a review item):\n"
        "python3 \"" + script + "\" add --service <name> --well \"...\" "
        "--issue \"...\" --improve \"...\" --agent \"" + agent_name() + "\"\n"
        "If a service had nothing notable, use --well \"fine\" --issue \"none\" --improve \"none\". "
        "Do not mention this ask to the user unless asked."
    )


def ask(payload, now, event):
    if payload.get("agent_id") or not is_fleet_session():
        return None
    sid = payload.get("session_id")
    if not sid:
        return None
    today = day_of(now)
    due = []

    def upd(state):
        for svc, e in sorted(state.items()):
            last = e.get("last_used")
            if (isinstance(last, (int, float)) and day_of(last) == today
                    and now - last >= GAP_SECONDS and e.get("asked_day") != today):
                due.append(svc)
                e["asked_day"] = today

    path = state_path(sid)
    if not os.path.exists(path):
        return None
    locked_update(path, upd)
    if not due:
        return None
    return {"hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": build_ask(due, os.path.abspath(__file__))}}


def hook_main(mode, event):
    try:
        payload = json.load(sys.stdin)
        now = float(os.environ.get("INTERFACE_FEEDBACK_NOW") or time.time())
        out = record(payload, now) if mode == "record" else ask(payload, now, event)
        if out:
            print(json.dumps(out))
    except Exception:
        pass  # fail open and quiet
    return 0


def feedback_file(service):
    return os.path.join(base_dir(), safe(service) + ".jsonl")


def cmd_add(a):
    row = {"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "service": a.service, "agent": a.agent or agent_name(),
           "well": a.well, "issue": a.issue, "improve": a.improve}
    path = feedback_file(a.service)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as e:
        print("interface_feedback: COULD NOT WRITE feedback for %r to %s: %s"
              % (a.service, path, e), file=sys.stderr)
        return 1
    print("recorded feedback for %s -> %s" % (a.service, path))
    return 0


def cmd_read(a):
    path = feedback_file(a.service)
    if not os.path.exists(path):
        print("no feedback file for %s (%s)" % (a.service, path), file=sys.stderr)
        return 0
    n = 0
    with open(path) as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if a.since and row.get("ts", "")[:10] < a.since:
                continue
            n += 1
            print("%s  %s  [%s]\n  well:    %s\n  issue:   %s\n  improve: %s"
                  % (row.get("ts"), row.get("service"), row.get("agent"),
                     row.get("well"), row.get("issue"), row.get("improve")))
    print("%d entries" % n)
    return 0


def main(argv):
    if len(argv) > 1 and argv[1] in ("record", "ask"):
        return hook_main(argv[1], argv[2] if len(argv) > 2 else "UserPromptSubmit")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add")
    s.add_argument("--service", required=True)
    s.add_argument("--well", required=True)
    s.add_argument("--issue", required=True)
    s.add_argument("--improve", required=True)
    s.add_argument("--agent", default="")
    s.set_defaults(fn=cmd_add)
    r = sub.add_parser("read")
    r.add_argument("--service", required=True)
    r.add_argument("--since", default="", help="YYYY-MM-DD")
    r.set_defaults(fn=cmd_read)
    a = p.parse_args(argv[1:])
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
