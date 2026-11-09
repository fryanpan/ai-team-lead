#!/usr/bin/env python3
"""Daily interface-feedback ping for the plugins and MCP services a session uses.

Subcommands
  record            PostToolUse hook. Notes which service a tool call belonged to
                    and, in the same write, asks if any service is due. Sessions
                    woken by channel events rather than typed prompts make tool
                    calls, so this covers them.
  ask [EVENT]       Prompt hook. Asks if any service is due, without stamping use.
  collect           Stop hook. Reads the agent's last messages for an
                    ```interface-feedback``` block and stores each line.
  status            Prints the agent's last hook outcome: asked (with which
                    services were answered), nothing due, could not run, or
                    never run.
  add  ...          Append one feedback line for a service by hand.
  read ...          Print feedback lines for a service (run by the owner).

When a service is due
  A service is due when it has use that no ask has covered yet, that use began at
  least an hour ago, and the agent has not been asked about it today. "Began" is
  the first uncovered use, so steady use is asked about after an hour of use, and
  use late at night is still asked about the next day.

State is per AGENT, not per session, so a /clear or a respawn does not ask again.

Design notes
  * The agent answers by writing a fenced block in its normal reply; the Stop hook
    stores it. No Bash call, so no shell expansion of free text and no permission
    dialog on peers with narrow allow lists.
  * No network, stdlib only. Hooks never print to the session on failure, but
    every failure is written to errors.log and shown by `status`, so "could not
    run" never looks like "nothing due".
  * One locked read-modify-write per hook call.
  * Subagents are skipped (payload carries agent_id); they report to their parent.
  * Sessions with neither CW_AGENT_NAME nor FEEDBACK_AGENT_NAME are skipped.
"""
import argparse
import datetime
import fcntl
import json
import os
import re
import sys
import time
import traceback

GAP_SECONDS = 3600
REFRESH_SECONDS = 600
FORGET_SECONDS = 7 * 86400
TRANSCRIPT_TAIL_BYTES = 200_000
BLOCK_RE = re.compile(r"```interface-feedback[^\n]*\n(.*?)```", re.S)


def base_dir():
    return os.environ.get("INTERFACE_FEEDBACK_DIR") or os.path.expanduser(
        "~/Library/Application Support/team-lead/interface-feedback")


def safe(name):
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def day_of(epoch):
    return datetime.date.fromtimestamp(epoch).isoformat()


def stamp(epoch):
    return datetime.datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def service_from_tool(tool_name, tool_input=None):
    """Map a tool call to one service name, or None if it is not one we track."""
    if tool_name.startswith("mcp__"):
        server = tool_name.split("__")[1] if tool_name.count("__") >= 2 else ""
        if not server:
            return None
        if server.startswith("plugin_"):
            rest = server[len("plugin_"):]
            plugin, _, srv = rest.partition("_")
            if rest.count("_") > 1:  # ambiguous split: prefer an installed plugin name
                for name in sorted(installed_plugins(), key=len, reverse=True):
                    if rest == name or rest.startswith(name + "_"):
                        plugin, srv = name, rest[len(name) + 1:]
                        break
            if not srv or srv == plugin:
                return plugin
            return plugin + "/" + srv
        return server
    if tool_name == "Skill":
        skill = (tool_input or {}).get("skill") or (tool_input or {}).get("name") or ""
        if ":" in skill:
            return skill.split(":", 1)[0]
    return None


def installed_plugins():
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    data = load_json(os.path.join(cfg, "plugins", "installed_plugins.json"))
    return {k.split("@", 1)[0] for k in (data.get("plugins") or {})}


def agent_name():
    return os.environ.get("CW_AGENT_NAME") or os.environ.get("FEEDBACK_AGENT_NAME") or ""


def state_path():
    return os.path.join(base_dir(), "state", safe(agent_name()) + ".json")


def load_json(path):
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
        state = load_json(path)
        result = fn(state)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
        return result


def take_due(state, now):
    """Mark every due service as asked; return their names. Mutates state."""
    today = day_of(now)
    services = state.setdefault("services", {})
    due = []
    for svc, e in sorted(services.items()):
        since = e.get("pending_since")
        if (isinstance(since, (int, float)) and now - since >= GAP_SECONDS
                and e.get("asked_day") != today):
            due.append(svc)
            e["asked_day"] = today
            e.pop("pending_since", None)
    for svc in [s for s, e in services.items()
                if now - e.get("last_used", now) > FORGET_SECONDS]:
        del services[svc]
    if due:
        state["last_outcome"] = {"ts": now, "outcome": "asked", "services": due,
                                 "answered": []}
    state["last_run"] = now
    return due


def build_ask(services, event):
    names = ", ".join(services)
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": (
        "Interface feedback (about once a day, so the tools you use can improve). "
        "You used these plugins/services and have not given feedback on them today: "
        + names + ". At the END of your next reply, add one fenced block tagged "
        "interface-feedback, one line per service, fields separated by ' | ':\n"
        "```interface-feedback\n"
        "<service> | what went well | what was confusing or broke | one concrete improvement\n"
        "```\n"
        "If a service had nothing notable, write: <service> | fine | none | none. "
        "A hook stores the block; do not run a command for it, message anyone, or "
        "file a review item. Keep it to those lines.")}}


def log_error(event, exc):
    now = time.time()
    line = "%s %s %s: %s\n" % (stamp(now), safe(agent_name()) or "-", event, exc)
    try:
        os.makedirs(base_dir(), exist_ok=True)
        with open(os.path.join(base_dir(), "errors.log"), "a") as f:
            f.write(line)
    except OSError:
        sys.stderr.write("interface_feedback: COULD NOT RUN and could not log: " + line)


def eligible(payload):
    return not payload.get("agent_id") and bool(agent_name())


def record(payload, now):
    if not eligible(payload):
        return None
    svc = service_from_tool(payload.get("tool_name") or "", payload.get("tool_input"))
    if not svc:
        return None

    if not needs_write(load_json(state_path()), svc, now):
        return None

    def upd(state):
        # Ask before stamping this call, so the call that ends a long gap counts
        # its previous use, not itself. Use after today's ask gets a new
        # pending_since; take_due holds it until tomorrow.
        due = take_due(state, now)
        e = state["services"].setdefault(svc, {})
        e["last_used"] = now
        e.setdefault("pending_since", now)
        return due

    due = locked_update(state_path(), upd)
    return build_ask(due, "PostToolUse") if due else None


def needs_write(state, svc, now):
    """False when this call would change nothing worth a locked write: the
    service already has pending use, nothing is due, last_used and last_run
    are under REFRESH_SECONDS old, and no error was logged since last_run."""
    services = state.get("services")
    if not isinstance(services, dict):
        return True
    e = services.get(svc)
    if not e or "pending_since" not in e:
        return True
    last_run = state.get("last_run", 0)
    if now - e.get("last_used", 0) > REFRESH_SECONDS or now - last_run > REFRESH_SECONDS:
        return True
    try:  # an error since the last run: write, so status can see the recovery
        if os.stat(os.path.join(base_dir(), "errors.log")).st_mtime >= last_run:
            return True
    except OSError:
        pass
    today = day_of(now)
    return any(isinstance(x.get("pending_since"), (int, float))
               and now - x["pending_since"] >= GAP_SECONDS and x.get("asked_day") != today
               for x in services.values())


def ask(payload, now, event):
    if not eligible(payload) or not os.path.exists(state_path()):
        return None
    due = locked_update(state_path(), lambda s: take_due(s, now))
    return build_ask(due, event) if due else None


def open_services(state, now):
    """Services in today's ask that have no answer stored yet."""
    last = state.get("last_outcome") or {}
    if last.get("outcome") != "asked" or day_of(last.get("ts", 0)) != day_of(now):
        return []
    return [s for s in last.get("services") or [] if s not in (last.get("answered") or [])]


def read_tail(path):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - TRANSCRIPT_TAIL_BYTES))
        return f.read().decode("utf-8", "replace")


def entry_day(ts):
    """Local day of a transcript timestamp, or None if it cannot be read."""
    try:
        return day_of(datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
    except (AttributeError, ValueError):
        return None


def assistant_texts(raw, today):
    """Text of today's assistant messages. Older blocks in the tail were stored
    on the day they were written, or are too old to store now."""
    for line in raw.splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") != "assistant":
            continue
        day = entry_day(o.get("timestamp"))
        if day is not None and day != today:
            continue
        for b in (o.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "text":
                yield b.get("text") or ""


def collect(payload, now):
    if not eligible(payload):
        return None
    tp = payload.get("transcript_path")
    if not tp or not os.path.exists(tp):
        return None
    today = day_of(now)
    if not open_services(load_json(state_path()), now):
        return None  # no ask waiting for an answer: skip reading the transcript
    rows = []
    for text in assistant_texts(read_tail(tp), today):
        for block in BLOCK_RE.findall(text):
            for line in block.splitlines():
                parts = [p.strip() for p in line.split(" | ")]
                if len(parts) >= 4 and parts[0] and not parts[0].startswith("<"):
                    rows.append((parts[0], parts[1], parts[2], " | ".join(parts[3:])))
    if not rows:
        return None

    def upd(state):
        done = state.setdefault("recorded", {})
        fresh = [r for r in rows if done.get(r[0]) != today]
        for r in fresh:
            done[r[0]] = today
        last = state.get("last_outcome") or {}
        if last.get("outcome") == "asked":
            got = set(last.get("answered") or []) | {r[0] for r in rows}
            last["answered"] = sorted(got & set(last.get("services") or []))
        state["last_run"] = now
        return fresh

    for svc, well, issue, improve in locked_update(state_path(), upd):
        append_row(svc, well, issue, improve, agent_name(), now)
    return None


def append_row(service, well, issue, improve, agent, now):
    path = os.path.join(base_dir(), safe(service) + ".jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    row = {"ts": stamp(now), "service": service, "agent": agent,
           "well": well, "issue": issue, "improve": improve}
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")
    return path


def hook_main(mode, event):
    try:
        payload = json.load(sys.stdin)
        now = float(os.environ.get("INTERFACE_FEEDBACK_NOW") or time.time())
        if mode == "record":
            out = record(payload, now)
        elif mode == "collect":
            out = collect(payload, now)
        else:
            out = ask(payload, now, event)
        if out:
            print(json.dumps(out))
    except Exception as e:  # never wedge the session; make the failure visible
        tb = traceback.extract_tb(e.__traceback__)
        where = " at line %d" % tb[-1].lineno if tb else ""
        log_error(mode, "%s: %s%s" % (type(e).__name__, e, where))
    return 0


def cmd_status(a):
    """Exit 0: ran (asked, or nothing due). Exit 1: could not run. Exit 2: no
    hook run recorded for this agent, so nothing can be said."""
    now = float(os.environ.get("INTERFACE_FEEDBACK_NOW") or time.time())
    agent = a.agent or agent_name()
    if not agent:
        print("could not run: no agent name (CW_AGENT_NAME and FEEDBACK_AGENT_NAME unset); "
              "the hooks skip unnamed sessions")
        return 1
    os.environ["CW_AGENT_NAME"] = agent
    last_err = None
    try:
        with open(os.path.join(base_dir(), "errors.log")) as f:
            mine = [l for l in f if l.split(" ", 2)[1:2] == [safe(agent)]]
        last_err = mine[-1].strip() if mine else None
    except OSError:
        pass
    state = load_json(state_path())
    last_run = state.get("last_run", 0)
    if last_err:
        try:
            err_ts = datetime.datetime.fromisoformat(last_err.split(" ", 1)[0]).timestamp()
        except ValueError:
            err_ts = now
        if err_ts >= last_run and now - err_ts < 86400:
            print("could not run: " + last_err)
            return 1
    if not last_run:
        print("never run: no hook run recorded for %s (plugin not loaded, or no MCP or "
              "Skill call yet) at %s" % (agent, state_path()))
        return 2
    last = state.get("last_outcome") or {}
    if last.get("outcome") == "asked" and day_of(last["ts"]) == day_of(now):
        answered = last.get("answered") or []
        waiting = open_services(state, now)
        print("asked at %s about %s; answered: %s%s"
              % (stamp(last["ts"]), ", ".join(last["services"]),
                 ", ".join(answered) or "none",
                 "; unanswered: " + ", ".join(waiting) if waiting else ""))
        return 0
    print("nothing due (last run %s; %d services tracked for %s)"
          % (stamp(last_run), len(state.get("services", {})), agent))
    return 0


def cmd_add(a):
    try:
        path = append_row(a.service, a.well, a.issue, a.improve, a.agent or agent_name(),
                          time.time())
    except OSError as e:
        print("interface_feedback: COULD NOT WRITE feedback for %r: %s" % (a.service, e),
              file=sys.stderr)
        return 1
    print("recorded feedback for %s -> %s" % (a.service, path))
    return 0


def cmd_read(a):
    path = os.path.join(base_dir(), safe(a.service) + ".jsonl")
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
    if len(argv) > 1 and argv[1] in ("record", "ask", "collect"):
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
    st = sub.add_parser("status")
    st.add_argument("--agent", default="")
    st.set_defaults(fn=cmd_status)
    a = p.parse_args(argv[1:])
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
