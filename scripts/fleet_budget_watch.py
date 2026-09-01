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
  --decide TEXT       record a standing decision for this breach and stop re-asking
  --state PATH        verdict state file (default under Application Support)

A recorded decision is what separates a monitor from a nag. Without one, every
re-wake re-litigates the same call with no memory of the last one -- which is
the "surfaced it again" failure wearing a different hat. `--decide` stores the
call and the share it was made at; the watch then stays quiet until the picture
materially worsens (WORSEN_PP) or the decision goes stale (DECISION_TTL_MIN),
and every later wake carries the standing decision so it is amended rather than
made from scratch.

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
DECIDE = argval("--decide", "", str)
# A standing decision holds until the situation moves against it by this many
# percentage points of unprotected share, or until it simply ages out. Both are
# needed: "let it run" can be right at 72% and wrong at 85%, and it can also be
# right at 18:30 and stale by morning.
WORSEN_PP = 0.06
DECISION_TTL_MIN = 240
# How long the breach must stay GONE before a standing decision is discarded.
# Anything shorter treats a single quiet sample as the end of the episode.
EPISODE_OVER_MIN = 90
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
    "project-alpha": 0.30,   # this week's goal 2
    "ai-team-lead": 0.10,            # coordination has to survive a squeeze
}
# Below this share of the window, nobody is throttled regardless of the split —
# a quiet fleet has no contention to resolve.
FLOOR_ENGAGE = 0.55

# ---------------------------------------------------- the absolute ceiling
# Everything above this point measures the SPLIT between projects. A split is
# scale-free: the identical "unprotected holds 64%" line prints at 4% of the
# pool and at 96%. So the share verdict was green through both account
# exhaustions it was built to catch, and was not wrong -- it was answering a
# different question than the one that matters when the pool runs out.
#
# These thresholds are CALIBRATED, not chosen. Rolling 5h fleet burn was
# reconstructed hourly across the five days to 2026-09-01, against the known
# exhaustion events:
#
#   08-31 02:00   799.6M   <- peak; overnight burn-through
#   08-31 19:00   759.7M   <- evening, the run that cost the second account
#   09-01 14:00   671.9M
#
# Nothing survived above ~800M, so that is where the window empties. WATCH at
# 500M is roughly 60% of it -- early enough to act, high enough that an ordinary
# busy afternoon does not trip it. CEILING at 650M is the last point where a
# Tier 2 call still has time to matter.
#
# Two honest limits on this number, both of which argue for acting EARLY on it:
#   - It sums raw tokens, and cache reads bill far cheaper than fresh input.
#     The real limit is weighted, so this correlates with exhaustion rather than
#     measuring it.
#   - It is fleet-wide, while the limit is per-account. With the fleet on one
#     account at a time that is the same thing; it stops being so the moment
#     sessions are split across accounts.
#
# Re-derive them after any exhaustion event rather than trusting these forever.
WINDOW_WATCH_TOKENS = argval("--watch-tokens", 500_000_000, int)
WINDOW_CEILING_TOKENS = argval("--ceiling-tokens", 650_000_000, int)

def carry_decision(decision, verdict, now):
    """Should a standing decision survive this wake?

    A decision governs an EPISODE, not a sample. The original rule dropped it
    the instant one wake came back non-breach, which sounds like "the breach it
    was made about is over" and is not: the fleet oscillates across the
    threshold, so a single quiet sample between two breaches erased the decision
    and the next breach asked from scratch. That is exactly the hourly
    re-litigation --decide exists to stop, reintroduced by the code meant to
    scope it.

    An episode ends only once the breach has stayed gone for EPISODE_OVER_MIN.
    Staleness is handled separately by DECISION_TTL_MIN and WORSEN_PP.
    """
    if not decision:
        return None
    if verdict == "BREACH":
        decision.pop("clear_since", None)      # still the same episode
        return decision
    since = decision.get("clear_since")
    if not since:
        decision["clear_since"] = now.astimezone().isoformat(timespec="seconds")
        return decision
    if _mins_since_iso(since, now) >= EPISODE_OVER_MIN:
        return None                            # episode genuinely over
    return decision


def _mins_since_iso(iso, now):
    """Minutes between an ISO timestamp and now; a huge number if unparseable,
    so a corrupt value expires a decision rather than pinning it forever."""
    try:
        from datetime import datetime as _dt
        return (now.astimezone() - _dt.fromisoformat(iso)).total_seconds() / 60
    except Exception:
        return float("inf")


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

def transcripts_under(project_dir):
    """Every transcript billing to this project, subagents included.

    The plain `<dir>/*.jsonl` glob this replaced saw ONLY main-agent sessions.
    Subagent transcripts live one and two levels down --
    `<dir>/<session-id>/subagents/agent-*.jsonl` -- and there are thousands of
    them. Measured 2026-09-01: the old glob reported 185M for the trailing 5h
    while the true figure was 455M, a 2.4x undercount.

    That is this instrument's whole reason for existing, inverted. It was built
    after 2026-08-31, where the finding was that subagent fan-out was 77% of
    fleet burn and "invisible to the report we actually read" -- and it shipped
    with the same blindness, so it reported OK through the burn it was added to
    catch. A watcher that cannot see the dominant consumer is not a quieter
    watcher, it is a green light.
    """
    return glob.glob(os.path.join(project_dir, "**", "*.jsonl"), recursive=True)


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
        for tp in transcripts_under(d):
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
    fleet_tokens = sum(b["tokens"] for b in per_project.values())
    rows = sorted(per_project.items(), key=lambda kv: -kv[1]["tokens"])
    top_burner = rows[0][0] if rows else "nobody"
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
    # The absolute reading is checked FIRST and wins. When the window itself is
    # emptying, which project holds which share is a second-order question --
    # and reporting the split alone is what let two accounts die green.
    if fleet_tokens >= WINDOW_CEILING_TOKENS:
        verdict = "BREACH"
        detail = (f"5h window at {fleet_tokens/1e6:.0f}M, past the "
                  f"{WINDOW_CEILING_TOKENS/1e6:.0f}M ceiling -- nothing has "
                  f"survived above ~800M. Top burner: {top_burner}")
    elif fleet_tokens >= WINDOW_WATCH_TOKENS:
        verdict = "WATCH"
        detail = (f"5h window at {fleet_tokens/1e6:.0f}M, past the "
                  f"{WINDOW_WATCH_TOKENS/1e6:.0f}M watch line "
                  f"({fleet_tokens/WINDOW_CEILING_TOKENS:.0%} of ceiling). "
                  f"Top burner: {top_burner}")
    elif unprotected_share >= FLOOR_ENGAGE and headroom < reserve:
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

    # carry the standing decision forward, or replace it when --decide is passed
    if DECIDE:
        decision = {"text": DECIDE,
                    "at": now.astimezone().isoformat(timespec="seconds"),
                    "share": round(unprotected_share, 4)}
    else:
        decision = carry_decision(prev.get("decision") or None, verdict, now)
    out["decision"] = decision

    def _mins_since(iso):
        try:
            t = datetime.datetime.fromisoformat(iso)
            return (now - t.astimezone(datetime.timezone.utc)).total_seconds() / 60
        except (ValueError, TypeError):
            return None

    should_wake = False
    if verdict == "BREACH" and not DECIDE:
        if decision:
            # A standing decision silences the wake until the picture actually
            # changes. Silence is the POINT -- re-asking a question already
            # answered is what trains a human to ignore the channel.
            age = _mins_since(decision.get("at", ""))
            worsened = unprotected_share - decision.get("share", 0) >= WORSEN_PP
            should_wake = worsened or (age is None or age >= DECISION_TTL_MIN)
        elif prev.get("verdict") != "BREACH":
            should_wake = True                      # newly breached
        else:
            age = _mins_since(prev.get("woke_at", ""))
            should_wake = age is None or age >= REWAKE_MINUTES
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
        if decision:
            print(f"standing decision ({decision['at']}, at "
                  f"{decision['share']:.0%}): {decision['text']}")

    if NOTIFY and verdict == "BREACH":
        subprocess.run(["osascript", "-e",
                        f'display notification "{detail}" with title "Fleet budget BREACH"'],
                       capture_output=True)

    if WAKE and should_wake:
        split = " · ".join(f"{k} {b['share']:.0%}" for k, b in rows[:4])
        if decision:
            why = (f"Standing decision from {decision['at']} at "
                   f"{decision['share']:.0%}: \"{decision['text']}\". "
                   f"It is now {unprotected_share:.0%} — amend or re-affirm that call, "
                   f"do not make it from scratch.")
        else:
            why = ("Decide whether to throttle the top unprotected project or let it "
                   "run, then record it with --decide so this stops re-asking.")
        text = (f"[budget-watch] BREACH on the trailing {WINDOW_H:g}h window. {detail}. "
                f"Split: {split}. "
                f"This is the {WINDOW_H:g}h session-limit window, not the weekly meter. "
                f"{why} State: {STATE}")
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
