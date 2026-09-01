#!/bin/bash
# Rolling-window burn watch — runs inside tmux, NOT launchd.
#
# Why tmux: a launchd daemon does not inherit Terminal's disk access to the
# external /Volumes/Data, so it cannot read the transcripts this measures. Same
# constraint as fleet_monitor_loop.sh; see docs/process/fleet-ops.md.
#
# Why 15 minutes: the failure this exists to catch — one project fanning out
# subagents hard enough to exhaust a 5-hour session window — ran its course in
# about two hours on 2026-08-31. A daily or 3x/day instrument cannot see it. The
# interval has to be short relative to the window it protects.
#
# Launch (detached):
#   tmux new-session -d -s fleet-budget \
#     /Volumes/Data/Users/bryanchan/dev/ai-team-lead/scripts/fleet_budget_loop.sh
SCRIPT="/Volumes/Data/Users/bryanchan/dev/ai-team-lead/scripts/fleet_budget_watch.py"
INTERVAL="${FLEET_BUDGET_INTERVAL:-900}"   # 15m

while true; do
  echo "=== $(date '+%Y-%m-%d %H:%M') ==="
  python3 "$SCRIPT" --wake --notify
  sleep "$INTERVAL"
done
