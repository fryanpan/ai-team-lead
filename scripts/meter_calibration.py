#!/usr/bin/env python3
"""What unit does the weekly subscription meter count? Measure it, don't read docs.

Anthropic documents the 0.1x cache-read discount for API RATE LIMITS only. No page
says how a subscription's session/weekly meter weights a cache read, or even what
unit it counts. Two agents confirmed that null on 2026-08-26. This measures it.

The method: the trend log records the meter as an integer percentage at known
times, and the transcripts say exactly what the fleet burned between any two of
those readings. Competing hypotheses predict different things:

  raw        meter counts every token the same
  discounted cache reads count at 0.1x, the way they are priced
  output     meter counts output tokens only
  cost       meter counts dollar-equivalents at published per-model rates

The pool size in tokens is unknown, and does not need to be known: if a hypothesis
is right, (meter points burned / hypothesised tokens burned) is the SAME CONSTANT
across every interval in one pool. So the winner is whichever hypothesis has the
lowest coefficient of variation across intervals -- the pool size cancels.

Only compare intervals inside ONE pool. An account rotation or a weekly reset
replaces the denominator and makes the ratios incomparable.

  meter_calibration.py --pool pools/max-aug06.txt
"""
import bisect, collections, datetime, glob, json, os, statistics, sys

PROJ = os.path.expanduser("~/.claude/projects")

# Published per-million rates. Used only by the `cost` hypothesis, and only as
# RELATIVE weights -- absolute dollars cancel out with the pool size.
RATES = {   # (input, output, cache_write_5m, cache_read)
    "opus":   (15.0, 75.0, 18.75, 1.50),
    "sonnet": (3.0,  15.0,  3.75, 0.30),
    "haiku":  (1.0,   5.0,  1.25, 0.10),
}


def rate_for(model):
    m = (model or "").lower()
    for key in RATES:
        if key in m:
            return RATES[key]
    if "fable" in m:          # premium tier, priced above opus
        return RATES["opus"]
    return RATES["opus"]      # unknown model: assume the expensive one


def argval(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def parse_pool(path):
    """`YYYY-MM-DD HH:MM <percent>` per line, in order. Blank/# lines ignored."""
    out = []
    for line in open(path):
        line = line.split("#")[0].strip()
        if not line:
            continue
        d, t, pct = line.split()
        stamp = datetime.datetime.fromisoformat(f"{d}T{t}:00").astimezone()
        out.append((stamp, float(pct)))
    return sorted(out)


def blank():
    return collections.Counter()


def scan(marks):
    """One pass over the store, bucketing every turn into its interval.

    Scanning once per interval would re-read a 4.8GB store a dozen times; this
    walks it once and assigns each record to the interval containing it. Files
    whose mtime predates the pool cannot hold a record inside it, so they are
    skipped -- that is what makes the pass affordable at all.

    Returns per_bucket[i][model] -> Counter of usage fields.
    """
    edges = [m[0] for m in marks]
    start, end = edges[0], edges[-1]
    per_bucket = collections.defaultdict(lambda: collections.defaultdict(blank))
    floor = start.timestamp()
    paths = glob.glob(os.path.join(PROJ, "*", "*.jsonl"))
    paths += glob.glob(os.path.join(PROJ, "*", "*", "subagents", "*.jsonl"))
    scanned = 0
    for path in paths:
        try:
            if os.path.getmtime(path) < floor:
                continue
        except OSError:
            continue
        scanned += 1
        # One API response is written as one record PER CONTENT BLOCK, each
        # repeating a usage object. Counting lines counts each billed request
        # ~1.9x, and UNEVENLY across components — measured on the week of
        # 2026-08-17: output 1.53x, cache_read 1.89x, cache_creation 2.45x.
        # That skew biases any fit that weights the components separately, so
        # collapse on message.id and keep the LAST record (the earlier ones are
        # partial streaming snapshots; the last is the max in 100% of splits).
        seen = {}
        with open(path, errors="replace") as fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                msg = rec.get("message") or {}
                usage = msg.get("usage")
                if not usage:
                    continue
                try:
                    ts = datetime.datetime.fromisoformat(
                        rec["timestamp"].replace("Z", "+00:00"))
                except Exception:
                    continue
                if not (start <= ts <= end):
                    continue
                i = bisect.bisect_right(edges, ts) - 1
                if not 0 <= i < len(marks) - 1:
                    continue
                seen[msg.get("id") or rec.get("uuid")] = (
                    i, msg.get("model") or "unknown", usage)
        for i, model, usage in seen.values():
            c = per_bucket[i][model]
            for k in ("input_tokens", "output_tokens",
                      "cache_creation_input_tokens", "cache_read_input_tokens"):
                c[k] += usage.get(k, 0)
            c["turns"] += 1
    return per_bucket, scanned


def hypotheses(c, model):
    """Tokens 'spent' in this interval under each competing rule."""
    inp = c["input_tokens"]
    out = c["output_tokens"]
    write = c["cache_creation_input_tokens"]
    read = c["cache_read_input_tokens"]
    r_in, r_out, r_write, r_read = rate_for(model)
    return {
        "raw":        inp + out + write + read,
        "discounted": inp + out + write + 0.1 * read,
        "output":     out,
        "cost":       (inp * r_in + out * r_out + write * r_write + read * r_read) / 1e6,
    }


def main():
    pool_file = argval("--pool")
    if not pool_file:
        print(__doc__)
        return
    marks = parse_pool(pool_file)
    start, end = marks[0][0], marks[-1][0]
    print(f"Pool window: {start} -> {end}   ({len(marks)} readings, "
          f"{len(marks)-1} intervals)\n")

    per_bucket, scanned = scan(marks)
    print(f"Scanned {scanned:,} transcripts touched since the pool opened.\n")

    rows = []
    for i, ((t0, p0), (t1, p1)) in enumerate(zip(marks, marks[1:])):
        totals = collections.Counter()
        for model, c in per_bucket.get(i, {}).items():
            for name, val in hypotheses(c, model).items():
                totals[name] += val
            totals["turns"] += c["turns"]
            totals["cache_read"] += c["cache_read_input_tokens"]
        rows.append({
            "t0": t0, "t1": t1, "pts": p1 - p0,
            "turns": totals["turns"],
            "cache_share": (totals["cache_read"] / totals["raw"]) if totals["raw"] else 0,
            **{h: totals[h] for h in ("raw", "discounted", "output", "cost")},
        })
        print(f"  {t0:%m-%d %H:%M} -> {t1:%m-%d %H:%M}  "
              f"{p1-p0:+5.0f} pts  {totals['turns']:>6,} turns  "
              f"raw {totals['raw']/1e6:>8,.0f}M  "
              f"cache-read share {100*rows[-1]['cache_share']:.1f}%")
        if "--by-model" in sys.argv:
            for model, c in sorted(per_bucket.get(i, {}).items(),
                                   key=lambda kv: -sum(kv[1].values())):
                tot = (c["input_tokens"] + c["output_tokens"]
                       + c["cache_creation_input_tokens"]
                       + c["cache_read_input_tokens"])
                print(f"        {model:<34} {tot/1e6:>8,.0f}M  {c['turns']:>6,} turns")

    if "--dump" in sys.argv:
        out = []
        for i, ((t0, p0), (t1, p1)) in enumerate(zip(marks, marks[1:])):
            out.append({
                "t0": t0.isoformat(), "t1": t1.isoformat(), "pts": p1 - p0,
                "models": {m: {k: c[k] for k in (
                    "input_tokens", "output_tokens",
                    "cache_creation_input_tokens", "cache_read_input_tokens", "turns")}
                    for m, c in per_bucket.get(i, {}).items()},
            })
        with open(argval("--dump"), "w") as fh:
            json.dump(out, fh, indent=1)
        print(f"\ndumped per-model components to {argval('--dump')}")

    usable = [r for r in rows if r["pts"] > 0 and r["raw"] > 0]
    print(f"\n{len(usable)} of {len(rows)} intervals usable "
          f"(positive meter movement and non-zero burn)\n")
    if len(usable) < 3:
        print("Too few usable intervals to discriminate. Stopping.")
        return

    print("Points of meter per unit burned -- the RIGHT hypothesis is CONSTANT.\n")
    print(f"{'hypothesis':<12} {'mean':>12} {'stdev':>12} {'CoV':>8}   verdict")
    scores = {}
    for h in ("raw", "discounted", "output", "cost"):
        ratios = [r["pts"] / r[h] for r in usable if r[h] > 0]
        if len(ratios) < 3:
            continue
        mean = statistics.mean(ratios)
        sd = statistics.stdev(ratios)
        scores[h] = sd / mean if mean else float("inf")
        print(f"{h:<12} {mean:>12.3e} {sd:>12.3e} {scores[h]:>8.3f}")

    if scores:
        best = min(scores, key=scores.get)
        rest = sorted(k for k in scores if k != best)
        margin = min(scores[k] for k in rest) / scores[best] if rest else 1
        print(f"\nLowest variation: {best!r} (CoV {scores[best]:.3f}), "
              f"{margin:.2f}x tighter than the next best.")
        spread = max(r["cache_share"] for r in usable) - min(r["cache_share"] for r in usable)
        print(f"Cache-read share varies {100*spread:.1f} points across intervals.")
        if spread < 0.05:
            print("WARNING: that spread is too small to separate raw from discounted. "
                  "Treat this as inconclusive between those two.")



if __name__ == "__main__":
    main()
