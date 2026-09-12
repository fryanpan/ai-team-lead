#!/usr/bin/env python3
"""Attribute memory demand to named process families, using a metric that does
not collapse during the crisis it is supposed to explain.

Why this exists
---------------
`fleet_guard.py` records `claude_gb`, the summed RSS of the claude sessions. Its
own header says not to trust it for attribution: RSS counts *resident* pages, so
it FALLS as pages move to swap. During the 2026-09-09 incident it dropped from
3.6GB to 1.55GB while swap climbed to 41GB. A metric that shrinks as the problem
grows cannot name the cause, and reading a low value as "the sessions were
small" is the trap.

So on 2026-09-12, with the machine in a degraded state for 39.7% of 8,055 guard
samples over 11 days, nothing on this machine could say WHAT took the memory.
That is a could-not-look, not a clean result, and it sits in front of a ~$2,700
hardware decision: buying 64GB is a bet that the consumer scales with RAM.

What it measures instead
------------------------
`top -l 1 -stats pid,command,mem,cmprs` reports, per process, its physical
footprint (MEM) and how much of that footprint is compressed (CMPRS). MEM keeps
RISING while RSS falls, because it still counts the pages that went away under
pressure. That is the population that can reach swap, which is the question.

**CMPRS is a SUBSET of MEM, not an addition to it.** MEM is the kernel's
phys_footprint, which already includes compressed pages. Verified 2026-09-12 on
twelve same-user processes: `top` MEM matched `vmmap --summary` "Physical
footprint" to within 0.5 MB on every one (e.g. MEM 58M, CMPRS 49M, footprint
58.2M). The first version of this script summed MEM+CMPRS and overstated demand
by the whole compressed total -- a 9.8 GB fleet that was really ~5.2 GB.

`footprint(1)` and `vmmap(1)` expose the same thing more precisely. Both were
refused on a plain claude pid from inside a sandboxed agent on 2026-09-12, and
`vmmap --summary` then worked on same-user pids from an unsandboxed shell the same
day -- so treat them as unavailable under launchd, not as root-only everywhere. `top`
does not, which is the whole reason this reads `top`. Do not "improve" this by
switching to vmmap; under launchd it would return nothing and report zero.

Three states, not two
---------------------
If `top` cannot be read or parsed, this says COULD-NOT-SAMPLE and exits non-zero.
It never prints a zero that could be mistaken for "nothing is using memory" --
that is the exact failure this file was written to end.

Usage:
    python3 attribute_memory.py                 # one snapshot, grouped
    python3 attribute_memory.py --watch 120     # sample every 120s until stopped
    python3 attribute_memory.py --top 15        # widen the per-process list
"""

import argparse
import re
import subprocess
import sys
import time
from collections import defaultdict

# Longest patterns first: a process matches the first family whose pattern is a
# substring of its command, so "Google Chrome He" must not be caught by a
# broader rule placed above it.
FAMILIES = [
    ("claude sessions", (re.compile(r"^\d+\.\d+\.\d+$"), re.compile(r"claude"))),
    ("bun (MCP + fleet)", (re.compile(r"^bun$"),)),
    ("Chrome", (re.compile(r"Google Chrome"),)),
    ("node / npm", (re.compile(r"^(node|npm)"),)),
    ("Java / Gradle", (re.compile(r"(java|gradle|kotlin)", re.I),)),
    ("Android / emulator", (re.compile(r"(qemu|emulator|adb)", re.I),)),
    ("python", (re.compile(r"^[Pp]ython"),)),
    ("WindowServer", (re.compile(r"WindowServer"),)),
    ("Dropbox / sync", (re.compile(r"(Dropbox|Google Drive|iCloud)", re.I),)),
    # Desktop apps, split out because the first run showed them collectively
    # outweighing the agent fleet -- which is the finding, not a detail.
    ("Loom", (re.compile(r"^Loom"),)),
    ("Claude desktop", (re.compile(r"^Claude( Helper)?"),)),
    ("terminal", (re.compile(r"(iTerm|Terminal|tmux)", re.I),)),
    ("Finder / Spotlight", (re.compile(r"(Finder|Spotlight|mds|mdworker)", re.I),)),
    ("kernel", (re.compile(r"^kernel"),)),
]


def size_to_mb(tok: str):
    """top prints 812M, 1.2G, 44928K. Returns MB, or None if unparseable."""
    tok = tok.strip()
    if not tok or tok in ("-", "N/A"):
        return 0.0
    m = re.match(r"^([\d.]+)([KMGB]?)[+-]?$", tok)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    return {"K": n / 1024, "M": n, "G": n * 1024, "B": n / 1048576, "": n / 1048576}[unit]


def sample(top_n: int):
    """Returns (families, processes) or raises RuntimeError. Never returns zeros
    to mean 'could not look'."""
    try:
        out = subprocess.run(
            ["top", "-l", "1", "-n", "200", "-o", "mem",
             "-stats", "pid,command,mem,cmprs"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        raise RuntimeError(f"could not run top: {err}") from err
    if out.returncode != 0:
        raise RuntimeError(f"top exited {out.returncode}: {out.stderr.strip()[:200]}")

    lines = out.stdout.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("PID") and "CMPRS" in line:
            body = lines[i + 1:]
            break
    else:
        raise RuntimeError("top produced no PID/CMPRS header — output shape changed")

    procs, unparsed = [], 0
    for line in body:
        parts = line.split()
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        pid, mem_s, cmprs_s = parts[0], parts[-2], parts[-1]
        command = " ".join(parts[1:-2])
        mem, cmprs = size_to_mb(mem_s), size_to_mb(cmprs_s)
        if mem is None or cmprs is None:
            unparsed += 1
            continue
        procs.append((int(pid), command, mem, cmprs))

    if not procs:
        raise RuntimeError(f"top parsed to 0 processes ({unparsed} unparseable rows)")

    fam = defaultdict(lambda: [0, 0.0, 0.0])  # count, mem, cmprs
    for _, command, mem, cmprs in procs:
        name = "other"
        for label, pats in FAMILIES:
            if any(p.search(command) for p in pats):
                name = label
                break
        fam[name][0] += 1
        fam[name][1] += mem
        fam[name][2] += cmprs
    return fam, sorted(procs, key=lambda p: -p[2])[:top_n], unparsed


def report(top_n: int):
    fam, procs, unparsed = sample(top_n)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    total_mem = sum(v[1] for v in fam.values())
    total_cmprs = sum(v[2] for v in fam.values())

    print(f"[{stamp}] SAMPLED  {sum(v[0] for v in fam.values())} processes"
          + (f"  ({unparsed} rows unparseable)" if unparsed else ""))
    print(f"  demand = footprint = {total_mem/1024:.2f}GB "
          f"(of which {total_cmprs/1024:.2f} compressed)\n")

    print(f"  {'family':<22}{'procs':>6}{'footprint':>12}{'of it cmprs':>13}{'share':>8}")
    denom = total_mem or 1
    for name, (n, mem, cmprs) in sorted(fam.items(), key=lambda kv: -kv[1][1]):
        print(f"  {name:<22}{n:>6}{mem/1024:>11.2f}G{cmprs/1024:>12.2f}G"
              f"{100*mem/denom:>7.0f}%")

    print(f"\n  top {len(procs)} processes by demand:")
    for pid, command, mem, cmprs in procs:
        print(f"    {pid:>7}  {command[:34]:<34}{mem/1024:>7.2f}G"
              f"   (of which {cmprs:.0f}M compressed)")


def oneline(top_n: int):
    """One line per sample, for a log that gets analysed later rather than read.
    The guard's cadence is 120s; match it so the two series line up by timestamp."""
    fam, _, unparsed = sample(top_n)
    total = sum(v[1] for v in fam.values())
    slug = {"claude sessions": "fleet", "Claude desktop": "claude-app",
            "bun (MCP + fleet)": "bun", "Finder / Spotlight": "finder",
            "Dropbox / sync": "dropbox", "Java / Gradle": "gradle",
            "Android / emulator": "android", "node / npm": "node"}
    parts = " ".join(
        f"{slug.get(name, name.split()[0].lower())}={(v[1]/1024):.2f}"
        for name, v in sorted(fam.items(), key=lambda kv: -kv[1][1])
        if v[1] / 1024 >= 0.05
    )
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] demand {total/1024:.2f}GB · {parts}"
          + (f" · unparsed {unparsed}" if unparsed else ""), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watch", type=int, metavar="SECONDS",
                    help="sample repeatedly at this interval instead of once")
    ap.add_argument("--top", type=int, default=10, help="processes to list (default 10)")
    ap.add_argument("--oneline", action="store_true",
                    help="one compact line per sample, for logging a series")
    args = ap.parse_args()

    while True:
        try:
            (oneline if args.oneline else report)(args.top)
        except RuntimeError as err:
            # The loud third state. Never a zero that reads as a clean result.
            print(f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] COULD-NOT-SAMPLE: {err}",
                  file=sys.stderr)
            return 2
        if not args.watch:
            return 0
        if not args.oneline:
            print()
        time.sleep(args.watch)


if __name__ == "__main__":
    sys.exit(main())
