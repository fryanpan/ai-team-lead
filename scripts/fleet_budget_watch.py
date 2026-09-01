#!/usr/bin/env python3
"""Rolling-window burn watch with a per-project floor. Zero LLM turns.

The gap this fills: fleet_burn_report.py answers "who burned the most TODAY",
and the token-watch cron reads the WEEKLY meter 3x/day. Neither can see the
failure that actually cost us two accounts on 2026-08-31 — one project fanning
out subagents hard enough to exhaust a **5-hour session window** in about two
hours, entirely between two checks of a daily instrument.

So this measures the thing that breaks: burn inside a trailing window, split by
project, against a priority order.

The policy is a FLOOR, not a CAP (Bryan, 2026-08-31): "I want workspaces to keep
moving as fast as we have tokens for. But I don't want to sacrifice higher
priority projects like QB." A low-priority project is free to consume the whole
window while the protected projects are idle. It gets throttled only when its
burn is on track to eat the reserve the protected ones would need.

Flags:
  --window-hours N    trailing window (default 5, the session-limit window)
  --json              machine-readable output
  --notify            send a push when the verdict is BREACH
  --wake              hive-message Team Lead when the verdict TURNS to BREACH
  --state PATH        verdict state file (default under Application Support)

What it does NOT do: throttle anyone. Pausing or slowing a peer's workflow is
Tier 2 in docs/process/token-control.md — a judgement call, not a threshold. The
script's whole job is to make sure that call gets made inside 15 minutes instead
of at the next daily read, which is the failure that cost two accounts in one
evening on 2026-08-31. Detection on a timer, action by a person or by Team Lead.
"""
import json, os, re, subprocess, sys, glob, datetime

def argval(flag, default, cast=str):
    return cast(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default

HOME = os.path.expanduser("~")
PROJ = os.path.join(HOME, ".claude", "projects")
WINDOW_H = argval("--window-hours", 5.0, float)
AS_JSON = "--json" in sys.argv
NOTIFY = "--notify" in sys.argv
WAKE = "--wake" in sys.argv
HIVE = "http://127.0.0.1:7900/send-message"
TEAM_LEAD_STABLE_ID = "6e87a52503d5"
# While a breach persists, re-wake on this cadence. A breach that is still true
# an hour later is still costing quota, and one notification that scrolled past
# is indistinguishable from none — the same "surfaced once" failure the whole
# script exists to fix.
REWAKE_MINUTES = 60
STATE = argval("--state", os.path.join(
    HOME, "Library", "Application Support", "team-lead", "budget-watch.json"))

# ---------------------------------------------------------------- policy
# Protected projects get a reserved share of the window. The reserve is what we
# refuse to let a lower-priority project spend, NOT an allocation they must use.
# Keyed on the project directory name, which is what the transcript path encodes.
#
# Keep this list SHORT and tied to committed weekly goals. A project that is not
# named here is unprotected, which is the correct default: most weeks most
# projects are not load-bearing, and protecting everything protects nothing.
PROTECTED = {
    "project-alpha": 0.30,   # ClientOrg Project Beta — this week's goal 2
    "ai-team-lead": 0.10,            # coordination has to survive a squeeze
}
# Below this share of the window, nobody is throttled regardless of the split —
# a quiet fleet has no contention to resolve.
FLOOR_ENGAGE = 0.55

def encode(c): return re.sub(r"[/_.]", "-", c)
def sh(a): return subprocess.run(a, capture_output=True, text=True).stdout

def project_of(transcript_dir):
    """Map an encoded project dir back to a coarse project key.

    Worktrees live under the parent repo, so a session in
    .../project-alpha/worktrees/feature-worktree/... must bill to
    project-alpha — otherwise a project dodges its own budget simply by
    working in a worktree, which is where the heaviest work usually happens.
    """
    name = os.path.basename(transcript_dir)
    parts = [p for p in name.split("-") if p]
    # the encoded dir is the full path with separators flattened; find the
    # segment right after the last "dev" marker
    for i, p in enumerate(parts):
        if p == "dev" and i + 1 < len(parts):
            rest = parts[i + 1:]
            # rejoin greedily against known repo dirs
            for take in range(len(rest), 0, -1):
                cand = "-".join(rest[:take])
                if os.path.isdir(os.path.join(HOME, "..", "dev", cand)) or \
                   os.path.isdir(os.path.join("/Volumes/Data/Users/bryanchan/dev", cand)):
                    return cand
            return rest[0]
    return name

def window_burn(path, cutoff):
    """Sum per-turn usage for turns at or after `cutoff` (an aware datetime).

    Dedupes on requestId: a transcript writes one record per content block and
    every one repeats the SAME usage object, so summing records counts each
    billed request once per block — 1.9x too high fleet-wide. See
    docs/process/learnings.md, "Every transcript token count we had was ~1.9x
    too high". Do not "simplify" this set away.
    """
    total = 0
    turns = 0
    seen = set()
    try:
        f = open(path)
    except OSError:
        return 0, 0
    with f:
        for line in f:
            try: o = json.loads(line)
            except Exception: continue
            ts = o.get("timestamp") or ""
            try:
                dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
            if dt < cutoff:
                continue
            msg = o.get("message") or {}
            u = msg.get("usage")
            if not u:
                continue
            rid = o.get("requestId") or msg.get("id")
            if rid is not None:
                if rid in seen:
                    continue
                seen.add(rid)
            turns += 1
            total += (u.get("input_tokens", 0) + u.get("output_tokens", 0)
                      + u.get("cache_creation_input_tokens", 0)
                      + u.get("cache_read_input_tokens", 0))
    return total, turns

def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(hours=WINDOW_H)

    # Walk EVERY project dir, not just running sessions. A session that has since
    # been killed still spent the window's tokens, and a subagent's transcript
    # outlives the turn that launched it — counting only live cwds is how the
    # heaviest consumer stays invisible.
    per_project = {}
    for d in glob.glob(os.path.join(PROJ, "*")):
        if not os.path.isdir(d):
            continue
        key = project_of(d)
        for tp in glob.glob(os.path.join(d, "*.jsonl")):
            # cheap skip: a file untouched since the cutoff has nothing in window
            try:
                if datetime.datetime.fromtimestamp(
                        os.path.getmtime(tp), datetime.timezone.utc) < cutoff:
                    continue
            except OSError:
                continue
            tok, turns = window_burn(tp, cutoff)
            if not tok:
                continue
            b = per_project.setdefault(key, {"tokens": 0, "turns": 0, "files": 0})
            b["tokens"] += tok
            b["turns"] += turns
            b["files"] += 1

    fleet = sum(b["tokens"] for b in per_project.values()) or 1
    rows = sorted(per_project.items(), key=lambda kv: -kv[1]["tokens"])
    for _, b in rows:
        b["share"] = b["tokens"] / fleet

    # ---- verdict -------------------------------------------------------
    # Contention exists only when the protected reserve cannot be met out of
    # what the unprotected projects are leaving unspent.
    reserve = sum(PROTECTED.values())
    unprotected = sum(b["tokens"] for k, b in per_project.items() if k not in PROTECTED)
    unprotected_share = unprotected / fleet
    headroom = 1.0 - unprotected_share          # what protected work could still take

    top = rows[0] if rows else None
    verdict, detail = "OK", ""
    if unprotected_share >= FLOOR_ENGAGE and headroom < reserve:
        verdict = "BREACH"
        offender = next((k for k, b in rows if k not in PROTECTED), None)
        detail = (f"unprotected work holds {unprotected_share:.0%} of the "
                  f"{WINDOW_H:g}h window, leaving {headroom:.0%} against a "
                  f"{reserve:.0%} reserve; top unprotected: {offender}")
    elif unprotected_share >= FLOOR_ENGAGE:
        verdict = "WATCH"
        detail = (f"unprotected work at {unprotected_share:.0%}; reserve still "
                  f"met ({headroom:.0%} free vs {reserve:.0%} needed)")

    out = {
        "checked_at": now.astimezone().isoformat(timespec="seconds"),
        "window_hours": WINDOW_H,
        "fleet_tokens": fleet,
        "reserve": reserve,
        "unprotected_share": round(unprotected_share, 4),
        "headroom": round(headroom, 4),
        "verdict": verdict,
        "detail": detail,
        "projects": [
            {"project": k, "tokens": b["tokens"], "turns": b["turns"],
             "share": round(b["share"], 4),
             "protected": k in PROTECTED}
            for k, b in rows
        ],
    }

    # read the previous verdict BEFORE overwriting, so we can fire only on a
    # transition into BREACH rather than every single run
    prev = {}
    try:
        with open(STATE) as f:
            prev = json.load(f)
    except (OSError, ValueError):
        pass

    should_wake = False
    if verdict == "BREACH":
        if prev.get("verdict") != "BREACH":
            should_wake = True                      # newly breached
        else:
            try:
                last = datetime.datetime.fromisoformat(prev.get("woke_at", ""))
                age = (now - last.astimezone(datetime.timezone.utc)).total_seconds() / 60
                should_wake = age >= REWAKE_MINUTES
            except (ValueError, TypeError):
                should_wake = True
    out["woke_at"] = (now.astimezone().isoformat(timespec="seconds")
                      if should_wake else prev.get("woke_at", ""))

    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE, "w") as f:
            json.dump(out, f, indent=2)
    except OSError:
        pass

    if AS_JSON:
        print(json.dumps(out, indent=2))
    else:
        print(f"Rolling burn — trailing {WINDOW_H:g}h to "
              f"{now.astimezone().strftime('%Y-%m-%d %H:%M')}\n")
        print(f"{'TOKENS':>14}  {'TURNS':>6}  {'SHARE':>6}  PROJECT")
        print("-" * 66)
        for k, b in rows:
            mark = "*" if k in PROTECTED else " "
            print(f"{b['tokens']:>14,}  {b['turns']:>6,}  {b['share']:>5.0%}  {mark}{k}")
        print("-" * 66)
        print(f"{fleet:>14,}  {sum(b['turns'] for _, b in rows):>6,}         "
              f"fleet total  (* = protected)\n")
        print(f"verdict: {verdict}" + (f" — {detail}" if detail else ""))

    if NOTIFY and verdict == "BREACH":
        subprocess.run(["osascript", "-e",
                        f'display notification "{detail}" with title "Fleet budget BREACH"'],
                       capture_output=True)

    if WAKE and should_wake:
        split = " · ".join(f"{k} {b['share']:.0%}" for k, b in rows[:4])
        text = (f"[budget-watch] BREACH on the trailing {WINDOW_H:g}h window. {detail}. "
                f"Split: {split}. "
                f"This is the 5h session-limit window, not the weekly meter. "
                f"Decide now whether to throttle the top unprotected project or let it "
                f"run — and say which, rather than re-reading the number next pass. "
                f"State: {STATE}")
        try:
            subprocess.run(
                ["curl", "-s", "-m", "5", "-X", "POST", HIVE,
                 "-H", "content-type: application/json",
                 "-d", json.dumps({"from_id": "budget-watch",
                                   "to_stable_id": TEAM_LEAD_STABLE_ID,
                                   "text": text})],
                capture_output=True)
        except Exception:
            pass

    return 2 if verdict == "BREACH" else 0

if __name__ == "__main__":
    sys.exit(main())
