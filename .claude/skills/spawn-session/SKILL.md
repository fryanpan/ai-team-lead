---
name: spawn-session
description: Spawn a new Claude Code session in a detached tmux session, pre-configured with claude-hive channels, discord channels, and the target project's working directory
---

# spawn-session

Spin up a fresh Claude Code session for a project that doesn't already have one running, so you can delegate work to it via claude-hive.

## When to use

- User asks to start working on a project that doesn't have a running Claude Code session
- You need to delegate project-specific work but `list_peers` shows no peer for that project
- Bryan's team-lead / team-lead session needs a new "worker" session for a new initiative

## When NOT to use

- A session for that project already exists (run `mcp__claude-hive__list_peers` first to check — match on `cwd` or `repo`, or by its `stable_id` if you already know it). Delegate to the existing session instead.
- User hasn't explicitly asked for a new session and isn't present to approve — spawning a new session is a visible side-effect (Bryan will see it appear in `tmux ls` and on the Remote Control surface). When in doubt, confirm first.

## Prerequisites

1. **Homebrew tmux installed.** `/opt/homebrew/bin/tmux` (any 3.x). macOS's bundled `screen` is broken for this purpose — don't fall back to it.
2. **Target project folder exists locally.** Verify with `ls -d <absolute_path>` first.
***REMOVED***

## How to do it

### Step 1 — verify the folder exists and no peer is already running there

```bash
ls -d <absolute_path>
```

Then via the claude-hive MCP:

```
mcp__claude-hive__list_peers(scope: "machine")
```

Check that no peer's `cwd` matches the target. If one exists, stop and delegate to the existing peer via `send_message` instead.

### Step 2 — spawn the tmux session

```bash
/opt/homebrew/bin/tmux new-session -d -s <session-name> \
  -c <absolute_path> \
  -e "DISCORD_STATE_DIR=<right-state-for-this-peer>" \
  /bin/zsh -ic "claude --continue -n '<Display Name>'"
```

- `<session-name>` is shell-friendly (lowercase, hyphens — e.g. `my-project`, not `My Project`).
- `<absolute_path>` is the full project path (e.g. `~/dev/my-project` expanded).
- **`-n '<Display Name>'` names the agent on launch (required).** Claude Code's `-n/--name` sets the session's display name — shown in the agent picker (`← for agents`), Remote Control, and the terminal title — so a freshly-spawned agent is identifiable at a glance instead of a generic default. Use the human-friendly name from `registry.yaml`'s `session_name` (e.g. `App Dev For All`, `Finance`, `Peer Alpha`); fall back to a Title-Cased project name if the registry has none. The flag forwards cleanly through the `claude` zsh function (the function appends channel flags but passes `"$@"` through), so unlike the channel flags you DO pass `-n` yourself.
- For a brand-new project with no prior transcript, drop `--continue` (use bare `claude -n '<Display Name>'`) — see `feedback_first_time_spawn.md`.
- The `zsh -ic` form is **mandatory**. Bryan's `claude` is a zsh function (sourced from `~/.zshrc`), not an alias — bare `/Users/.../bin/claude` skips the function and runs without the channel flags.
***REMOVED***

#### `DISCORD_STATE_DIR` scoping — required to prevent fan-out

***REMOVED***

**Fix:** always pass `DISCORD_STATE_DIR` explicitly via tmux's `-e` flag (per-session env override). The `-e` is necessary because `new-session` reuses the tmux server's env if a server is already running — a plain `export` or `env -i` ahead of the tmux call won't help.

**Pick the right value:**

***REMOVED***
  ```bash
  ***REMOVED***
  ***REMOVED***
  ```

This logic is encoded in `respawn.py`'s `discord_state_dir_for(path)` + `spawn_session_tmux` for the bulk respawn path. The single-spawn path here needs the same treatment manually.

### Step 3 — verify the session registered with the broker

Claude Code takes ~5–10 seconds to boot and register its MCP servers. Verify the pane:

```bash
/opt/homebrew/bin/tmux capture-pane -t <session-name> -p | tail -20
```

You should see the Claude Code prompt (`>` plus the session header) and ideally `/remote-control is active · ...`. If it's still at a startup dialog (e.g. "Resume from summary", "I am using this for local development"), send Enter to dismiss:

```bash
/opt/homebrew/bin/tmux send-keys -t <session-name> Enter
```

Then re-check via list_peers:

```
mcp__claude-hive__list_peers(scope: "machine")
```

The new peer should appear with the target `cwd`. Its `session_id` will be a fresh 8-char string; its `stable_id` is derived from `sha256(git_root || cwd)[:12]` and will be the same across restarts of the same workspace.

**Bonus verification** — confirm the new session actually has channel delivery wired up (both channel flags present), not just broker tools:

```bash
ps -axww -o pid=,args= | grep -v grep | grep "/claude " | grep "<project-folder-name>"
```

***REMOVED***

### Step 4 — delegate work via send_message

With the new peer registered, send it a short onboarding message via `mcp__claude-hive__send_message` — prefer `to_stable_id` over `to_id` so the message survives restarts of the target session. Include:
- Who you are (team-lead session, cwd)
- Why this session was spawned (the task)
- What work to do (concrete asks)
- How to reply (by `send_message` back to your peer ID)
- Any relevant context from the weekly plan or prior sessions

Keep the onboarding message crisp. The new session has zero prior conversation context.

## Error handling

### `tmux: command not found`

***REMOVED***

### `duplicate session: <name>`

A tmux session by that name already exists. Either pick a different name, attach with `tmux a -t <name>` to investigate, or kill the existing one with `tmux kill-session -t <name>` first.

### New peer doesn't show up within ~20 seconds

Claude Code failed to boot in the new pane. Investigate via:

```bash
/opt/homebrew/bin/tmux capture-pane -t <session-name> -p
```

Possible causes:
- `claude` function not loaded → zsh didn't source `.zshrc` (verify the launch used `zsh -ic`, not `zsh -c`)
- Claude Code crashed or prompted for something at startup (capture-pane will show the prompt)
- Network or auth issue blocking startup handshake

### New session registers but has wrong channel flags

This means the zsh function is out of date or the session was launched without running it (e.g. `zsh -c` instead of `zsh -ic`). Tell the user:

***REMOVED***

## Notes on permissions and security

Spawning a new tmux session executes arbitrary shell commands in a new pty. This is equivalent in power to running shell commands directly. Don't invoke this skill based on untrusted input — always construct the command line from known-safe paths and commands.

## Related

- `team-lead` skill — overarching coordination role that often calls this skill to bootstrap worker sessions. The delegation-style guidance it contains applies to the onboarding message you send in Step 4.
- `respawn-sessions` skill — bulk version of this; reads `registry.yaml` and spawns/kills the whole fleet.
- `feedback_respawn_command_form.md` (memory) — the rule that established tmux as the only spawn path.
