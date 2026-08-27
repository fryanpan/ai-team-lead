#!/usr/bin/env bash
# SessionStart hook for team-lead-fleet plugin.
# Concatenates the rules under rules/ that APPLY TO THIS SESSION into a single
# block and injects it as additional_context, replicating the alwaysApply
# behavior the rules used to have when they lived in each project's
# .claude/rules/.
#
# Which rules apply is decided by frontmatter IN THE RULE FILES (`gate:` and
# `appliesTo:`), never by a list here -- a list in the hook goes stale the first
# time someone adds a rule. See "Gating" below.

set -euo pipefail

# A SessionStart hook does NOT inherit a login shell's PATH. Claude Code may
# invoke it with a minimal or empty one, and `set -e` turns the first missing
# binary into a silent TOTAL failure: the hook dies before injecting anything,
# so the session comes up with NO fleet rules and nothing anywhere reports it.
# Seen 2026-08-25 as `line 9: dirname: command not found`.
# Two defences, because the single-point fix has already failed here once
# (python3 was pinned to an absolute path; dirname and basename were missed):
#   1. Pin a known-good PATH -- the system dirs always exist on macOS.
#   2. Use shell builtins instead of dirname/basename below, so a broken PATH
#      cannot take the hook down even if this export is ever removed.
# The gating added 2026-08-26 uses ONLY bash builtins for the same reason --
# no cmp, no sed, no grep. A missing binary must never be able to change which
# rules a session receives.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin${PATH:+:$PATH}"

_hook_dir="${0%/*}"
[ "$_hook_dir" = "$0" ] && _hook_dir="."
SCRIPT_DIR="$(cd "$_hook_dir" && pwd)"
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
# every peer kept running the pre-update text, and every external signal --
# cache hash, drift check, clean restart -- said the deploy had worked. A peer
# caught it by grepping its own injected context.
#
# Read defensively: never block on a tty, and treat any parse failure as
# "unknown" rather than failing the hook (a non-zero exit here would strip the
# rules from the session entirely). "unknown" always means INJECT MORE.
HOOK_SOURCE="unknown"
AGENT_TYPE="unknown"
HOOK_CWD=""
if [ ! -t 0 ]; then
  hook_input="$(cat 2>/dev/null || printf '')"
  if [ -n "$hook_input" ]; then
    parsed="$(printf '%s' "$hook_input" \
      | /usr/bin/python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    print(d.get("source") or "unknown")
    print(d.get("agent_type") or "unknown")
    print(d.get("cwd") or "")
except Exception:
    print("unknown"); print("unknown"); print("")' 2>/dev/null || printf 'unknown\nunknown\n\n')"
    _l1=""; _l2=""; _l3=""
    { IFS= read -r _l1; IFS= read -r _l2; IFS= read -r _l3; } <<< "$parsed" || :
    [ -n "${_l1:-}" ] && HOOK_SOURCE="$_l1"
    [ -n "${_l2:-}" ] && AGENT_TYPE="$_l2"
    [ -n "${_l3:-}" ] && HOOK_CWD="$_l3"

    # Payload log. Observability only -- this MUST NOT change what gets
    # injected. It is how the `agent_type` values below were checked against
    # reality rather than against documentation, and it is how the next gate
    # should be checked too. Never fails the hook: the redirect is guarded and
    # the line ends in `|| :`.
    {
      printf '%s\n' "$hook_input" >> "${HOME}/Library/Logs/team-lead-fleet-hook-payloads.jsonl"
    } 2>/dev/null || :
  fi
fi
[ -n "$HOOK_CWD" ] || HOOK_CWD="$PWD"

if [ ! -d "$RULES_DIR" ]; then
  printf '{}\n'
  exit 0
fi

# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------
#
# Two gates, both driven by frontmatter in the rule file itself:
#
#   gate: never       Injected unconditionally, for every session and every
#                     agent, and never deduplicated. This is the security and
#                     untrusted-data set: a session that does not receive these
#                     can leak a credential or execute injected instructions.
#                     Nothing below may skip a rule carrying this field.
#
#   appliesTo: main   Injected only into a top-level session. Skipped when the
#                     payload PROVES this is not one (`agent_type` present and
#                     not "main"). A missing or unrecognised agent_type means
#                     inject -- unknown always resolves toward more rules.
#
#   (absent)          Default. Injected into everything. A new rule with no
#                     frontmatter therefore behaves exactly as it did before
#                     this gating existed.
#
# MEASURED 2026-08-26, and the reason the appliesTo gate saves nothing yet:
# this hook does NOT currently run for subagents. 586 subagents were dispatched
# in the 7 days the payload log has been open and not one of them produced a
# SessionStart payload; all 283 real payloads carry a top-level session_id and
# none carries an `agent_type` field at all. (The single logged `agent_type` is
# a hand-written probe with session_id "probe".) Subagents get the fleet rules
# by a different route -- the project-instruction load of .claude/rules/*.md --
# which this hook cannot reach. The appliesTo gate is correct and inert today;
# it starts paying the moment the harness fires SessionStart for a subagent or
# an agent-team member.
#
# The third gate is not frontmatter, it is a fact about the session:
#
#   DEDUP           A rule is skipped when $CWD/.claude/rules/<same-name>.md
#                   holds a BYTE-IDENTICAL copy. Such a project already loads
#                   that exact text as a project instruction, so injecting it
#                   again puts the same bytes in the window twice, on every
#                   turn. Byte equality is the whole safety argument: a copy
#                   that differs by one character is a STALE copy, and stale is
#                   exactly the failure the 2026-08-04 note above describes --
#                   so a differing copy is injected, not skipped.
#                   `gate: never` rules are exempt from dedup as well. The
#                   evidence that project instructions always load is
#                   observational, and the cost of being wrong about a security
#                   rule is not worth 17KB.

# Read one frontmatter field with builtins only. Prints the value, or nothing.
# Always returns 0 -- a malformed rule must degrade to "no frontmatter", which
# means "inject".
_fm_field() {
  local f="$1" key="$2" line v="" n=0
  [ -r "$f" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    if [ "$n" = 1 ]; then
      # No opening fence on line 1 means the file has no frontmatter.
      [ "$line" = "---" ] || return 0
      continue
    fi
    # The value is printed ONLY once the closing fence is seen. An unterminated
    # fence is not frontmatter, it is a broken file, and a broken file must fall
    # through to "no field" -- which means inject.
    if [ "$line" = "---" ]; then
      printf '%s' "$v"
      return 0
    fi
    [ "$n" -gt 25 ] && return 0
    case "$line" in
      "$key":*)
        v="${line#"$key":}"
        v="${v#"${v%%[![:space:]]*}"}"   # ltrim
        v="${v%"${v##*[![:space:]]}"}"   # rtrim
        v="${v%\"}"; v="${v#\"}"
        v="${v%\'}"; v="${v#\'}"
        ;;
    esac
  done < "$f"
  return 0
}

# Rule text with the frontmatter block removed -- it is metadata for this hook,
# not instruction for the model.
_rule_body() {
  local f="$1" s rest
  # $(<file) is a bash redirection, not a program. cat used to be here, and it
  # reintroduced the exact failure the comment at the top of this file forbids:
  # a broken PATH made every rule body empty while the block still looked
  # structurally valid, so the empty-block fail-safe below never fired and a
  # session came up with its security rules silently blank.
  s="$(<"$f")" || return 1
  [ -n "$s" ] || return 1
  case "$s" in
    "---"$'\n'*)
      rest="${s#---$'\n'}"
      case "$rest" in
        *$'\n'"---"$'\n'*) s="${rest#*$'\n'---$'\n'}" ;;
        *$'\n'"---") s="" ;;
      esac
      ;;
  esac
  printf '%s' "$s"
}

# Belt and braces. `gate: never` in the rule file is the source of truth; this
# floor exists so that a frontmatter typo, a bad merge, or a rule file that
# fails to read cannot strip a safety rule from a session. A rule added later
# is protected by its own frontmatter, not by this list -- so this list going
# stale costs nothing.
# True only for an agent_type we RECOGNISE as a subagent. Deliberately a
# denylist, not an allowlist of "main": the harness's value for a top-level
# session is not pinned, so keying on it would strip rules from every real
# session the moment that string changes. An unrecognised value gets the full
# block -- too much, which costs tokens, rather than too little, which costs
# behaviour.
_is_subagent_type() {
  case "$1" in
    unknown|"") return 1 ;;
    subagent|task|agent|general-purpose|Explore|Plan|statusline-setup) return 0 ;;
    *:*) return 0 ;;   # plugin-scoped agent types, e.g. team-lead-fleet:writing-editor
  esac
  return 1
}

_is_floor_rule() {
  case "$1" in
    security-posture.md|public-content-scrubbing.md|email-channel-capability-firewall.md) return 0 ;;
  esac
  return 1
}

# True when this project already loads a byte-identical copy of the rule as a
# project instruction.
_dup_project_instruction() {
  local fleet="$1" name="$2" local_rule a b
  local_rule="${HOOK_CWD}/.claude/rules/${name}"
  [ -f "$local_rule" ] || return 1
  a="$(<"$fleet")" || return 1
  b="$(<"$local_rule")" || return 1
  [ -n "$a" ] && [ -n "$b" ] || return 1
  [ "$a" = "$b" ]
}

# Build the concatenated rules block with real newlines (NOT bash literal
# "\n", which is two chars: backslash + n). printf -v + %s\n is the safest
# way to assemble multi-line content in bash without surprises.
#
# `full_content` is assembled alongside as the fail-safe: if the gated block
# comes out empty for any reason, the ungated one is injected instead. Injecting
# too much is a cost; injecting nothing is an outage.
rules_content=""
full_content=""
skipped_agent=""
skipped_dup=""
for rule in "$RULES_DIR"/*.md; do
  [ -f "$rule" ] || continue
  rule_name="${rule##*/}"
  printf -v header '\n\n<!-- team-lead-fleet rule: %s -->\n' "$rule_name"
  if ! body="$(_rule_body "$rule")"; then
    # Unreadable or empty. No fallback can recover the text, so make it LOUD:
    # a blank rule that looks present is worse than one announced as missing.
    body=$'\n**RULE FILE UNREADABLE — this session is missing this fleet rule. Tell the user.**\n'
  fi
  full_content+="${header}${body}"

  gate="$(_fm_field "$rule" gate)"
  applies="$(_fm_field "$rule" appliesTo)"

  if [ "$gate" = "never" ] || _is_floor_rule "$rule_name"; then
    rules_content+="${header}${body}"
    continue
  fi

  if [ "$applies" = "main" ] && _is_subagent_type "$AGENT_TYPE"; then
    skipped_agent+=" ${rule_name}"
    continue
  fi

  if _dup_project_instruction "$rule" "$rule_name"; then
    skipped_dup+=" ${rule_name}"
    continue
  fi

  rules_content+="${header}${body}"
done

# Fail safe: an empty gated block with a non-empty ungated one means the gating
# went wrong. Ship everything rather than nothing.
if [ -z "$rules_content" ] && [ -n "$full_content" ]; then
  rules_content="$full_content"
  skipped_agent=""
  skipped_dup=""
fi

# Wrap with a container so it's visually distinct in the transcript.
# $'...' ANSI-C quoting gives real newlines.
session_context=$'<team-lead-fleet-rules>\n\nThe following rules apply to every peer in the user\'s Claude Code fleet. They are loaded on session start by the team-lead-fleet plugin and behave as alwaysApply rules. Treat them as standing instructions.\n'
if [ "$HOOK_SOURCE" = "resume" ]; then
  session_context+=$'\n**This copy is current and supersedes any earlier <team-lead-fleet-rules> block in this conversation.** This session was resumed, so its restored history still contains the rules as they were when it first started. Where the two disagree, THIS block wins; the earlier one is stale text, not a second policy.\n'
fi
# Say what was left out and why. A rule that goes missing without a trace is the
# failure mode this fleet has already paid for once; a short line here makes a
# short block explainable instead of alarming.
if [ -n "$skipped_dup" ]; then
  session_context+=$'\nAlready in your context verbatim as project instructions, so not repeated here:'"${skipped_dup}"$'\n'
fi
if [ -n "$skipped_agent" ]; then
  session_context+=$'\nScoped to top-level sessions and omitted for this agent:'"${skipped_agent}"$'\n'
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
