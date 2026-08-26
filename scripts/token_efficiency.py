#!/usr/bin/env python3
"""Tokens per delivered hands-on hour — the ratio the token goal is stated in.

Burn lives in the trend log, delivered hours live in the weekly plan, and nothing
joined them. That ratio IS the key result ("25 hands-on hours a week on two Max
20x accounts without running out"), so until it is computed every lever is
unfalsifiable. Zero LLM turns: it shells out to turn_attribution.py, which counts
subagent transcripts as well as top-level ones (the burn report misses them).

  token_efficiency.py --week 2026-08-24 [--hours 22]   # compute (and record) one week
  token_efficiency.py --report                          # print the recorded curve

--hours is Bryan's delivered hands-on hours for that week. Transcripts under-report
it, so it is an input, not something to derive. Recording a week without --hours
stores the burn and leaves hours blank until it is known.
"""
import csv, datetime, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ATTRIB = os.path.join(HERE, "turn_attribution.py")
LEDGER = os.path.join(REPO, "docs", "process", "token-efficiency.csv")
FIELDS = ["week_of", "total_tokens", "turns", "sessions", "hands_on_hours",
          "tokens_per_hour", "tokens_per_turn", "note"]


def argval(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def week_burn(monday):
    """Sum Mon..Sun. Returns (total_tokens, turns, distinct session names).

    Goes through turn_attribution.py rather than fleet_burn_report.py, because
    the burn report misses two things: subagent transcripts (a subdirectory its
    glob never reaches — 61% of burn in the week of 2026-08-17), and any session
    that is no longer running, since it discovers transcripts via live cwds.
    """
    r = subprocess.run([sys.executable, ATTRIB, "--week", monday.isoformat(), "--json"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return 0, 0, set(), [monday.isoformat()]
    data = json.loads(r.stdout)
    total = sum(b.get("total", 0) for b in data.get("fleet", {}).values())
    turns = sum(b.get("turns", 0) for b in data.get("fleet", {}).values())
    names = {name for name, causes in data.get("sessions", {}).items()
             if any(b.get("turns") for b in causes.values())}
    return total, turns, names, []


def load():
    if not os.path.exists(LEDGER):
        return []
    with open(LEDGER, newline="") as fh:
        return list(csv.DictReader(fh))


def save(rows):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    rows.sort(key=lambda r: r["week_of"])
    with open(LEDGER, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def fmt(n):
    return f"{n/1e6:,.1f}M" if n else "-"


def report(rows):
    if not rows:
        print("No weeks recorded yet. Run with --week YYYY-MM-DD --hours N.")
        return
    print(f"{'week of':<12} {'burn':>10} {'turns':>7} {'hours':>6} "
          f"{'tokens/hour':>13} {'tokens/turn':>12}  change")
    prev = None
    for r in rows:
        tph = r.get("tokens_per_hour") or ""
        tpt = r.get("tokens_per_turn") or ""
        change = ""
        if tph and prev:
            a, b = float(prev), float(tph)
            change = f"{a/b:.2f}x better" if b < a else f"{b/a:.2f}x worse"
        tph_s = f"{float(tph)/1e6:,.1f}M" if tph else "-"
        tpt_s = f"{float(tpt)/1e3:,.0f}k" if tpt else "-"
        print(f"{r['week_of']:<12} {fmt(int(r['total_tokens'] or 0)):>10} "
              f"{r['turns'] or '-':>7} {r['hands_on_hours'] or '-':>6} "
              f"{tph_s:>13} {tpt_s:>12}  {change}")
        if tph:
            prev = tph


def main():
    if "--report" in sys.argv:
        report(load())
        return
    wk = argval("--week")
    if not wk:
        today = datetime.date.today()
        monday = today - datetime.timedelta(days=today.weekday())
    else:
        monday = datetime.date.fromisoformat(wk)
        if monday.weekday() != 0:
            monday -= datetime.timedelta(days=monday.weekday())
    hours = argval("--hours")

    total, turns, names, missing = week_burn(monday)
    tph = round(total / float(hours)) if hours and float(hours) else ""
    tpt = round(total / turns) if turns else ""

    rows = [r for r in load() if r["week_of"] != monday.isoformat()]
    prior = next((r for r in load() if r["week_of"] == monday.isoformat()), {})
    rows.append({
        "week_of": monday.isoformat(),
        "total_tokens": total,
        "turns": turns,
        "sessions": len(names),
        "hands_on_hours": hours or prior.get("hands_on_hours", ""),
        "tokens_per_hour": tph or prior.get("tokens_per_hour", ""),
        "tokens_per_turn": tpt,
        "note": ("partial: no data for " + ",".join(missing)) if missing else "",
    })
    save(rows)

    print(f"Week of {monday}: {fmt(total)} tokens over {turns:,} turns, "
          f"{len(names)} sessions")
    if hours:
        print(f"  {float(hours):g} hands-on hours -> {tph/1e6:,.1f}M tokens per delivered hour")
    else:
        print("  hands-on hours not supplied - ratio left blank")
    if missing:
        print(f"  incomplete: no transcript data for {', '.join(missing)}")
    print(f"  ledger: {os.path.relpath(LEDGER, REPO)}")


if __name__ == "__main__":
    main()
