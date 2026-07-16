#!/bin/bash
# Deny writing-editor dispatch from inside a subagent.
#
# Why: the delegate-writing rule tells agents to hand doc work to
# team-lead-fleet:writing-editor. Custom subagents load CLAUDE.md and project
# rules, so that rule is inherited by the writing subagent itself — which invites
# it to delegate its own job back to a fresh copy of itself, forever. A subagent
# writes its own doc; only a top-level session delegates.
#
# Rules alone don't hold here — an inherited alwaysApply rule is advice a subagent
# can rationalize past, and the prose gate at the top of delegate-writing.md is
# exactly the kind of thing that gets skimmed under load. This hook is the
# enforcement point. Same lesson, same shape, same discriminator as
# block-hive-in-subagents.sh.
#
# Mechanism: PreToolUse fires for tool calls inside subagents, and the payload
# carries `agent_id` ONLY for subagents (absent/null in the main session). That's
# the whole discriminator — top-level peers delegate freely, subagents cannot.
#
# Fail-open by design: if jq is missing or the payload is unparseable we emit
# nothing and exit 0 ("no decision, use the normal permission flow"). A broken hook
# must never block the fleet from writing docs.

set -u

command -v jq >/dev/null 2>&1 || exit 0

payload=$(cat)   # read stdin exactly once

agent_id=$(printf '%s' "$payload" | jq -r '.agent_id // empty' 2>/dev/null)
tool_name=$(printf '%s' "$payload" | jq -r '.tool_name // empty' 2>/dev/null)
subagent_type=$(printf '%s' "$payload" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null)

# Main session -> agent_id absent. Leave it alone.
[ -n "$agent_id" ] || exit 0

# Only care about Agent dispatches naming the writing subagent.
[ "$tool_name" = "Agent" ] || exit 0

case "$subagent_type" in
  *writing-editor*)
    jq -n '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: "The writing-editor subagent is not available to subagents. You were dispatched to do this work — write the doc yourself and return the path to your caller. Delegating writing onward from inside a subagent recurses."
      }
    }'
    exit 0
    ;;
esac

exit 0
