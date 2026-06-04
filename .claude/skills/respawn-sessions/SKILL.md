---
name: respawn-sessions
description: Respawn Bryan's long-running Claude Code sessions in detached tmux sessions. Three modes — `missing` (default, fill gaps), `plugin` (kill+respawn anyone lacking the live-feedback plugin), `all` (kill+respawn everyone). Auto-accepts dev-channel permission dialogs and sweeps orphan claude-hive servers.
---

# Respawn Sessions

Re-open Bryan's long-running Claude Code sessions as detached tmux sessions. One command for any of these scenarios:

- After a Mac reboot — fill gaps in the running set.
- After a plugin/marketplace update — push every session onto the new plugin set.
- "Respawn everything" — full fleet reset.

Attach to any session with `tmux a -t <session>` when you want a pane visible.

## Modes

| Mode | What it does |
|------|--------------|
| `missing` (default) | For each `respawn: true` project: spawn a fresh session ONLY if no claude is currently running with that project's `path` as cwd. Skips everything that's already up. **Safe.** |
| `plugin` | Kill+respawn any running session whose argv doesn't include the canonical fleet plugin (`plugin:live-feedback@claude-live-feedback`). Use after enabling/upgrading a plugin globally. **Team Lead (self) is never killed**; if it lacks the plugin, the script flags it for manual restart. |
| `all` | Kill+respawn every `respawn: true` session. Use as a full fleet reset. **Team Lead (self) is never killed.** |

## Per-peer DISCORD_STATE_DIR scoping (automatic)

Each spawned tmux session gets `DISCORD_STATE_DIR` set explicitly via `tmux new-session -e DISCORD_STATE_DIR=…`, so the peer doesn't inherit the team-lead's discord state. Two destinations:

- **Peer has its own `.claude/discord/access.json`** (e.g., octoturtle with a family-bot) → that peer's local state dir is used.
- **Peer doesn't** → a shared `~/.config/team-lead/no-discord/` state is used. The dir contains an empty allowlist and no `.env`, so the discord plugin loads but doesn't connect (no token) and the peer isn't subscribed to any channel.

Why this matters: without the per-session override, every peer inherits the team-lead's `DISCORD_STATE_DIR` from the team-lead's shell env (set by direnv). All peers then share the team-lead's bot + access.json + channel subscriptions. A single Discord post to the team-lead's channel fans out to every peer. The `-e` flag is critical — `new-session` ignores client env if the tmux server is already running, so just `export`ing the var in the parent shell doesn't help.

Encoded in `respawn.py`'s `discord_state_dir_for(path)` + `spawn_session_tmux`.

## What `--execute` actually does

Beyond spawning the tmux sessions, when something gets spawned the script also:

1. **Polls each new tmux pane for known startup dialogs** and sends Enter to dismiss them — covers the dev-channel approval dialog (`--dangerously-load-development-channels`), the MCP-server approval dialog, and the resume-from-summary auto-compact dialog. Polls every 3s for up to ~75s; multiple Enters per pane are sent if dialogs chain.
2. **Sweeps orphan `claude-hive-mcp/server.ts` processes** (re-parented to PID 1 / launchd) left behind by killed claudes. Without this, new claude sessions can't register with claude-hive cleanly — the hive registry would still show the old PIDs.

Pass `--no-auto-accept` to skip both post-spawn steps and walk through the panes by hand.

## Safety: dry-run by default

The script is **dry-run by default**. A bare invocation prints what it would do (which projects are already running, which would be killed, which would be spawned) and exits without touching anything. To actually execute you must pass `--execute`.

```bash
# Safe preview:
python3 respawn.py
python3 respawn.py --mode plugin
python3 respawn.py --mode all

# Actually do it:
python3 respawn.py --execute                   # missing (default)
python3 respawn.py --mode plugin --execute     # upgrade plugin-less sessions
python3 respawn.py --mode all --execute        # full fleet reset

# Skip post-spawn dialog acceptance + cleanup (rare — usually you want it):
python3 respawn.py --execute --no-auto-accept

# Help:
python3 respawn.py --help
```

## How to invoke

### Manually (from a team-lead session)

```bash
python3 /Users/bryanchan/dev/ai-team-lead/.claude/skills/respawn-sessions/respawn.py --mode all --execute
```

### Via the skill

If you have this skill loaded, say "respawn my sessions" or "respawn all agents." Team Lead will pick the right mode based on intent — `all` for "respawn all," `plugin` for "the plugin isn't loaded everywhere," `missing` for default fill-gap.

### Attaching to a session

```bash
tmux a -t <session-name>      # e.g. tmux a -t my-project
tmux ls                        # list all live tmux sessions
```

Session names are derived from the registry's `session_name` field, lowercased with spaces replaced by hyphens (e.g., `"My Project"` → `my-project`).

## Team Lead self-protection

The script walks the parent process chain to identify the team-lead's own claude PID and **never kills it**, in any mode. If `--mode plugin` finds the team-lead lacks the plugin, it prints a flag asking the user to restart the team-lead session manually (you can't replace yourself in a process you're running inside).

If the parent-walk fails to find a claude PID (`get_self_pid()` returns None), `--mode all` and `--mode plugin` abort with a non-zero exit code rather than risk killing the team-lead.

## How to add or remove a project from the respawn list

Edit `registry.yaml` and add (or remove) two fields on the project's entry:

```yaml
projects:
  example-project:
    type: personal
    path: ~/dev/example-project
    repo: fryanpan/example-project
    respawn: true              # ← include in respawn
    session_name: "Example"    # ← tmux session name (optional; defaults to humanized key)
    docs:
      ...
```

`respawn: true` is required. `session_name` is optional — if missing, the script uses a Title-Cased version of the registry key. `repo` is **optional**: omit it for a local-only folder (no git repo — e.g. a synced Google Drive subfolder). respawn.py never runs git regardless, so a local-only project respawns exactly like a repo-backed one; only `path` + `respawn: true` are needed.

No need to redeploy the script — the next respawn pass picks up the new registry contents automatically.

## How it parses registry.yaml

Minimal regex parser (~30 lines), no PyYAML dependency. Handles the registry's specific structure (2-space project keys, 4-space scalar fields). Ignores nested blocks like `docs:`, `linear:`, `notion:`.

## Why `zsh -ic` (and not bare `claude`)

Bryan's `claude` is a zsh **function** (defined in `~/.zshrc`), not an alias. The function injects the canonical channel + dev-plugin flags before forwarding to `/Users/bryanchan/.local/bin/claude`. Bare `claude` from a non-interactive shell skips the function entirely.

The script invokes `tmux new-session -d -s <name> -c <path> /bin/zsh -ic "claude --continue"` so the interactive zsh sources `~/.zshrc` and the function applies. Don't try to inline the channel flags in `claude_invocation` — the function will append its own copy and you'll see duplicates in argv.

## Bootstrapping requirements

For each project to actually have a session to resume:

- The project's `path` must exist as a directory.
- The project must have at least one prior Claude Code conversation in that directory (so `claude --continue` finds something to resume). For brand-new projects, the script falls back to plain `claude` (per `feedback_first_time_spawn.md`).

The script logs `[skip]` for any registry entry whose `path` doesn't exist on disk and continues.

## Failure modes and how to debug

- **`claude` command not found in pane:** the inline `zsh -ic` should source `~/.zshrc`. If your `claude` shell function isn't there, or `~/.local/bin` isn't on `PATH`, fix the rc file. Verify with `tmux capture-pane -t <session> -p` after spawn.
- **Dialog wasn't dismissed:** the auto-accept polls the pane via `tmux capture-pane -p` and sends `Enter` when it sees one of `DIALOG_PATTERNS` (`Resume from summary`, `I am using this for local development`, `Use this and all future MCP servers`, `Enter to confirm`). If a new dialog wording doesn't match, add it to the pattern list. As a workaround: `tmux send-keys -t <session> Enter` manually.
- **Wrong session resumed:** `claude --continue` resumes the most recent conversation in the cwd. If you accidentally started a fresh session in that cwd at any point, it's now the "most recent." Fix: attach with `tmux a -t <name>`, run `/resume` in-session, pick the right one, exit, and the next respawn will grab it.
- **tmux session name collision:** `spawn_session_tmux` calls `tmux kill-session -t <name>` before creating, so the spawn is idempotent. If you see "session not found" warnings on first run, that's normal — the kill is best-effort.

## Common scenarios

### "I just enabled a new plugin globally and want every agent to pick it up"

```bash
python3 respawn.py --mode plugin --execute
```

If team-lead itself lacks the plugin, the script flags it. Restart team-lead with `/exit` then `claude --continue`.

### "I rebooted my Mac and want my fleet back"

```bash
python3 respawn.py --execute
```

(`missing` mode — only spawns gaps, doesn't disturb anything that's already up.)

### "Something's weird — full reset"

```bash
python3 respawn.py --mode all --execute
```

Kills + respawns everything. Team Lead is preserved; restart manually if needed.
