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
  # --enforce is OFF, deliberately. It was on for about six hours on
  # 2026-09-03 and it throttled real work on a number that does not measure
  # the real constraint: the window figure here is a SUM OF TRANSCRIPT TOKENS
  # compared against a ceiling reconstructed from where that sum happened to
  # sit at past rejections. It correlates with rejections; it does not measure
  # what causes one. It also tallies the whole fleet while the limit is
  # per-account, so it can read high against a pool that is nearly untouched --
  # which is exactly what Bryan saw when he checked /usage against it.
  #
  # What settles it is the cost asymmetry, not the accuracy. Bryan can move the
  # fleet to his personal token whenever a pool runs out, so a real rejection
  # costs one switch. A false hold costs work that was asked for and not done.
  # Preemptive throttling only pays when the failure is expensive and the
  # recovery is slow; here the recovery is one login.
  #
  # The trigger that works is the healthcheck's session-limit detector, which
  # observes rejections that ACTUALLY HAPPENED and names the blocked dirs.
  # Report here, act on that.
  python3 "$SCRIPT" --wake --notify
  sleep "$(current_interval)"
done
