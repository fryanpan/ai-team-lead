#!/usr/bin/env bash
# SessionStart hook (team-lead only — lives in ai-team-lead/.claude/settings.json,
# so it never fires for peer sessions).
#
# Why this exists: the token-watch that logs weekly %-used 3×/day is an in-session
# CronCreate job. CronCreate is session-scoped — it dies whenever the team-lead
# respawns. On 2026-07-11→14 that killed it silently and the trend log went dark
# for 3 days. A shell hook CANNOT call CronCreate (it's an in-session tool), so
# instead this injects a standing directive that makes the model re-arm the watch
# at the top of every session — including `resume`, which is what --continue
# (the respawn path) fires. The re-arm is idempotent: the directive says check
# CronList first and skip if already armed.

set -euo pipefail

read -r -d '' DIRECTIVE <<'EOF' || true
[Token-watch re-arm — team-lead startup check]
The weekly-usage token-watch is a session-scoped CronCreate job that dies on respawn. Ensure it is armed now:
1. Call CronList. If a job with a token-watch prompt (pulls /usage, appends to docs/process/token-control.md trend log) is already present, do nothing — it's armed.
2. If absent, arm it: CronCreate(cron="7 8,13,18 * * *", recurring=true) with the token-watch prompt documented in docs/process/token-control.md. It pulls /usage via an idle fleet session, runs the burn + context scripts, appends one trend-log line (all-models % + Fable sub-meter % + %-elapsed + verdict), applies Tier 0/1, pings Bryan only on a Tier 2 call, AND on the first run after a weekly reset runs the end-of-week quota retro (§ End-of-week quota retro) — that retro is owned and fires without being asked.
Do this once, silently, as a startup housekeeping step — no need to announce it unless the watch was found dead (worth a one-line note if so).
EOF

# Emit as SessionStart additionalContext. jq -Rs slurps the raw directive and
# JSON-encodes it safely (handles newlines/quotes without hand-rolled escaping).
jq -n --arg ctx "$DIRECTIVE" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
