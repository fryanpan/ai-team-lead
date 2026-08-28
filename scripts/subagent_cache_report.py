#!/usr/bin/env python3
"""Subagent cache behaviour against the 5-minute default TTL.

Task 9 of docs/product/plans/token-efficiency-plan.md. A subagent does not read
its parent's cache and its own cache defaults to the 5-minute tier, so every gap
longer than 5 minutes between its requests is a full prefix rebuild. The 5-minute
tier breaks even near a 22% hit rate.

DEDUPE ON requestId. A transcript writes one record per content block and each
repeats the same `usage` object; summing records over-counts ~1.8-2.2x. See
docs/process/learnings.md, "Every transcript token count we had was ~1.9x too high".
"""
import json, os, sys, glob, datetime, collections

ROOT = os.path.expanduser("~/.claude/projects")
TTL_SECONDS = 300


def requests_in(path):
    """Yield (timestamp, usage, model) once per billed request, in file order."""
    seen = set()
    with open(path, errors="replace") as f:
        for line in f:
            try:
                o = json.loads(line)
            except Exception:
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
            try:
                ts = datetime.datetime.fromisoformat(
                    (o.get("timestamp") or "").replace("Z", "+00:00"))
            except Exception:
                ts = None
            yield ts, u, msg.get("model")


def main(day=None):
    day = day or datetime.date.today().isoformat()
    files = glob.glob(os.path.join(ROOT, "*", "*", "subagents", "*.jsonl"))

    per_project = collections.defaultdict(lambda: {
        "subagents": 0, "requests": 0, "create": 0, "read": 0,
        "cold_starts": 0, "expired_gaps": 0, "warm_gaps": 0})
    totals = per_project["__ALL__"]

    for path in files:
        project = path.split(os.sep)[5]
        rows = [r for r in requests_in(path)
                if r[0] and r[0].astimezone().date().isoformat() == day]
        if not rows:
            continue
        p = per_project[project]
        for bucket in (p, totals):
            bucket["subagents"] += 1
        prev = None
        for ts, u, _model in rows:
            create = u.get("cache_creation_input_tokens", 0)
            read = u.get("cache_read_input_tokens", 0)
            for bucket in (p, totals):
                bucket["requests"] += 1
                bucket["create"] += create
                bucket["read"] += read
                if prev is None:
                    bucket["cold_starts"] += 1
                else:
                    gap = (ts - prev).total_seconds()
                    if gap > TTL_SECONDS:
                        bucket["expired_gaps"] += 1
                    else:
                        bucket["warm_gaps"] += 1
            prev = ts

    def hit_rate(b):
        denom = b["read"] + b["create"]
        return (100.0 * b["read"] / denom) if denom else 0.0

    print(f"Subagent cache behaviour for {day} "
          f"(deduped per request; TTL {TTL_SECONDS}s)\n")
    print(f"{'SUBAGT':>7} {'REQS':>6} {'CACHE_CREATE':>13} {'CACHE_READ':>13} "
          f"{'HIT%':>6} {'EXPIRED':>8}  PROJECT")
    print("-" * 100)
    rows = sorted(((k, v) for k, v in per_project.items() if k != "__ALL__"),
                  key=lambda kv: -(kv[1]["create"] + kv[1]["read"]))
    for name, b in rows:
        print(f"{b['subagents']:>7} {b['requests']:>6} {b['create']:>13,} "
              f"{b['read']:>13,} {hit_rate(b):>5.1f}% {b['expired_gaps']:>8}  {name[:44]}")
    print("-" * 100)
    t = totals
    print(f"{t['subagents']:>7} {t['requests']:>6} {t['create']:>13,} "
          f"{t['read']:>13,} {hit_rate(t):>5.1f}% {t['expired_gaps']:>8}  FLEET TOTAL")

    print(f"\nCold starts (first request of a subagent): {t['cold_starts']:,}")
    print(f"Gaps within the {TTL_SECONDS}s TTL (cache still warm): {t['warm_gaps']:,}")
    print(f"Gaps past the TTL (prefix rebuilt): {t['expired_gaps']:,}")
    intervals = t["warm_gaps"] + t["expired_gaps"]
    if intervals:
        print(f"Share of gaps that outlived the TTL: "
              f"{100.0 * t['expired_gaps'] / intervals:.1f}%")
    print(f"\nMeasured hit rate {hit_rate(t):.1f}% against the ~22% break-even "
          f"for the 5-minute tier.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
