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
  least an hour ago, and the agent has not been asked about it today or in the
  last 12 hours. "Began" is
  the first uncovered use, so steady use is asked about after an hour of use, and
  use late at night is still asked about the next day.

State is per AGENT, not per session, so a /clear or a respawn does not ask again.

Design notes
  * The agent answers by writing a fenced block in its normal reply; the Stop hook
    stores it. No Bash call, so no shell expansion of free text and no permission
    dialog on peers with narrow allow lists.
  * No network, stdlib only. Hooks never print to the session on failure, but
    every failure is written to errors/<agent>.log and shown by `status`, so "could not
    run" never looks like "nothing due".
  * One locked read-modify-write per hook call.
  * A subagent's tool use (payload carries agent_id) counts for its parent agent;
    the ask and the answer happen in the parent's context.
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
import urllib.parse

GAP_SECONDS = 3600
MIN_ASK_INTERVAL = 12 * 3600
ANSWER_WINDOW = 24 * 3600
REFRESH_SECONDS = 600
LOG_MAX_BYTES = 256_000
COLLECT_RETRY_SECONDS = float(os.environ.get("INTERFACE_FEEDBACK_RETRY", "1"))
FORGET_SECONDS = 7 * 86400
TRANSCRIPT_TAIL_BYTES = 200_000
BLOCK_RE = re.compile(r"```interface-feedback[^\n]*\n(.*?)```", re.S)


def base_dir():
    return os.environ.get("INTERFACE_FEEDBACK_DIR") or os.path.expanduser(
        "~/Library/Application Support/team-lead/interface-feedback")


def safe(name):
    """Reversible file-name form: distinct names never share a file."""
    return urllib.parse.quote(name, safe="-.")


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


def locked_update(path, fn, write_on_error=False):
    """Locked read-modify-write. With write_on_error, state changes made before
    fn raised are still saved (then the error is re-raised)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        state = load_json(path)
        try:
            result = fn(state)
        except Exception:
            if write_on_error:
                save_json(path, state)
            raise
        save_json(path, state)
        return result


def save_json(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def is_due(e, now):
    """The one due rule: uncovered use began GAP_SECONDS ago, the service was not
    asked today, and the last ask was MIN_ASK_INTERVAL ago (so an ask at 23:30 is
    not repeated at 00:31)."""
    since = e.get("pending_since")
    return (isinstance(since, (int, float)) and now - since >= GAP_SECONDS
            and e.get("asked_day") != day_of(now)
            and now - e.get("asked_ts", 0) >= MIN_ASK_INTERVAL)


def take_due(state, now):
    """Mark every due service as asked; return their names. Mutates state."""
    services = state.setdefault("services", {})
    due = [svc for svc, e in sorted(services.items()) if is_due(e, now)]
    for svc in due:
        e = services[svc]
        e["asked_day"] = day_of(now)
        e["asked_ts"] = now
        e.pop("pending_since", None)
    for svc in [s for s, e in services.items()
                if now - e.get("last_used", now) > FORGET_SECONDS]:
        del services[svc]
    asks = state.setdefault("open_asks", {})
    for svc in [s for s, t in asks.items() if now - t > ANSWER_WINDOW]:
        del asks[svc]  # unanswered for a day: dropped, still shown by status
    for svc in due:
        asks[svc] = now
    if due:
        state["last_outcome"] = {"ts": now, "outcome": "asked", "services": due}
    state["last_run"] = now
    return due


def build_ask(services, event):
    names = ", ".join(services)
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": (
        "Interface feedback (about once a day, so the tools you use can improve). "
        "You used these plugins/services and have not given feedback on them today: "
        + names + ". At the END of your next reply, add one fenced block tagged "
        "interface-feedback, one line per service, fields separated by ' | '. Write "
        "it as reply text even if you would otherwise end the turn on a tool call "
        "with no text:\n"
        "```interface-feedback\n"
        "<service> | what went well | what was confusing or broke | one concrete improvement\n"
        "```\n"
        "If a service had nothing notable, write: <service> | fine | none | none. "
        "A hook stores the block; do not run a command for it, message anyone, or "
        "file a review item. Keep it to those lines.")}}


def errors_path(agent=None):
    return os.path.join(base_dir(), "errors", safe(agent or agent_name() or "-") + ".log")


def log_error(event, exc):
    now = time.time()
    msg = " ".join(str(exc).split())  # one line, so the epoch always leads it
    line = "%r %s %s: %s\n" % (now, stamp(now), event, msg)
    path = errors_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".1")
        with open(path, "a") as f:
            f.write(line)
    except OSError:
        sys.stderr.write("interface_feedback: COULD NOT RUN and could not log: " + line)


def last_error(agent=None):
    """(epoch, line) of this agent's newest logged error, or (None, None)."""
    try:
        with open(errors_path(agent)) as f:
            lines = f.read().splitlines()
    except OSError:
        return None, None
    if not lines:
        return None, None
    ts, _, rest = lines[-1].partition(" ")
    try:
        return float(ts), rest
    except ValueError:
        return None, None  # not a line this script wrote


def eligible(payload):
    return not payload.get("agent_id") and bool(agent_name())


def record(payload, now):
    if not agent_name():
        return None
    svc = service_from_tool(payload.get("tool_name") or "", payload.get("tool_input"))
    if not svc:
        return None
    sub = bool(payload.get("agent_id"))
    if not needs_write(load_json(state_path()), svc, now, sub):
        return None

    def upd(state):
        # A subagent's use counts for its parent agent, but the ask goes to the
        # parent's context, so a subagent call never takes it.
        # Otherwise ask before stamping this call, so the call that ends a long
        # gap counts its previous use, not itself.
        due = [] if sub else take_due(state, now)
        e = state.setdefault("services", {}).setdefault(svc, {})
        e["last_used"] = now
        if svc not in due:
            e.setdefault("pending_since", now)
        if sub:
            state["last_run"] = now
        return due

    due = locked_update(state_path(), upd)
    return build_ask(due, "PostToolUse") if due else None


def needs_write(state, svc, now, sub=False):
    """False when this call would change nothing worth a locked write: nothing
    is due, last_run is under REFRESH_SECONDS old, no error was logged since
    last_run, and (for a tool call) the service already has pending use
    stamped under REFRESH_SECONDS ago."""
    services = state.get("services")
    if not isinstance(services, dict):
        return True
    last_run = state.get("last_run", 0)
    if now - last_run > REFRESH_SECONDS:
        return True
    if svc is not None:  # svc None: a prompt, which stamps no use
        e = services.get(svc)
        if not e or "pending_since" not in e or now - e.get("last_used", 0) > REFRESH_SECONDS:
            return True
    try:  # an error since the last run: write, so status can see the recovery
        if os.stat(errors_path()).st_mtime >= last_run:
            return True
    except OSError:
        pass
    return not sub and any(is_due(x, now) for x in services.values())


def ask(payload, now, event):
    if not eligible(payload) or not os.path.exists(state_path()):
        return None
    if not needs_write(load_json(state_path()), None, now):
        return None
    due = locked_update(state_path(), lambda s: take_due(s, now))
    return build_ask(due, event) if due else None


def open_services(state, now):
    """Services asked about in the last ANSWER_WINDOW with no answer stored yet."""
    return sorted(svc for svc, t in (state.get("open_asks") or {}).items()
                  if now - t <= ANSWER_WINDOW)


def read_tail(path):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - TRANSCRIPT_TAIL_BYTES))
        return f.read().decode("utf-8", "replace")


def entry_epoch(ts):
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def assistant_texts(raw, since):
    """(epoch, text) of assistant messages written at or after `since`. A block
    from before the ask it would answer is never stored, so a block left in the
    tail is not stored again on a later day."""
    for line in raw.splitlines():
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("type") != "assistant":
            continue
        ts = entry_epoch(o.get("timestamp"))
        if ts is None or ts < since:
            continue  # no timestamp: cannot tell it came after the ask
        for b in (o.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "text":
                yield ts, b.get("text") or ""


def collect(payload, now):
    if not eligible(payload):
        return None
    tp = payload.get("transcript_path")
    if not tp or not os.path.exists(tp):
        return None
    asks = load_json(state_path()).get("open_asks") or {}
    waiting = {svc: asks[svc] for svc in open_services({"open_asks": asks}, now)}
    if not waiting:
        return None  # no ask waiting for an answer: skip reading the transcript
    rows = find_rows(tp, waiting)
    if not rows:  # the Stop hook can fire before the last message is flushed
        time.sleep(COLLECT_RETRY_SECONDS)
        rows = find_rows(tp, waiting)
    if not rows:
        return None
    return store_rows(rows, waiting, now)


def find_rows(tp, waiting):
    rows = {}  # service -> fields; the newest block wins
    for ts, text in assistant_texts(read_tail(tp), min(waiting.values())):
        for block in BLOCK_RE.findall(text):
            for line in block.splitlines():
                parts = [p.strip() for p in line.split(" | ")]
                svc = parts[0] if parts else ""
                if len(parts) >= 4 and svc in waiting and ts >= waiting[svc]:
                    rows[svc] = (parts[1], parts[2], " | ".join(parts[3:]))
    return rows


def store_rows(rows, waiting, now):
    def upd(state):
        # Store under the lock, and close an ask only once its row is written:
        # a failed write leaves the ask open for the next Stop.
        open_now = state.setdefault("open_asks", {})
        answered = state.setdefault("answered", {})
        for svc, (well, issue, improve) in sorted(rows.items()):
            if open_now.get(svc) != waiting[svc]:
                continue  # another Stop already stored it, or it was re-asked
            append_row(svc, well, issue, improve, agent_name(), now)
            del open_now[svc]
            answered[svc] = now
            state["last_run"] = now

    locked_update(state_path(), upd, write_on_error=True)
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
    state = load_json(state_path())
    last_run = state.get("last_run", 0)
    err_ts, err = last_error(agent)
    if err_ts is not None and err_ts >= last_run and now - min(err_ts, now) < 86400:
        print("could not run: " + err)
        return 1
    if not last_run:
        print("never run: no hook run recorded for %s (plugin not loaded, or no MCP or "
              "Skill call yet) at %s" % (agent, state_path()))
        return 2
    last = state.get("last_outcome") or {}
    waiting = open_services(state, now)
    if last.get("outcome") == "asked" and day_of(last["ts"]) == day_of(now) or waiting:
        answered = sorted(s for s, t in (state.get("answered") or {}).items()
                          if day_of(t) == day_of(now))
        print("asked at %s about %s; answered today: %s%s"
              % (stamp(last.get("ts", 0)), ", ".join(last.get("services") or []),
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
