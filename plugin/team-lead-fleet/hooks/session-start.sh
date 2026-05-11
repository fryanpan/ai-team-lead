#!/usr/bin/env bash
# SessionStart hook for team-lead-fleet plugin.
# Concatenates every rule under rules/ into a single block and injects it
# as additional_context, replicating the alwaysApply behavior the rules
# used to have when they lived in each project's .claude/rules/.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RULES_DIR="${PLUGIN_ROOT}/rules"

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
