#!/usr/bin/env bash
# SessionStart hook for team-lead-fleet plugin.
# Concatenates every rule under rules/ into a single block and injects it
# as additional_context, replicating the alwaysApply behavior the rules
# used to have when they lived in each project's .claude/rules/.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RULES_DIR="${PLUGIN_ROOT}/rules"

# Claude Code passes the hook payload as JSON on stdin; `source` is one of
# startup | resume | clear | compact. We need it because a RESUMED session
# already has an older copy of these rules sitting in its restored transcript:
# `--continue` replays the conversation, injection included, so without a note
# saying which copy wins the model sees two contradictory rule sets and no
# ordering information.
#
# Why `resume` is in the matcher at all (added 2026-08-04): it wasn't, and the
# consequence was that a fleet restart deployed nothing. Rules changed on disk,
# every peer kept running the pre-update text, and every external signal —
# cache hash, drift check, clean restart — said the deploy had worked. A peer
# caught it by grepping its own injected context.
#
# Read defensively: never block on a tty, and treat any parse failure as
# "unknown source" rather than failing the hook (a non-zero exit here would
# strip the rules from the session entirely).
HOOK_SOURCE="unknown"
if [ ! -t 0 ]; then
  hook_input="$(cat 2>/dev/null || printf '')"
  if [ -n "$hook_input" ]; then
    parsed="$(printf '%s' "$hook_input" \
      | /usr/bin/python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("source","unknown"))
except Exception: print("unknown")' 2>/dev/null || printf 'unknown')"
    [ -n "$parsed" ] && HOOK_SOURCE="$parsed"
  fi
fi

if [ ! -d "$RULES_DIR" ]; then
  printf '{}\n'
  exit 0
fi

# Build the concatenated rules block with real newlines (NOT bash literal
# "\n", which is two chars: backslash + n). printf -v + %s\n is the safest
# way to assemble multi-line content in bash without surprises.
rules_content=""
for rule in "$RULES_DIR"/*.md; do
  [ -f "$rule" ] || continue
  rule_name="$(basename "$rule")"
  printf -v header '\n\n<!-- team-lead-fleet rule: %s -->\n' "$rule_name"
  body="$(cat "$rule")"
  rules_content+="${header}${body}"
done

# Wrap with a container so it's visually distinct in the transcript.
# $'...' ANSI-C quoting gives real newlines.
session_context=$'<team-lead-fleet-rules>\n\nThe following rules apply to every peer in the user\'s Claude Code fleet. They are loaded on session start by the team-lead-fleet plugin and behave as alwaysApply rules. Treat them as standing instructions.\n'
if [ "$HOOK_SOURCE" = "resume" ]; then
  session_context+=$'\n**This copy is current and supersedes any earlier <team-lead-fleet-rules> block in this conversation.** This session was resumed, so its restored history still contains the rules as they were when it first started. Where the two disagree, THIS block wins; the earlier one is stale text, not a second policy.\n'
fi
session_context+="${rules_content}"
session_context+=$'\n\n</team-lead-fleet-rules>'

# Escape for embedding in JSON via bash parameter substitution.
escape_for_json() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  printf '%s' "$s"
}

context_escaped=$(escape_for_json "$session_context")

# Claude Code expects hookSpecificOutput.additionalContext.
printf '{\n  "hookSpecificOutput": {\n    "hookEventName": "SessionStart",\n    "additionalContext": "%s"\n  }\n}\n' "$context_escaped"
