#!/bin/bash
# Autonomous team-lead self-respawn (macOS-safe).
#
# Problem: the team-lead runs as a bare `claude --continue` process (not in tmux),
# so it can't restart itself — a dying process can't revive itself, and a detached
# respawn needs a pty. So: arm a DETACHED job that outlives us, then kill self. The
# job waits for the old process to exit, then spawns a fresh team-lead into a tmux
# session on the current binary + plugin set, resuming this conversation via
# --continue.
#
# POSTMORTEM (2026-07-11) — why this script is shaped the way it is. DO NOT
# "simplify" either invariant away:
#
#   The original used `setsid`, which DOES NOT EXIST ON macOS. The detach failed with
#   "setsid: command not found" — and the script fell straight through to
#   `kill $SELF_PID` anyway. Net: the team-lead killed itself with no revival path
#   armed and stayed down ~16h until a human noticed. Hence:
#
#     1. DETACH VIA python3 os.setsid(). macOS has no setsid(1). We need a real new
#        session so the job escapes the dying team-lead's process group. nohup+&+
#        disown alone is NOT sufficient — that only ignores SIGHUP, it does not
#        escape the process group.
#     2. NEVER KILL SELF UNTIL THE JOB PROVES IT ARMED. The detached job's first act
#        is to touch a sentinel file; we block on that sentinel and ABORT (leaving
#        the team-lead alive) if it never appears. A failed detach must cost nothing.
#
#   The general rule this encodes: a script that kills its own caller must prove the
#   revival path is live BEFORE it pulls the trigger.
#
# Usage:  self-respawn.sh <team-lead-claude-pid>
# The caller passes its own claude PID, discovered via the parent-process walk (the
# top-level …/bin/claude ancestor, NOT the hive-mcp child). No PID is hard-coded.
#
# Result: ~10-20s with no team-lead, then it's back in tmux session `team-lead`,
# same conversation (--continue), named "Team Lead". Reconnect with
# `tmux a -t team-lead` or via Remote Control (it re-registers on the hive + RC).

set -u

SELF_PID="${1:?usage: self-respawn.sh <team-lead-claude-pid>}"
TL_DIR="${TL_DIR:-/Volumes/Data/Users/bryanchan/dev/ai-team-lead}"
TL_SESSION="${TL_SESSION:-team-lead}"
TL_NAME="${TL_NAME:-Team Lead}"
TMUX_BIN="${TMUX_BIN:-/opt/homebrew/bin/tmux}"
# 1 = answer the resume dialog with "Resume full session as-is" (keep full context).
# 0 = take the default "Resume from summary" (compacts on resume).
TL_NO_COMPACT="${TL_NO_COMPACT:-0}"
LOG=/tmp/tl-selfrespawn.log
ARMED="/tmp/tl-selfrespawn.armed.$$"

# ---- pre-flight: every failure here happens BEFORE we touch self ----
[ -x "$TMUX_BIN" ] || { echo "ABORT: tmux not executable at $TMUX_BIN — not killing self."; exit 1; }
[ -d "$TL_DIR" ]   || { echo "ABORT: TL_DIR does not exist: $TL_DIR — not killing self."; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ABORT: python3 required for the detach — not killing self."; exit 1; }
kill -0 "$SELF_PID" 2>/dev/null || { echo "ABORT: pid $SELF_PID is not alive (wrong PID?) — not killing anything."; exit 1; }

rm -f "$ARMED"

# ---- the detached respawn job ----
# Unquoted heredoc: $VARS expand NOW; \$… stays literal for the job's runtime.
# -e CW_AGENT_NAME: without it claude-workspaces gives this session no stable agent
# identity, so (a) every comment it posts is attributed to "Agent" and (b)
# set_workspace_lead returns subscriptionPersisted:false — the board subscription
# silently does NOT survive the respawn, so events stop arriving with no error.
# Must be -e: new-session ignores client env when the tmux server is already up.
# No -e DISCORD_STATE_DIR: the team-lead keeps its own shell/direnv discord state
# (unlike peers, which get an explicit override to prevent channel fan-out) —
# `zsh -ic` in TL_DIR restores it.
JOB=$(cat <<EOF
touch "$ARMED"   # FIRST ACT: tells the parent the revival path is live and it may die
echo "[\$(date)] armed (sentinel $ARMED); waiting for old team-lead (pid $SELF_PID) to exit" >> $LOG
for _ in \$(seq 1 60); do
  kill -0 $SELF_PID 2>/dev/null || break
  sleep 1
done
sleep 3
$TMUX_BIN kill-session -t $TL_SESSION 2>/dev/null
$TMUX_BIN new-session -d -s $TL_SESSION -e CW_AGENT_NAME='$TL_NAME' -e FEEDBACK_AGENT_NAME='$TL_NAME' -c '$TL_DIR' /bin/zsh -ic "claude --continue -n '$TL_NAME' --remote-control '$TL_NAME'"
echo "[\$(date)] spawned tmux:$TL_SESSION (rc=\$?)" >> $LOG
# Auto-accept startup dialogs.
# The resume dialog's DEFAULT option is "Resume from summary" — a bare Enter there
# COMPACTS us. Under TL_NO_COMPACT=1 we arrow down one and take "Resume full session
# as-is" instead, so we come back with full context. Every other dialog takes Enter.
for _ in \$(seq 1 25); do
  sleep 3
  pane=\$($TMUX_BIN capture-pane -t $TL_SESSION -p 2>/dev/null)
  if echo "\$pane" | grep -qE 'Resume from summary|Resume full session as-is'; then
    if [ "$TL_NO_COMPACT" = "1" ]; then
      $TMUX_BIN send-keys -t $TL_SESSION Down Enter
    else
      $TMUX_BIN send-keys -t $TL_SESSION Enter
    fi
  elif echo "\$pane" | grep -qE 'local development|Enter to confirm|future MCP'; then
    $TMUX_BIN send-keys -t $TL_SESSION Enter
  fi
done
echo "[\$(date)] self-respawn done" >> $LOG
EOF
)

# INVARIANT 1 — macOS-safe detach. python3's os.setsid() gives a real new session,
# so this job survives the team-lead's death.
nohup python3 -c "
import os, subprocess, sys
os.setsid()
subprocess.run(['/bin/bash', '-c', sys.argv[1]])
" "$JOB" >>"$LOG" 2>&1 &
disown 2>/dev/null || true

# INVARIANT 2 — do not die until the job proves it armed.
for _ in $(seq 1 15); do
  [ -f "$ARMED" ] && break
  sleep 1
done

if [ ! -f "$ARMED" ]; then
  echo "ABORT: respawn job never armed (no sentinel at $ARMED) — NOT killing self."
  echo "       The team-lead stays up; nothing lost. Inspect $LOG for the cause."
  exit 1
fi

rm -f "$ARMED"
echo "respawn job ARMED (log: $LOG); killing self (pid $SELF_PID) in 1s"
sleep 1
kill "$SELF_PID"
