#!/usr/bin/env python3
"""
Rotate the fleet's service logs, which nothing has ever rotated.

Measured 2026-09-12: 570 MB across ~/Library/Logs, three files over 10 MB, and
no rotation configured anywhere — /etc/newsyslog.d/ holds only Apple's own
entries and no fleet launchd plist mentions it.

── Why copy-truncate and not rename ────────────────────────────────────────────

Every fleet log is a launchd `StandardOutPath` / `StandardErrorPath`. launchd
opens that file once, at job start, and hands the daemon the descriptor for the
life of the process. The daemon never opens the path itself and never reopens
it.

So renaming the file rotates nothing. The descriptor follows the inode, not the
name: the daemon keeps appending to the now-unnamed old file, the fresh file
stays empty forever, and the rotation looks like it worked. That is the exact
shape this fleet keeps getting burned by — an instrument whose output cannot
distinguish working from not working.

newsyslog's default mode is that rename, and it clears it by signalling the
writer to reopen. These daemons have no pidfile and handle no such signal, so
that escape hatch is not available either.

Copy-truncate keeps the inode. We copy the content aside, then truncate the
file the daemon is already holding open. Its next write lands at the new end of
file, which is now zero.

**This depends on the descriptor being O_APPEND**, or the daemon would write at
its own remembered offset and leave a sparse file of null padding. launchd does
use O_APPEND. Do not take that on trust — `--verify` proves it against a live
writer and reports what it actually observed.

── Three states, not two ───────────────────────────────────────────────────────

Every log is reported as rotated, skipped, or COULD-NOT — the third loudly and
never folded into either of the others. A log we failed to read is not a log
that was fine.

Usage:

    python3 scripts/rotate_fleet_logs.py                  # dry run
    python3 scripts/rotate_fleet_logs.py --apply
    python3 scripts/rotate_fleet_logs.py --apply --verify <path>

Dry run by default. --apply writes.
"""

import argparse
import gzip
import os
import shutil
import sys
import time
from pathlib import Path

LOG_DIR = Path.home() / "Library" / "Logs"
DEFAULT_THRESHOLD_MB = 10
DEFAULT_KEEP = 3


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n/1:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}GB"


def size_str(n: int) -> str:
    for unit, div in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= div:
            return f"{n/div:.1f}{unit}"
    return f"{n}B"


def shift_generations(log: Path, keep: int, apply: bool) -> None:
    """file.log.2.gz -> file.log.3.gz, ... dropping anything past `keep`."""
    oldest = log.with_suffix(log.suffix + f".{keep}.gz")
    if oldest.exists():
        if apply:
            oldest.unlink()
        else:
            print(f"        would drop {oldest.name} (past keep={keep})")
    for i in range(keep - 1, 0, -1):
        src = log.with_suffix(log.suffix + f".{i}.gz")
        dst = log.with_suffix(log.suffix + f".{i+1}.gz")
        if src.exists():
            if apply:
                src.rename(dst)
            else:
                print(f"        would move {src.name} -> {dst.name}")


def rotate(log: Path, keep: int, apply: bool) -> str:
    """Copy the content aside gzipped, then truncate in place. Returns a verdict."""
    try:
        before = log.stat().st_size
    except OSError as err:
        print(f"  COULD-NOT  {log.name}: cannot stat ({err})")
        return "could-not"

    shift_generations(log, keep, apply)
    dest = log.with_suffix(log.suffix + ".1.gz")

    if not apply:
        print(f"  would rotate {log.name}  {size_str(before)} -> {dest.name}, then truncate")
        return "rotated"

    try:
        # Copy first, truncate second. If the copy fails we have changed nothing.
        with open(log, "rb") as src, gzip.open(dest, "wb", compresslevel=9) as out:
            shutil.copyfileobj(src, out, length=1 << 20)
        os.truncate(log, 0)
    except OSError as err:
        print(f"  COULD-NOT  {log.name}: {err}")
        return "could-not"

    after = dest.stat().st_size
    print(f"  rotated    {log.name}  {size_str(before)} -> {dest.name} {size_str(after)}, truncated in place")
    return "rotated"


def verify(log: Path, wait_s: int) -> int:
    """
    Prove the descriptor is O_APPEND by watching where the writer's next line lands.

    A sparse file — size back near its pre-truncate length with a run of NULs at
    the front — means the writer kept its own offset and the rotation silently
    lost the new lines. That is the failure this check exists to catch, and it is
    invisible from the size alone.
    """
    print(f"\nverifying against a live writer: {log.name}")
    start = log.stat().st_size
    print(f"  size now: {size_str(start)}   waiting up to {wait_s}s for the next write")

    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(2)
        now = log.stat().st_size
        if now > start:
            with open(log, "rb") as f:
                head = f.read(64)
            if head.startswith(b"\0"):
                print(f"  FAILED — file grew to {size_str(now)} but begins with NUL padding.")
                print("  The writer is NOT in append mode; it kept its own offset.")
                print("  Copy-truncate is unsafe for this log. Do not deploy.")
                return 1
            print(f"  PASSED — grew to {size_str(now)}, content starts at byte 0, no NUL padding.")
            print(f"  First bytes: {head[:60]!r}")
            print("  The descriptor is O_APPEND; the next write went to the new EOF.")
            return 0
        start = min(start, now)

    print(f"  COULD-NOT VERIFY — nothing written in {wait_s}s. This is not a pass.")
    print("  Pick a log with a frequent writer, or wait longer.")
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually write (default is a dry run)")
    ap.add_argument("--threshold-mb", type=int, default=DEFAULT_THRESHOLD_MB)
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="compressed generations to retain")
    ap.add_argument("--dir", type=Path, default=LOG_DIR)
    ap.add_argument("--verify", type=Path, metavar="LOG",
                    help="after rotating, prove the writer's next line lands in the truncated file")
    ap.add_argument("--verify-wait", type=int, default=150)
    args = ap.parse_args()

    threshold = args.threshold_mb << 20

    if not args.dir.is_dir():
        print(f"COULD-NOT: {args.dir} is not a directory", file=sys.stderr)
        return 2

    logs = sorted(args.dir.glob("*.log"), key=lambda p: -p.stat().st_size)
    print(f"dir:       {args.dir}")
    print(f"threshold: {args.threshold_mb}MB    keep: {args.keep} generations")
    print(f"mode:      {'APPLY' if args.apply else 'dry run'}")
    print(f"logs:      {len(logs)} found\n")

    counts = {"rotated": 0, "skipped": 0, "could-not": 0}
    for log in logs:
        try:
            sz = log.stat().st_size
        except OSError as err:
            print(f"  COULD-NOT  {log.name}: cannot stat ({err})")
            counts["could-not"] += 1
            continue
        if sz < threshold:
            counts["skipped"] += 1
            continue
        counts[rotate(log, args.keep, args.apply)] += 1

    print(f"\nrotated {counts['rotated']}  skipped {counts['skipped']}  COULD-NOT {counts['could-not']}")
    if counts["could-not"]:
        print("\nThis is NOT a clean result. A log that could not be read is not a log that was fine.")

    if not args.apply:
        print("\ndry run — pass --apply to write")
        return 0

    if args.verify:
        return verify(args.verify, args.verify_wait)
    return 0


if __name__ == "__main__":
    sys.exit(main())
