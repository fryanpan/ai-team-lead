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
# The script CANNOT read a /usage meter -- they live behind a slash command in a
# session pane, and driving a pane to scrape one is forbidden (see CLAUDE.md).
# So meter readings are entered by hand and carried in the state file, where the
# thing that makes them trustworthy is that their AGE is printed next to them.
RECORD_METER = argval("--record-meter", "", str)
METER_ALL = argval("--all-models", None, float)
METER_FABLE = argval("--fable", None, float)
METER_RESETS = argval("--resets", "", str)
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
# pool and at 96%. So the share verdict was green through every session-limit
# hit it was built to catch, and was not wrong -- it was answering a different
# question than the one that matters when the window empties.
#
# These thresholds are MEASURED, not chosen. Claude Code records every 5-hour
# rate-limit rejection in the transcript as
# `quotaLimits{"rateLimitType":"five_hour","status":"rejected"}`, so the exact
# moment each episode began is on disk. Reconstructing the rolling 5h burn at
# the first rejection of each episode (2026-08-29 to 2026-09-01):
#
#   08-29 12:38   507M      08-31 22:18   503M
#   08-31 15:27   472M      09-01 01:33   442M   <- lowest
#   08-31 17:04   695M      09-01 13:31   658M
#
# Six episodes, lowest 442M, median 507M. So the window can empty anywhere from
# ~440M up. CEILING sits below the lowest observed failure, not near the median:
# a threshold set at the median is green through half of the events it exists
# to predict. WATCH at 300M is the last point where reducing fan-out still
# changes the outcome.
#
# Three honest limits, all of which argue for acting EARLY rather than trusting
# the number precisely:
#   - It sums raw tokens, and cache reads bill far cheaper than fresh input, so
#     this correlates with exhaustion rather than measuring it. That is why the
#     spread is 442M-695M rather than a clean line.
#   - It is fleet-wide, while the limit is per-account. Same thing while the
#     fleet runs on one account; not once sessions are split across accounts.
#   - It is a leading indicator only. The EXACT signal is the rejection record
#     itself -- see check_rate_limit_hits in fleet_healthcheck.py, which reads
#     the same transcripts and needs no calibration at all.
#
# Re-derive after any new episode instead of trusting these forever.
WINDOW_WATCH_TOKENS = argval("--watch-tokens", 300_000_000, int)
WINDOW_CEILING_TOKENS = argval("--ceiling-tokens", 420_000_000, int)

def worsened_since(decision, unprotected_share, fleet_tokens):
    """Has the picture actually moved against a standing decision?

    The split alone cannot answer this. Unprotected share rises whenever a
    PROTECTED peer goes quiet, so a fleet whose total burn is FALLING can
    manufacture a rising share and wake a human to re-decide a breach that is
    receding. On 2026-09-02 23:00 that is exactly what happened: fleet total
    459M -> 454M, unprotected share 73% -> 82%, because project-alpha
    (protected) dropped 25% -> 16%. Nothing got worse; the alert said it had,
    and the wake cost a turn at 11pm.

    Same lesson as d7c2073 -- let the level decide severity, not the split --
    reaching the wake path, which that commit did not touch. A wake now needs
    BOTH: the split moved against the decision by WORSEN_PP, AND the fleet is
    burning more than when the decision was recorded.

    No margin on the level test on purpose. A second uncalibrated number is
    what this file already has too many of; the WORSEN_PP move is the
    magnitude test and the level is only a direction check.

    The split test saturates, though: once unprotected share is 99% it cannot
    move another WORSEN_PP, and a fleet that then doubles its burn would wake
    nobody. So a material rise in the LEVEL is sufficient on its own. The step
    reuses WORSEN_PP against the ceiling rather than introducing a third
    uncalibrated constant -- this file already has more of those than it can
    revalidate.

    A decision recorded before this field existed carries no `tokens`, so it
    falls back to the share-only test rather than going permanently silent.
    """
    at = decision.get("tokens")
    if at and fleet_tokens - at >= WORSEN_PP * WINDOW_CEILING_TOKENS:
        return True                      # burn itself ran away; split is moot
    if unprotected_share - decision.get("share", 0) < WORSEN_PP:
        return False
    if not at:
        return True
    return fleet_tokens > at


def wake_now(worsened, aged_out, mins_since_last_wake):
    """Gate a justified wake behind a floor on how often it can fire.

    A signal can be correct and still useless. On 2026-09-03 11:30 burn rose
    341M -> 381M -- a real 40M worsening that the level test rightly caught --
    twelve minutes after an escalation had gone to Bryan and was awaiting his
    answer. There was no second decision to make in those twelve minutes, and
    the decision branch had no REWAKE_MINUTES floor at all, so a fleet climbing
    steadily can wake on every run.

    The floor applies to ageing out as well. DECISION_TTL_MIN already clears it
    by a wide margin, but a floor that holds only because another constant
    happens to be larger is not a floor.

    This trades latency for signal: something that gets dramatically worse
    inside the window waits up to REWAKE_MINUTES to be reported. That is the
    right trade for a human who cannot act twice in an hour anyway, and the
    standing decision already names when the next reading is taken.
    """
    if not (worsened or aged_out):
        return False
    if mins_since_last_wake is None:
        return True
    return mins_since_last_wake >= REWAKE_MINUTES


def aged_into_a_question(decision, age_min, fleet_tokens, now_iso):
    """Has a standing decision aged out into something worth waking for?

    The TTL exists so a decision cannot govern an episode forever. It is not a
    reason to re-ask about an episode that is RECEDING. At 03:57 on 2026-09-03
    it woke a human for a fleet burning 304M against a decision recorded at
    410M -- the fourth consecutive wake reporting an escalation while burn fell
    151M, and the same shape as the split-vs-level bug in worsened_since: an
    alert firing on something that got better.

    Below the level the call was made at, the call is still comfortably right.
    Restart its clock instead, ratcheting the stored level down to the current
    one so a genuine rebound still fires through worsened_since.

    This cannot make a decision immortal. Once the breach itself clears, the
    verdict stops being BREACH and carry_decision discards the decision after
    EPISODE_OVER_MIN. Re-stamping only defers the question while the answer is
    visibly still yes.

    Mutates `decision` in place when it defers, so the caller writes the
    refreshed clock back to the state file.
    """
    if not (age_min is None or age_min >= DECISION_TTL_MIN):
        return False
    at = decision.get("tokens")
    if at and fleet_tokens < at:
        decision["at"] = now_iso
        decision["tokens"] = fleet_tokens
        return False
    return True


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


# Claude Code meters each model family separately, so a fleet can exhaust one
# sub-meter with the others barely touched. On 2026-09-03 `fryanpan@gmail.com`
# reached 100% on Fable while its all-models bar read 58%, and nothing here saw
# it: every figure this script produced summed the models together. Anything not
# in the map prints verbatim rather than being folded into "other" -- a model we
# do not recognise is exactly the one worth seeing by name.
MODEL_LABELS = {
    "claude-opus-4-8": "Opus 4.8",
    "claude-opus-5": "Opus 5",
    "claude-sonnet-5": "Sonnet",
    "claude-haiku-4-5-20251001": "Haiku",
    "claude-fable-5": "Fable",
    "claude-fable-5-1": "Fable 5.1",
}

def model_label(model_id):
    return MODEL_LABELS.get(model_id, model_id) if model_id else "unknown"


def active_account():
    """The account this machine is currently billing to, or None.

    Read from the real binary, never the shell function, and never from the
    auth-method line -- "Claude Max account" is true of two different pools.
    """
    try:
        r = subprocess.run([os.path.join(HOME, ".local", "bin", "claude"),
                            "auth", "status", "--json"],
                           capture_output=True, text=True, timeout=20)
        return (json.loads(r.stdout) or {}).get("email") or None
    except Exception:
        return None


def account_changed(prev, cur):
    """True only when two KNOWN and different accounts bracket a run.

    A switch breaks the series: burn before and after belongs to different
    pools and must not be compared or extrapolated across. Missing data is not
    a switch -- `auth status` failing would otherwise announce a pool change on
    every flaky run, which is worse than saying nothing.
    """
    return bool(prev and cur and prev != cur)


def meter_age_hours(entry, now):
    """How old a hand-entered /usage reading is, in hours, or None.

    The script cannot read a meter: they live behind `/usage` in a session pane
    and driving one to scrape it is forbidden. So a reading is entered by hand
    and its VALUE is worth exactly what its AGE says it is. An unlabelled stale
    number is what produced "roughly 24%" for a pool that was actually at 58%.
    """
    try:
        t = datetime.datetime.fromisoformat(entry["read_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.astimezone()
    return (now - t).total_seconds() / 3600.0


def pools_by_next_reset(ledger):
    """Account keys ordered by which pool resets soonest.

    This is the rotation trigger: the nearer OTHER-account reset is when the
    fleet switches. A pool with no recorded reset sorts last rather than
    raising -- an unknown reset should not be able to win the ordering and
    silently become the trigger.
    """
    far = datetime.datetime.max.replace(tzinfo=datetime.timezone.utc)
    def key(item):
        try:
            t = datetime.datetime.fromisoformat(item[1].get("resets") or "")
        except (TypeError, ValueError):
            return far
        return t.astimezone(datetime.timezone.utc) if t.tzinfo else t.astimezone()
    return [k for k, _ in sorted(ledger.items(), key=key)]


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
    by_model = {}
    seen = set()
    try:
        f = open(path)
    except OSError:
        return 0, 0, {}
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
            n = (u.get("input_tokens", 0) + u.get("output_tokens", 0)
                 + u.get("cache_creation_input_tokens", 0)
                 + u.get("cache_read_input_tokens", 0))
            total += n
            label = model_label(msg.get("model"))
            by_model[label] = by_model.get(label, 0) + n
    return total, turns, by_model

def verdict_for(fleet_tokens, unprotected_share, headroom, reserve,
                top_burner, rows):
    """(verdict, detail) for one window reading.

    THE LEVEL DECIDES THE SEVERITY; THE SPLIT ONLY ESCALATES IT.

    Concentration is scale-free -- the identical 61%/39% line prints at 189M
    and at 600M -- so it can never establish that anything is wrong on its own.
    It says WHO is spending, never HOW MUCH IS LEFT. Below the watch line the
    split is reported as context and the verdict stays OK.

    Shipped wrong on 2026-09-01 and caught the same evening: the absolute
    thresholds were added and checked first, but the two share rules were left
    as `elif` fallbacks -- which meant they only ever ran BELOW the watch line,
    exactly where a share is least informative. The watch then cried BREACH at
    189M, 45% of the watch line and 43% of the ceiling, while the window had
    fallen 265M in ninety minutes. That is the original green-through-six-
    exhaustions bug wearing its own fix as a disguise: a share deciding a
    verdict. A BREACH nobody needs to act on is how the next real one gets
    scrolled past.
    """
    concentrated = unprotected_share >= FLOOR_ENGAGE and headroom < reserve
    offender = next((k for k, b in rows if k not in PROTECTED), None)
    split = (f"unprotected work holds {unprotected_share:.0%}, leaving "
             f"{headroom:.0%} against a {reserve:.0%} reserve; "
             f"top unprotected: {offender}.")

    if fleet_tokens >= WINDOW_CEILING_TOKENS:
        return "BREACH", (
            f"{WINDOW_H:g}h window at {fleet_tokens/1e6:.0f}M, past the "
            f"{WINDOW_CEILING_TOKENS/1e6:.0f}M ceiling -- the lowest window we "
            f"have actually been rate-limited at is 442M. "
            f"Top burner: {top_burner}. {split}")

    if fleet_tokens >= WINDOW_WATCH_TOKENS:
        base = (f"{WINDOW_H:g}h window at {fleet_tokens/1e6:.0f}M, past the "
                f"{WINDOW_WATCH_TOKENS/1e6:.0f}M watch line "
                f"({fleet_tokens/WINDOW_CEILING_TOKENS:.0%} of ceiling). "
                f"Top burner: {top_burner}")
        # Concentration escalates a level that already matters, and only there.
        if concentrated:
            return "BREACH", (f"{base}. One project is also running away with "
                              f"it: {split}")
        return "WATCH", f"{base}. {split}"

    detail = (f"{WINDOW_H:g}h window at {fleet_tokens/1e6:.0f}M, "
              f"{fleet_tokens/WINDOW_WATCH_TOKENS:.0%} of the "
              f"{WINDOW_WATCH_TOKENS/1e6:.0f}M watch line. {split}")
    if concentrated:
        # Worth saying, not worth waking anyone: a concentrated small window is
        # what a single active project looks like, not a threat to the quota.
        detail += (" Concentrated, but the window is small enough that this is "
                   "who is working, not a risk.")
    return "OK", detail


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(hours=WINDOW_H)

    # Walk EVERY project dir, not just running sessions. A session that has since
    # been killed still spent the window's tokens, and a subagent's transcript
    # outlives the turn that launched it — counting only live cwds is how the
    # heaviest consumer stays invisible.
    per_project = {}
    fleet_by_model = {}
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
            tok, turns, by_model = window_burn(tp, cutoff)
            if not tok:
                continue
            b = per_project.setdefault(key, {"tokens": 0, "turns": 0, "files": 0})
            b["tokens"] += tok
            b["turns"] += turns
            b["files"] += 1
            for label, n in by_model.items():
                fleet_by_model[label] = fleet_by_model.get(label, 0) + n

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
    verdict, detail = verdict_for(fleet_tokens, unprotected_share, headroom,
                                  reserve, top_burner, rows)

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
                    "share": round(unprotected_share, 4),
                    "tokens": fleet_tokens}
    else:
        decision = carry_decision(prev.get("decision") or None, verdict, now)
    out["decision"] = decision

    # WHICH POOL did this measure? Raw tokens are meaningless without it: a
    # reading either side of a /login belongs to two different pools and must
    # never be read as one series.
    acct = active_account()
    out["account"] = acct
    switched = account_changed(prev.get("account"), acct)
    out["account_switched"] = switched

    # WHICH MODEL spent it? Each family is metered separately, so a fleet can
    # exhaust one sub-meter with the others idle.
    out["by_model"] = dict(sorted(fleet_by_model.items(), key=lambda kv: -kv[1]))

    ledger = dict(prev.get("meters") or {})
    if RECORD_METER:
        e = dict(ledger.get(RECORD_METER) or {})
        e["read_at"] = now.astimezone().isoformat(timespec="seconds")
        if METER_ALL is not None: e["all_models"] = METER_ALL
        if METER_FABLE is not None: e["fable"] = METER_FABLE
        if METER_RESETS: e["resets"] = METER_RESETS
        ledger[RECORD_METER] = e
    out["meters"] = ledger

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
            worsened = worsened_since(decision, unprotected_share, fleet_tokens)
            aged_out = aged_into_a_question(
                decision, age, fleet_tokens,
                now.astimezone().isoformat(timespec="seconds"))
            should_wake = wake_now(worsened, aged_out,
                                   _mins_since(prev.get("woke_at", "")))
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
        if out["by_model"]:
            split = " · ".join(f"{m} {n/1e6:.0f}M ({n/fleet:.0%})"
                               for m, n in list(out["by_model"].items())[:5])
            print(f"by model: {split}\n")
        print(f"account: {acct or 'UNREADABLE'}"
              + ("  ** SWITCHED since the last run — the series is broken, do "
                 "not compare across it **" if switched else ""))
        if ledger:
            print("\nmeters (hand-entered; a reading is worth what its age says):")
            for k in pools_by_next_reset(ledger):
                e = ledger[k]
                age = meter_age_hours(e, now)
                age_s = f"{age:.1f}h old" if age is not None else "never read"
                print(f"  {k:<28} all {e.get('all_models','?')}% · "
                      f"Fable {e.get('fable','?')}% · resets "
                      f"{e.get('resets','?')} · {age_s}")
        print()
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
