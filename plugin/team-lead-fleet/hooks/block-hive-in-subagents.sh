#!/bin/bash
# Deny claude-hive tool calls made from inside a subagent.
#
# Why: claude-hive is the peer network for TOP-LEVEL project sessions. A subagent
# that reaches into it bypasses its own parent — it registers its own stable_id and
# pings the team-lead directly, landing unasked-for status messages in the team-lead's
# context. That is precisely the cost delegating the work was meant to avoid.
# A subagent reports to its parent, on the channel its parent dispatched it with
# (its return value, or SendMessage as a named teammate). Never over the hive.
#
# Rules alone don't hold here: an alwaysApply rule telling subagents not to do this
# is inherited advice they can rationalize past, and a bare `Agent` call defaults to
# general-purpose, which no agent-definition `disallowedTools` can constrain. This
# hook is the enforcement point that actually covers every subagent.
#
# Mechanism: PreToolUse fires for tool calls inside subagents, and the payload carries
# `agent_id` ONLY for subagents (absent/null in the main session). That's the whole
# discriminator — top-level peers keep full hive access, subagents get none.
#
# Fail-open by design: if jq is missing or the payload is unparseable we emit nothing
# and exit 0, which is "no decision, use the normal permission flow." A broken hook
# must never wedge the fleet's own coordination channel.

set -u

command -v jq >/dev/null 2>&1 || exit 0

payload=$(cat)   # read stdin exactly once

agent_id=$(printf '%s' "$payload" | jq -r '.agent_id // empty' 2>/dev/null)
tool_name=$(printf '%s' "$payload" | jq -r '.tool_name // empty' 2>/dev/null)

# Main session -> agent_id absent. Leave it alone.
[ -n "$agent_id" ] || exit 0

case "$tool_name" in
  mcp__claude-hive__*)
    jq -n '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: "claude-hive is not available to subagents. You are not a peer on the hive — report to the agent that dispatched you, on the channel it dispatched you with (your final message, or SendMessage if you are a named teammate). Do not try to reach the team-lead or other project sessions directly."
      }
    }'
    exit 0
    ;;
esac

exit 0
