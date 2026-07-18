#!/bin/bash
# Fleet context monitor loop — runs inside tmux (inherits Terminal's disk access,
# which a launchd/cron daemon lacks for the external /Volumes/Data). Zero LLM turns.
#
#   * every 2h: measure fleet context, auto-/compact idle sessions >= 450k
#   * --notify only during the workday (weekdays 09:00–19:59) so it doesn't ping
#     overnight; auto-compact runs around the clock (freeing idle giants is
#     always good).
#
# Launch (detached):  tmux new-session -d -s fleet-monitor \
#                       /Volumes/Data/Users/bryanchan/dev/ai-team-lead/scripts/fleet_monitor_loop.sh
SCRIPT="/Volumes/Data/Users/bryanchan/dev/ai-team-lead/scripts/fleet_context_report.py"
INTERVAL="${FLEET_MONITOR_INTERVAL:-7200}"   # 2h

while true; do
  h=$(date +%H); dow=$(date +%u)
  args=(--auto-compact)
  if [ "$dow" -le 5 ] && [ "$h" -ge 9 ] && [ "$h" -lt 20 ]; then
    args+=(--notify)
  fi
  echo "=== $(date '+%Y-%m-%d %H:%M') run (${args[*]}) ==="
  python3 "$SCRIPT" "${args[@]}"
  sleep "$INTERVAL"
done
