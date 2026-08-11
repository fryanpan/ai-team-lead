#!/usr/bin/env bash
# SessionStart hook (team-lead only — lives in ai-team-lead/.claude/settings.json,
# so it never fires for peer sessions).
#
# Why this exists: the team-lead runs standing in-session CronCreate jobs — the
# weekly-usage token-watch (3x/day) and the automated morning daily-review +
# Asana sync. CronCreate is session-scoped, so both die whenever the team-lead
# respawns (--continue fires SessionStart `resume`). On 2026-07-11->14 a dead
# token-watch blacked out the trend log for 3 days. A shell hook CANNOT call
# CronCreate (it's an in-session tool), so instead this injects a standing
# directive that makes the model re-arm both at the top of every session. The
# re-arms are idempotent: each says check CronList first and skip if armed.
# (Filename is legacy — it now re-arms both crons, not just the token-watch.)

set -euo pipefail

read -r -d '' DIRECTIVE <<'EOF' || true
[Team-lead session-cron re-arm — startup check]
Three session-scoped CronCreate jobs die on respawn. Ensure ALL THREE are armed now — idempotent: call CronList first and skip any already present. Do this silently as startup housekeeping; note only if a job was found dead.

1. Token-watch (weekly usage, 3x/day). If a job with a token-watch prompt (pulls /usage, appends to docs/process/token-control.md trend log) is present, skip. Else arm: CronCreate(cron="7 8,13,18 * * *", recurring=true) with the token-watch prompt documented in docs/process/token-control.md — pulls /usage via an idle fleet session, runs the burn + context scripts, appends one trend-log line (all-models % + Fable sub-meter % + %-elapsed + verdict), applies Tier 0/1, pings Bryan only on a Tier 2 call, AND on the first run after a weekly reset runs the end-of-week quota retro (docs/process/token-control.md, "End-of-week quota retro").

2. Automated morning daily-review + Asana sync (5:27am). If a job with the daily-review morning prompt (invokes the /daily-review automated morning run) is present, skip. Else arm: CronCreate(cron="27 5 * * *", recurring=true) with a prompt that invokes the /daily-review skill's "Automated morning run" — gather fleet status, write today's review doc under live-feedback, produce Bryan's status + today's hit list, and SYNC his Asana so today's tasks match the hit list (mark done what shipped overnight; keep today's items dated today; shift other tasks for the week to later days to respect the weekly Capacity block; add any newly-surfaced must-do). Then send one "Good morning — today: <2-4 items>" PushNotification. Asana ref: workspace ASANA_WORKSPACE_GID, project "Bryan's Projects" ASANA_PROJECT_GID, Bryan (assignee) ASANA_ASSIGNEE_GID, non-premium so use asana_get_tasks not search_tasks. Leave family and other people's tasks + Bryan-Medical self-care items alone.

3. Weekly peer refresh + digest (Monday 08:23 local). If a job with this weekly-refresh prompt is present, skip. Else arm: CronCreate(cron="23 8 * * 1", recurring=true) with a prompt that wakes the peer at claude-hive stable_id 004e35052d8b — spawning its session first if it is not running, path per registry.yaml — and hands it the goal: run its weekly sourcing refresh, then build and surface the resulting digest to Bryan on whichever surface he is active on (Claude Remote before CLI). The peer owns how; do not prescribe commands and do not ask it for status.
EOF

# Emit as SessionStart additionalContext. jq -Rs slurps the raw directive and
# JSON-encodes it safely (handles newlines/quotes without hand-rolled escaping).
jq -n --arg ctx "$DIRECTIVE" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
