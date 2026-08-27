#!/usr/bin/env python3
"""Attribute fleet turns and burn to what CAUSED them. Zero LLM turns.

`fleet_burn_report.py` answers "which session burned the most." This answers
"why did the fleet take 28,288 turns," which is the question the token-efficiency
goal actually turns on: burn is `turns x context size`, and only turns have moved.

Two things it does that the burn report does not:

  1. **Counts subagent turns.** Subagent transcripts live in
     `<project>/<session-id>/subagents/agent-*.jsonl`, a subdirectory. The burn
     report globs `<project>/*.jsonl`, so every subagent turn the fleet has ever
     run is missing from it — and therefore from the ledger and the baseline.

  2. **Classifies each parent turn by its root trigger** — the most recent user
     message that was not a tool result. A turn spent because Bryan asked for
     something is attributed to `human`; the whole tool loop that follows it is
     attributed there too, because that is the work he asked for. A turn spent
     because a channel event arrived is attributed to that channel, follow-on
     work included. That is the split that says whether the fleet is working or
     reacting.

Usage:
    turn_attribution.py --week 2026-08-17      # Monday of the week to attribute
    turn_attribution.py --date 2026-08-25      # a single day
    turn_attribution.py --week 2026-08-17 --by-session
    turn_attribution.py --week 2026-08-17 --json
"""
import json, os, re, sys, glob, datetime, collections

HOME = os.path.expanduser("~")
PROJ = os.path.join(HOME, ".claude", "projects")


def argval(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def days_wanted():
    week = argval("--week")
    if week:
        d0 = datetime.date.fromisoformat(week)
        return {(d0 + datetime.timedelta(days=i)).isoformat() for i in range(7)}, f"week of {week}"
    day = argval("--date", datetime.date.today().isoformat())
    return {day}, day


def local_day(ts):
    try:
        dt = datetime.datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
        return dt.astimezone().date().isoformat()
    except Exception:
        return None


def blank():
    return {"turns": 0, "total": 0, "cache_read": 0, "output": 0}


def add(bucket, usage):
    bucket["turns"] += 1
    bucket["cache_read"] += usage.get("cache_read_input_tokens", 0)
    bucket["output"] += usage.get("output_tokens", 0)
    bucket["total"] += sum(usage.get(k, 0) for k in (
        "input_tokens", "output_tokens",
        "cache_creation_input_tokens", "cache_read_input_tokens"))


def user_text(rec):
    """Return the user message's text, or the sentinel TOOL for a tool result."""
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "tool_result":
                    return TOOL
                parts.append(block.get("text") or "")
        return "\n".join(parts)
    return ""


TOOL = "\x00tool_result"


def classify(text):
    """Bucket a user message by what it is. Tool results never reach here —
    they continue whatever trigger came before them."""
    match = re.search(r'<channel source="([^"]+)"', text)
    if match:
        src = match.group(1)
        # normalise the two spellings of the workspaces plugin, which coexist
        # for the whole rename rollout and would otherwise split one bucket in two
        if "live-feedback" in src or "claude-workspaces" in src:
            src = "workspaces"
        elif src.startswith("plugin:"):
            src = src.split(":")[1]
        return f"channel:{src}"
    if "<local-command-stdout>" in text or text.strip().startswith("<command-name>"):
        return "slash-command"
    if not text.strip():
        return "unknown"
    return "human"


def scan_parent(path, days, buckets):
    """Walk one top-level session transcript, attributing each turn to the root
    trigger that is still in force at that point in the file.

    One API response is written to the transcript as one record PER CONTENT
    BLOCK — thinking, text, and each tool_use land as separate lines sharing a
    `message.id`, and every one of them repeats a `usage` object. Counting lines
    therefore counts each billed request about 1.9x. Measured on the week of
    2026-08-17: 83,092 records against 42,905 real responses, 17,133M against
    8,996M. Collapse on `message.id` and keep the LAST record — the earlier ones
    are partial streaming snapshots, and the last is the maximum in 100% of the
    16,637 split responses where they differ.
    """
    trigger = "unknown"
    seen = {}                                 # message.id -> (trigger, usage)
    with open(path, errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            kind = rec.get("type")
            if kind == "user":
                text = user_text(rec)
                if text != TOOL:              # a tool result continues the current trigger
                    trigger = classify(text)
                continue
            if kind != "assistant":
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage")
            if not usage or local_day(rec.get("timestamp")) not in days:
                continue
            seen[msg.get("id") or rec.get("uuid")] = (trigger, usage)
    for trig, usage in seen.values():
        add(buckets[trig], usage)


def scan_subagent(path, days, buckets):
    seen = {}                                 # see scan_parent on why this dedupes
    with open(path, errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage")
            if not usage or local_day(rec.get("timestamp")) not in days:
                continue
            seen[msg.get("id") or rec.get("uuid")] = usage
    for usage in seen.values():
        add(buckets["subagent"], usage)


def main():
    days, label = days_wanted()
    per_session = collections.defaultdict(lambda: collections.defaultdict(blank))

    for project in sorted(glob.glob(os.path.join(PROJ, "*"))):
        if not os.path.isdir(project):
            continue
        name = os.path.basename(project).replace("-Volumes-Data-Users-bryanchan-dev-", "")
        for transcript in glob.glob(os.path.join(project, "*.jsonl")):
            scan_parent(transcript, days, per_session[name])
        for transcript in glob.glob(os.path.join(project, "*", "subagents", "*.jsonl")):
            scan_subagent(transcript, days, per_session[name])

    fleet = collections.defaultdict(blank)
    for session in per_session.values():
        for cause, b in session.items():
            for k in ("turns", "total", "cache_read", "output"):
                fleet[cause][k] += b[k]

    if "--json" in sys.argv:
        print(json.dumps({"label": label,
                          "fleet": {k: dict(v) for k, v in fleet.items()},
                          "sessions": {s: {k: dict(v) for k, v in c.items()}
                                       for s, c in per_session.items()}}, indent=2))
        return

    total_turns = sum(b["turns"] for b in fleet.values())
    total_burn = sum(b["total"] for b in fleet.values())
    if not total_turns:
        print(f"No turns found for {label}.")
        return

    print(f"Turn attribution — {label}\n")
    print(f"{'TURNS':>7} {'%':>6}  {'BURN':>10} {'%':>6}  {'TOK/TURN':>9}  CAUSE")
    for cause, b in sorted(fleet.items(), key=lambda kv: -kv[1]["total"]):
        print(f"{b['turns']:>7,} {100*b['turns']/total_turns:>5.1f}%  "
              f"{b['total']/1e6:>9,.0f}M {100*b['total']/total_burn:>5.1f}%  "
              f"{b['total']/b['turns']/1000:>8,.0f}k  {cause}")
    print(f"{total_turns:>7,} {'100%':>6}  {total_burn/1e6:>9,.0f}M {'100%':>6}"
          f"  {total_burn/total_turns/1000:>8,.0f}k  fleet total")

    if "--by-session" in sys.argv:
        print()
        rows = sorted(per_session.items(),
                      key=lambda kv: -sum(b["total"] for b in kv[1].values()))
        for name, causes in rows:
            burn = sum(b["total"] for b in causes.values())
            turns = sum(b["turns"] for b in causes.values())
            if not turns:
                continue
            print(f"\n{name} — {burn/1e6:,.0f}M over {turns:,} turns")
            for cause, b in sorted(causes.items(), key=lambda kv: -kv[1]["total"]):
                if b["turns"]:
                    print(f"    {b['turns']:>6,} turns  {b['total']/1e6:>8,.0f}M  {cause}")


if __name__ == "__main__":
    main()
