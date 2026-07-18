#!/usr/bin/env python3
"""Fleet token-BURN attribution — who consumed the most quota today. Zero LLM turns.

Complements fleet_context_report.py: that measures how big each session IS right
now; this measures how much each session BURNED over a day (sum of per-turn usage
across all turns timestamped today). A session can stay small yet burn hard via
many turns — cache_read (re-reading context every turn) is the dominant driver, so
it's summed in full.

Output is a proxy for subscription-quota consumption (raw tokens, model-agnostic),
good for RELATIVE ranking — which agent to add controls to first — not an exact
dollar figure.

Flags:
  --date YYYY-MM-DD   day to attribute (default: today, local)
  --json              machine-readable output
"""
import json, os, re, subprocess, sys, glob, datetime

def argval(flag, default, cast=str):
    return cast(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default

DAY = argval("--date", datetime.date.today().isoformat())
AS_JSON = "--json" in sys.argv
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

def blank_bucket():
    return {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0, "turns": 0}

def burn_for_day(path, day):
    """Sum per-turn usage components for turns timestamped on `day` (local).
    Attributes each turn to that turn's model, since a session may mix models
    across the day (e.g. Opus for implementation, Haiku for a polling loop)."""
    tot = blank_bucket()
    models = {}
    with open(path) as f:
        for line in f:
            try: o = json.loads(line)
            except Exception: continue
            ts = o.get("timestamp") or ""
            # transcript timestamps are ISO-8601 UTC; convert to local date
            try:
                dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                local_day = dt.astimezone().date().isoformat()
            except Exception:
                continue
            if local_day != day:
                continue
            msg = o.get("message") or {}
            u = msg.get("usage")
            if not u:
                continue
            label = model_label(msg.get("model"))
            m = models.setdefault(label, blank_bucket())
            for bucket in (tot, m):
                bucket["turns"] += 1
                bucket["input"] += u.get("input_tokens", 0)
                bucket["output"] += u.get("output_tokens", 0)
                bucket["cache_creation"] += u.get("cache_creation_input_tokens", 0)
                bucket["cache_read"] += u.get("cache_read_input_tokens", 0)
    tot["total"] = sum(tot[k] for k in ("input", "output", "cache_creation", "cache_read"))
    for m in models.values():
        m["total"] = sum(m[k] for k in ("input", "output", "cache_creation", "cache_read"))
    tot["models"] = models
    return tot

rows = []
seen = set()
fleet_models = {}  # model label -> blank_bucket(), summed across the whole fleet
for pid, cwd in running_claude_cwds().items():
    d = os.path.join(PROJ, encode(cwd))
    files = glob.glob(os.path.join(d, "*.jsonl"))
    if not files: continue
    # a session may have several transcripts in its dir; sum burn across all of them
    agg = {"input": 0, "output": 0, "cache_creation": 0, "cache_read": 0, "total": 0, "turns": 0}
    agg_models = {}
    for tp in files:
        b = burn_for_day(tp, DAY)
        for k in agg:
            if k != "total": agg[k] += b[k]
        for label, m in b["models"].items():
            am = agg_models.setdefault(label, blank_bucket())
            for k in am: am[k] += m[k]
    agg["total"] = sum(agg[k] for k in ("input", "output", "cache_creation", "cache_read"))
    if agg["total"] == 0: continue
    name = os.path.basename(cwd)
    if name in seen: continue
    seen.add(name)
    agg["name"] = name
    for m in agg_models.values():
        m["total"] = sum(m[k] for k in ("input", "output", "cache_creation", "cache_read"))
    agg["models"] = agg_models
    rows.append(agg)
    for label, m in agg_models.items():
        fm = fleet_models.setdefault(label, blank_bucket())
        for k in fm: fm[k] += m[k]
rows.sort(key=lambda r: -r["total"])
for m in fleet_models.values():
    m["total"] = sum(m[k] for k in ("input", "output", "cache_creation", "cache_read"))
model_rows = sorted(fleet_models.items(), key=lambda kv: -kv[1]["total"])

if AS_JSON:
    print(json.dumps({"day": DAY, "sessions": rows,
                       "models": {k: v for k, v in model_rows}}, indent=2)); sys.exit(0)

print(f"Token burn for {DAY} (proxy for quota consumption; cache_read = re-reading context)\n")
print(f"{'BURN':>12}  {'TURNS':>5}  {'cache_read':>12}  {'output':>9}  {'MODELS':<20}  SESSION")
print("-" * 100)
for r in rows:
    models_str = ", ".join(sorted(r["models"]))
    print(f"{r['total']:>12,}  {r['turns']:>5}  {r['cache_read']:>12,}  {r['output']:>9,}  {models_str:<20}  {r['name']}")
print("-" * 100)
gt = sum(r["total"] for r in rows)
print(f"{gt:>12,}  {sum(r['turns'] for r in rows):>5}  fleet total across {len(rows)} sessions")
if rows:
    top = rows[0]
    print(f"\nTop burner: {top['name']} — {top['total']:,} tokens over {top['turns']} turns "
          f"({100*top['total']//gt if gt else 0}% of today's fleet burn).")

# Claude Code's weekly usage limits are per-model (Opus is the binding constraint
# for most plans) — a fleet total hides which model is actually under pressure.
print(f"\nBy model (fleet-wide, ranked by burn):\n")
print(f"{'BURN':>12}  {'TURNS':>5}  {'cache_read':>12}  {'output':>9}  MODEL")
print("-" * 78)
for label, m in model_rows:
    print(f"{m['total']:>12,}  {m['turns']:>5}  {m['cache_read']:>12,}  {m['output']:>9,}  {label}")
print("-" * 78)
print(f"{gt:>12,}  {sum(m['turns'] for _, m in model_rows):>5}  fleet total across {len(model_rows)} model(s)")
