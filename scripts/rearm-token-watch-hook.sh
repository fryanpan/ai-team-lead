#!/usr/bin/env bash
# SessionStart hook (team-lead only — registered in ai-team-lead/.claude/settings.json,
# so it never fires for peer sessions).
#
# Why this exists NOW: it used to re-arm session-scoped CronCreate jobs, which die on
# every respawn. On 2026-09-18 all three moved to BOARD SCHEDULES, which survive a
# respawn. So this hook no longer re-arms anything — its remaining job is to stop a
# fresh session from "helpfully" re-creating the crons it no longer finds.
#
# It is registered in settings.json and can only be unregistered by Bryan. Until then
# it stays, emitting the directive below. Filename is legacy.

set -euo pipefail

read -r -d '' DIRECTIVE <<'EOF' || true
[Team-lead startup check — recurring jobs]
**Do NOT arm any CronCreate job. There are none left to re-arm.** All three recurring
jobs moved to board schedules on 2026-09-18, on the Team Lead board. A board schedule
survives the respawn that kills a session cron, which is the whole reason they moved.

**Verify with list_tasks on those rows, not with CronList.** CronList returning nothing
is now the CORRECT state, not a dead job.

1. Token watch — cadence is whatever the row's own schedule says. Read the armed
   times; do not restate them as an interval. The gaps are NOT uniform, and deriving
   one is what made the board's own stall detector file a false "has not succeeded in
   4h" on 2026-09-21 against a run that was on time. Runs the /token-watch skill; the
   contract is docs/process/token-control.md.
2. Morning digest — daily at 06:47 America/Los_Angeles. The procedure lives in
   .claude/skills/morning-digest/SKILL.md. /daily-review keeps only its intra-day triggers.
3. Weekly digest surfacing — Mondays at 08:23 America/Los_Angeles. Read-and-relay only;
   never ask the peer to run a refresh, which re-scrapes a paid aggregator.

**If CronList shows any of these armed as a cron, delete it** — a cron and a board
schedule both firing means two runs. In particular the old "27 5 * * *" daily-review job
is gone and must not come back: Bryan asked for ONE 7AM prep, and a 5:27 review alongside
it is two briefings ninety minutes apart.

**This hook is itself scheduled for removal.** Its registration in .claude/settings.json
is Bryan's to delete; a review item on the Team Lead board carries the snippet.
EOF

# Emit as SessionStart additionalContext. jq -n --arg JSON-encodes the raw directive
# safely (handles newlines/quotes without hand-rolled escaping).
jq -n --arg ctx "$DIRECTIVE" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
