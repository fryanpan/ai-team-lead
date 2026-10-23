#!/usr/bin/env python3
"""Claude Code statusLine command that records the live subscription meter.

Claude Code pipes a JSON blob to the statusLine command on every render. While the
API reports them, it carries rate_limits.five_hour and rate_limits.seven_day, each
with used_percentage and resets_at (epoch seconds). This is the same meter /usage
shows, read without driving anyone's pane.

Writes the latest reading to ~/Library/Application Support/team-lead/usage-latest.json
and prints a one-line status. A render with no rate_limits leaves the file alone, so
the file's captured_at says how fresh the reading is. The Fable-only bar is not in the
statusLine data, so it is not recorded here.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

STATE = Path(os.environ.get("USAGE_STATE_FILE") or Path.home() / "Library/Application Support/team-lead/usage-latest.json")


def account_email():
    try:
        with open(Path.home() / ".claude.json") as f:
            return json.load(f).get("oauthAccount", {}).get("emailAddress")
    except Exception:
        return None


def window(limits, key):
    w = (limits or {}).get(key)
    if not isinstance(w, dict) or w.get("used_percentage") is None:
        return None
    return {"used_percentage": w["used_percentage"], "resets_at": w.get("resets_at")}


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        print("usage: no input")
        return
    limits = data.get("rate_limits")
    five, week = window(limits, "five_hour"), window(limits, "seven_day")
    if five or week:
        rec = {
            "captured_at": int(time.time()),
            "account": account_email(),
            "model": (data.get("model") or {}).get("id"),
            "five_hour": five,
            "seven_day": week,
        }
        try:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=STATE.parent, prefix=".usage-")
            with os.fdopen(fd, "w") as f:
                json.dump(rec, f)
            os.replace(tmp, STATE)
        except Exception:
            pass
    parts = []
    if five:
        parts.append(f"5h {five['used_percentage']:.0f}%")
    if week:
        parts.append(f"wk {week['used_percentage']:.0f}%")
    print(" · ".join(parts) if parts else "usage: not reported yet")


if __name__ == "__main__":
    main()
