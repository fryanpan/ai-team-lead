#!/usr/bin/env python3
"""Answer "did that channel event actually reach the session?" from the record.

A channel event that lands in a session's queue and is never acted on leaves the
same trace as one that was never sent -- from outside AND from inside that
session. This walks the four legs that each keep their own record, so the two
cases can be told apart:

  1. the MCP server's stderr log   -- the push leaving the server
  2. the session transcript        -- enqueue / remove / queued_command
  3. the broker's message table    -- queued, acked, or never inserted
  4. a control message             -- one the session demonstrably acted on

Two things this exists to stop you doing:

  * Counting by the ``source=`` attribute. A bridge that routes through another
    channel keeps THAT channel's source. Sentry events arrive as
    source="claude-hive", so a tally bucketed by source files every one of them
    under the hive's count and reports zero for the bridge. Match on payload --
    the project slug, the issue title -- not on transport.

  * Reading an absent broker row as loss. Undelivered rows are kept for a TTL
    (24h by default) and acked rows are deleted, so a missing row inside the TTL
    means the recipient acked it. The intuition runs the other way.

Usage:
    channel_delivery_trace.py --cwd ~/dev/some-project --match WORKSPACES-SERVER-2
    channel_delivery_trace.py --cwd ~/dev/some-project --match Sentry --since 2026-09-10T06:00
"""

import argparse
import glob
import json
import os
import sqlite3
import sys

HOME = os.path.expanduser("~")
CACHE_ROOT = os.path.join(HOME, "Library/Caches/claude-cli-nodejs")
PROJECT_ROOT = os.path.join(HOME, ".claude/projects")
HIVE_DB = os.path.join(HOME, ".claude-hive.db")


def encode_cwd(path):
    """Claude Code's directory encoding: / _ . all become -."""
    real = os.path.realpath(os.path.expanduser(path))
    return real.replace("/", "-").replace("_", "-").replace(".", "-")


def newest(pattern):
    hits = sorted(glob.glob(pattern), key=os.path.getmtime)
    return hits[-1] if hits else None


def scan_mcp_log(cwd, match, since):
    """Leg 1 -- did the MCP server emit the channel push?"""
    enc = encode_cwd(cwd)
    path = newest(os.path.join(CACHE_ROOT, enc, "mcp-logs-claude-hive", "*.jsonl"))
    if not path:
        return None, []
    found = []
    with open(path, errors="replace") as fh:
        for line in fh:
            if match not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ts = rec.get("timestamp", "")
            if since and ts < since:
                continue
            body = rec.get("debug") or ""
            if "notifications/claude/channel" in body:
                found.append(ts)
    return path, found


def scan_transcript(cwd, match, since):
    """Leg 2 -- did it reach the queue, and was it taken into a turn?"""
    enc = encode_cwd(cwd)
    path = newest(os.path.join(PROJECT_ROOT, enc, "*.jsonl"))
    if not path:
        return None, {}
    legs = {"enqueue": [], "remove": [], "queued_command": 0, "other": 0}
    with open(path, errors="replace") as fh:
        for line in fh:
            if match not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ts = rec.get("timestamp", "")
            if since and ts and ts < since:
                continue
            if rec.get("type") == "queue-operation":
                op = rec.get("operation")
                if op in legs:
                    legs[op].append(ts)
                continue
            att = rec.get("attachment") or {}
            if att.get("type") == "queued_command":
                legs["queued_command"] += 1
            else:
                legs["other"] += 1
    return path, legs


def scan_broker(stable_id):
    """Leg 3 -- queued, or never inserted. An ABSENT row means acked."""
    if not os.path.exists(HIVE_DB):
        return None
    con = sqlite3.connect(f"file:{HIVE_DB}?mode=ro", uri=True)
    try:
        if stable_id:
            rows = con.execute(
                "SELECT id, from_id, sent_at, delivered FROM messages "
                "WHERE to_stable_id = ? ORDER BY id",
                (stable_id,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT to_stable_id, COUNT(*), MIN(sent_at), MAX(sent_at) "
                "FROM messages GROUP BY to_stable_id"
            ).fetchall()
    finally:
        con.close()
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cwd", required=True,
                    help="the RECIPIENT session's working directory")
    ap.add_argument("--match", required=True,
                    help="a payload substring -- an issue title, a project slug. "
                         "NOT a source= attribute; see the module docstring.")
    ap.add_argument("--since", default=None, help="ISO timestamp lower bound")
    ap.add_argument("--stable-id", default=None,
                    help="the recipient's hive stable_id, to check the broker queue")
    args = ap.parse_args()

    print(f"tracing {args.match!r} into {args.cwd}\n")

    log_path, pushes = scan_mcp_log(args.cwd, args.match, args.since)
    print("1. MCP server push")
    if log_path is None:
        print("   no claude-hive MCP log for that cwd -- check the path is the")
        print("   RECIPIENT's cwd, not the sender's")
    else:
        print(f"   {log_path}")
        print(f"   {len(pushes)} channel push(es)" +
              (f", first {pushes[0]}, last {pushes[-1]}" if pushes else ""))

    tr_path, legs = scan_transcript(args.cwd, args.match, args.since)
    print("\n2. Session transcript")
    if tr_path is None:
        print("   no transcript for that cwd")
    else:
        print(f"   {tr_path}")
        print(f"   enqueue: {len(legs['enqueue'])}   remove: {len(legs['remove'])}"
              f"   queued_command: {legs['queued_command']}"
              f"   other mentions: {legs['other']}")
        if legs["enqueue"]:
            print(f"   first enqueue {legs['enqueue'][0]}")
        if legs["enqueue"] and legs["remove"] and legs["queued_command"]:
            print("   -> DELIVERED INTO A TURN. If nothing happened next, the")
            print("      session received it and did not act -- not a delivery bug.")
        elif legs["enqueue"]:
            print("   -> reached the queue but was not taken into a turn")
        else:
            print("   -> no queue trace. Widen --since, or check --match is a")
            print("      payload string and not a source= attribute.")

    print("\n3. Broker queue")
    rows = scan_broker(args.stable_id)
    if rows is None:
        print(f"   no broker db at {HIVE_DB}")
    elif args.stable_id:
        if not rows:
            print(f"   no rows for {args.stable_id}. Inside the 24h TTL that means")
            print("   ACKED, not lost -- acked rows are deleted, undelivered ones kept.")
        for r in rows:
            state = "delivered" if r[3] else "QUEUED, not yet acked"
            print(f"   msg {r[0]} from {r[1]} at {r[2]} -- {state}")
    else:
        print("   pass --stable-id to check one mailbox. Current queue depth:")
        for r in rows:
            print(f"   {r[0]:<16} {r[1]:>3} row(s)  {r[2]} .. {r[3]}")

    print("\n4. Control")
    print("   Re-run with --match set to a message this session demonstrably")
    print("   acted on. If the two traces have the same shape, delivery is not")
    print("   the problem.")


if __name__ == "__main__":
    sys.exit(main())
