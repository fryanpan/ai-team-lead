#!/bin/bash
# Fleet context monitor loop — runs inside tmux (inherits Terminal's disk access,
# which a launchd/cron daemon lacks for the external /Volumes/Data). Zero LLM turns.
#
#   * every 2h: measure fleet context and FLAG quiet sessions >= 450k for review.
#     It never compacts anything -- the report only prints and notifies.
#   * --notify only during the workday (weekdays 09:00–19:59) so it doesn't ping
#     overnight.
#
# `--auto-compact` below is a DEAD FLAG kept only because removing it would change
# nothing: fleet_context_report.py has no such option and silently ignores it. The
# comment above used to claim "auto-compact runs around the clock", which was never
# true of the current script and would have sent a reader looking for a compaction
# that never happens.
#
# Launch (detached), by this script's physical path:
#   tmux new-session -d -s fleet-monitor <checkout>/scripts/fleet_monitor_loop.sh
# The report script is taken from the main checkout. This script's own folder
# gives the checkout it is in. When that is a linked git worktree its `.git` is
# a file that names the main checkout, and the path is read from there, with
# no other program involved.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
CHECKOUT="${HERE%/*}"
if [ -f "$CHECKOUT/.git" ]; then
  read -r GITDIR < "$CHECKOUT/.git"
  GITDIR="${GITDIR#gitdir: }"
  case "$GITDIR" in
    /*/.git/worktrees/*) CHECKOUT="${GITDIR%/.git/worktrees/*}" ;;
    *) echo "ABORT: $CHECKOUT is a linked worktree and its .git file does not name the main checkout. Start this loop from the main checkout." >&2
       exit 1 ;;
  esac
fi
SCRIPT="$CHECKOUT/scripts/fleet_context_report.py"
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
