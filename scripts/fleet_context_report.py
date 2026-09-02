#!/usr/bin/env python3
"""Fleet context monitor — measure every running claude session's context size and
FLAG idle giants for Bryan's review, using MESSAGE-LEVEL judgment. Zero LLM turns.

Never auto-compacts on a blunt rule. For each big session it looks at the actual
last assistant message and classifies:
  * waiting  -> last message ends with a question / a direct ask to Bryan, or has
               pending-work language -> it's waiting on him, NOT done -> skip.
  * done     -> last message reads as a clean completion -> a real compact candidate.
  * unsure   -> can't tell -> surface for a human/judgment review.
Only sessions that are QUIET (no transcript activity for --quiet-after minutes),
non-self and big, and that classify done/unsure, are flagged. Whether a session is
working is read from its transcript, never from `tmux capture-pane` -- see
activity_state() for what that cost when it was read from the pane.

Flags:
  --review-at N  size at/above which an idle session is considered (default 450000)
  --threshold N  warn line in the table (default 300000)
  --notify       macOS notification for NEW candidates (respects sleep window + dedup)
  --awake A-B    waking hours 24h (default 8-24); notifications only fire inside it
  --state PATH   dedup state file (default /tmp/fleet-monitor-flagged.json)
  --quiet-after N  minutes of transcript silence before a session is reviewable (default 15)
"""
import json, os, re, subprocess, sys, glob, time, datetime

def argval(flag, default, cast=int):
    return cast(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default

REVIEW_AT = argval("--review-at", 450_000)
THRESH = argval("--threshold", 300_000)
NOTIFY = "--notify" in sys.argv
AWAKE = argval("--awake", "8-24", str)
STATE = argval("--state", "/tmp/fleet-monitor-flagged.json", str)
# Minutes of transcript silence before a session counts as not-working.
# The loop runs every 2h, so this only has to outlast a single long turn.
QUIET_AFTER_S = argval("--quiet-after", 15) * 60
SELF_MARKER = "ai-team-lead"
TAIL_BYTES = 1_048_576

HOME = os.path.expanduser("~")
PROJ = os.path.join(HOME, ".claude", "projects")

# Claude Code enforces separate weekly usage limits per model (Opus is the binding
# constraint for most users). Map full model IDs to short display labels; anything
# not in this map (older model generations, "<synthetic>", etc.) is shown verbatim
# so nothing is silently dropped.
MODEL_LABELS = {
    "claude-opus-4-8": "Opus 4.8",
    "claude-sonnet-5": "Sonnet",
    "claude-haiku-4-5-20251001": "Haiku",
    "claude-fable-5": "Fable",
}
def model_label(model_id):
    if not model_id:
        return "unknown"
    return MODEL_LABELS.get(model_id, model_id)

def encode(c): return re.sub(r"[/_.]", "-", c)
def sh(a): return subprocess.run(a, capture_output=True, text=True).stdout

def awake_now():
    a, b = (int(x) for x in AWAKE.split("-"))
    h = datetime.datetime.now().hour
    return a <= h < b if a <= b else (h >= a or h < b)

def running_claude_cwds():
    out = sh(["ps", "-axww", "-o", "pid=,command="])
    pids = [l.split(None, 1)[0] for l in out.splitlines()
            if ".local/bin/claude" in l and "grep" not in l]
    cwds = {}
    for pid in pids:
        for line in sh(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"]).splitlines():
            if line.startswith("n"):
                cwds[pid] = line[1:]; break
    return cwds

def tmux_by_cwd():
    m = {}
    for line in sh(["tmux", "list-panes", "-a", "-F", "#{session_name}|#{pane_current_path}"]).splitlines():
        if "|" in line:
            s, p = line.split("|", 1); m.setdefault(p, s)
    return m

def context_tokens(path):
    """Returns (ctx_size, model_label) from the most recent turn with usage — a
    session's current context belongs to whichever model produced its last turn."""
    last, last_model = None, None
    with open(path) as f:
        for line in f:
            try: o = json.loads(line)
            except Exception: continue
            msg = o.get("message") or {}
            u = msg.get("usage")
            if u:
                last = u
                last_model = msg.get("model")
    if not last: return None, None
    ctx = sum(last.get(k, 0) for k in
              ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return ctx, model_label(last_model)

def activity_state(transcript_path):
    """"active" | "quiet", decided from the TRANSCRIPT -- never from the pane.

    THIS FUNCTION REPLACED A pane_state() THAT READ `tmux capture-pane`, AND THAT
    IS THE WHOLE POINT. The old one made three inferences the pane cannot support:

      * `"esc to interrupt" in pane -> busy`. That footer is a render like every
        other pixel on the pane. It is the exact reading that got an idle peer
        skipped as mid-task three times in one hour.
      * a non-empty `❯` line -> `draft`, i.e. Bryan has unsent text. It is
        usually a GHOST over an empty editor. Proven on 2026-09-01: health-tool's
        pane rendered `❯ Order the Gicisky tag`, and a single typed character
        REPLACED it, so the editor had been empty the whole time. Six non-existent
        "unsent messages" had already been reported to Bryan as his own words.
      * an empty `❯` line -> `idle`, the same guess with the sign flipped.

    The failure was silent and it inverted the tool. `busy` and `draft` both mean
    "skip", so every misread pane SUPPRESSED a notification -- this monitor exists
    to flag idle giants, and pane inference is what stopped it flagging them.

    A transcript is state: a session that is taking turns appends to one. Subagent
    transcripts live at `<session-id>/subagents/agent-*.jsonl`, so a parent stuck in
    a long fan-out turn still shows as active through its children -- glob
    recursively or a working session reads quiet.

    A false "quiet" here costs one line in a review notification. A false "busy"
    cost the entire signal.
    """
    newest = os.path.getmtime(transcript_path)
    sess_dir = os.path.splitext(transcript_path)[0]
    for sub in glob.glob(os.path.join(sess_dir, "**", "*.jsonl"), recursive=True):
        newest = max(newest, os.path.getmtime(sub))
    return "active" if (time.time() - newest) < QUIET_AFTER_S else "quiet"

def last_assistant_text(path):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - TAIL_BYTES))
        tail = f.read().decode("utf-8", "replace")
    text = ""
    for line in tail.splitlines():
        try: o = json.loads(line)
        except Exception: continue
        m = o.get("message") or {}
        if m.get("role") == "assistant":
            c = m.get("content")
            if isinstance(c, list):
                t = " ".join(b.get("text", "") for b in c
                             if isinstance(b, dict) and b.get("type") == "text")
                if t.strip(): text = t
            elif isinstance(c, str) and c.strip():
                text = c
    return text

WAIT_PHRASES = ("let me know", "want me to", "should i", "which ", "your call",
    "confirm", "ready when you", "say the word", "whenever you're ready",
    "waiting on your", "waiting on you", "shall i", "do you want", "tell me",
    "flag me", "standing by", "will ping", "let you know", "waiting for")
DONE_PHRASES = ("done", "shipped", "complete", "merged", "spun down", "all set",
    "✅", "fully closed", "wrapped", "no action needed", "nothing left")

def classify_last_message(path):
    """Message-level: waiting | done | unsure (deterministic, no LLM)."""
    txt = last_assistant_text(path).strip()
    if not txt:
        return "unsure"
    lines = [l for l in txt.splitlines() if l.strip()]
    last_line = lines[-1].strip().lower() if lines else ""
    tail = txt[-700:].lower()
    if last_line.endswith("?") or any(p in tail for p in WAIT_PHRASES):
        return "waiting"
    if any(p in tail for p in DONE_PHRASES):
        return "done"
    return "unsure"

def main():
    # Everything below runs only under __main__ so the module can be imported
    # by tests. activity_state() is the reason that matters: the pane-reading
    # function it replaced was never covered by a test, and an untestable
    # inference is how it survived long enough to invert the tool.
    # --- measure ---
    tmux_map = tmux_by_cwd()
    rows = []
    for pid, cwd in running_claude_cwds().items():
        files = glob.glob(os.path.join(PROJ, encode(cwd), "*.jsonl"))
        if not files: continue
        tp = max(files, key=os.path.getmtime)
        ctx, model = context_tokens(tp)
        if ctx is None: continue
        rows.append({"ctx": ctx, "model": model, "name": os.path.basename(cwd), "cwd": cwd,
                     "tmux": tmux_map.get(cwd), "tp": tp})
    rows.sort(key=lambda r: -r["ctx"])

    # --- classify: flag only idle+no-draft giants that look done/unsure ---
    candidates = []
    for r in rows:
        r["decision"] = ""
        if r["ctx"] < REVIEW_AT:
            continue
        if SELF_MARKER in r["cwd"]:
            r["decision"] = "skip (self)"; continue
        if activity_state(r["tp"]) == "active":
            r["decision"] = f"skip (took a turn in the last {QUIET_AFTER_S // 60}m)"
            continue
        verdict = classify_last_message(r["tp"])
        if verdict == "waiting":
            r["decision"] = "skip (quiet, but last msg is waiting on you)"
        else:
            r["decision"] = f"REVIEW — quiet, looks {verdict}"
            candidates.append(r["name"])

    # --- report ---
    print(f"{'CONTEXT':>9}  {'MODEL':<10}  {'SESSION':<26}  DECISION")
    print("-" * 90)
    for r in rows:
        flag = "⚠️ " if r["ctx"] >= THRESH else "  "
        print(f"{r['ctx']:>9,}  {r['model']:<10}  {flag}{r['name']:<24}  {r['decision']}")
    print("-" * 90)
    over = [r for r in rows if r["ctx"] >= THRESH]
    print(f"{sum(r['ctx'] for r in rows):>9,}  TOTAL / {len(rows)} sessions · "
          f"{len(over)} over {THRESH//1000}k · {len(candidates)} to review")

    # --- by model: a session's context belongs to whichever model is CURRENTLY active
    # (its most recent turn) — this is what's actually pressing on that model's weekly cap.
    by_model = {}
    for r in rows:
        by_model.setdefault(r["model"], []).append(r)
    model_order = sorted(by_model, key=lambda m: -sum(r["ctx"] for r in by_model[m]))
    print(f"\nBy currently-active model (ranked by summed context):\n")
    print(f"{'CONTEXT':>9}  {'SESSIONS':>8}  MODEL — session list")
    print("-" * 90)
    for model in model_order:
        sess = by_model[model]
        names = ", ".join(r["name"] for r in sorted(sess, key=lambda r: -r["ctx"]))
        print(f"{sum(r['ctx'] for r in sess):>9,}  {len(sess):>8}  {model} — {names}")
    print("-" * 90)

    # --- dedup + sleep-gated notify ---
    try:
        flagged = set(json.load(open(STATE)))
    except Exception:
        flagged = set()
    new = [c for c in candidates if c not in flagged]
    json.dump(sorted(set(candidates)), open(STATE, "w"))   # persists only current set

    if NOTIFY and new:
        if not awake_now():
            print(f"[asleep {AWAKE}] holding {len(new)} new candidate(s): {', '.join(new)}")
        else:
            body = ("Review (idle, may be done): " + ", ".join(new[:5])).replace('"', "'")
            subprocess.run(["osascript", "-e",
                f'display notification "{body}" with title "Fleet context — review" sound name "Ping"'])


if __name__ == "__main__":
    main()
