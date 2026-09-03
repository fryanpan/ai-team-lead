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
INTERVAL="${FLEET_BUDGET_INTERVAL:-900}"        # 15m when the fleet is clear
DANGER_INTERVAL="${FLEET_BUDGET_DANGER_INTERVAL:-300}"  # 5m once it is not
STATE="$HOME/Library/Application Support/team-lead/budget-watch.json"

# 15 minutes is short relative to a five-hour window and far too long relative
# to a fleet that can go from clear to rejected inside one sleep -- on
# 2026-09-03 the projection ran 131M -> 684M between two runs. Sample faster in
# the band where a hold is actually being managed, so that both the ask and the
# RELEASE land near when they are true.
current_interval() {
  python3 -c 'import json,sys
try: lvl = json.load(open(sys.argv[1])).get("admission_level","clear")
except Exception: lvl = "clear"
print(sys.argv[2] if lvl == "clear" else sys.argv[3])' \
    "$STATE" "$INTERVAL" "$DANGER_INTERVAL" 2>/dev/null || echo "$INTERVAL"
}

while true; do
  echo "=== $(date '+%Y-%m-%d %H:%M') ==="
  # --enforce: the loop acts on what it finds rather than only reporting it.
  # 2026-09-03, Bryan: "your responsibility is to do what's necessary to avoid
  # it." Waking a human every 15 minutes cannot beat a fleet that exhausts the
  # window in two hours; the loop has to ask the top burners to stop fanning
  # out, and to release them when the projection recedes.
  python3 "$SCRIPT" --wake --notify --enforce
  sleep "$(current_interval)"
done
