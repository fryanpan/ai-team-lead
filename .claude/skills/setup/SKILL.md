---
name: setup
description: First-time setup for a fresh clone of ai-team-lead. Enables git hooks (leak gate + worktree symlinks), seeds the project registry, verifies dependencies, installs + registers claude-hive (required for inter-agent messaging), and walks through optional channels (Discord, Notion watching, GitHub events). Run this once after cloning the repo, then ask Claude to start using the team-lead skills.
user-invocable: true
---

# Setup

First-time setup for a fresh clone of `ai-team-lead`. Walk the user through it in order. Pause for confirmation between sections; skip optional sections if they say no.

## What this skill does

Brings a fresh checkout of this repo to a working state:

1. Enables the project's git hooks (pre-push leak gate + setup-private on worktree creation)
2. Seeds `registry.yaml` from the example template
3. Verifies + installs OS-level dependencies (`tmux`, `gh`, `uv`, `bun`)
4. Installs Python dependencies via `uv`
5. **Installs + registers claude-hive — required.** The inter-agent messaging the team-lead workflow depends on.
6. (Optional) Discord channel access for the team-lead session
7. (Optional) Notion canonical-parent subscription for `/weekly-plan`
8. (Optional) GitHub events channel (CI / PR / review / merge / deploy notifications)

## Steps

### 1. Confirm scope with the user

Ask once:

> "I'll run first-time setup for this repo. Plan: enable git hooks, seed `registry.yaml` from the template (you'll fill in your project list), verify + install dependencies (`tmux`, `gh`, `uv`, `bun`, plus Python deps), install + register **claude-hive** (required — it's the inter-agent messaging), and optionally walk you through Discord, Notion-watching, and GitHub-events channels. Anything you want to skip up front?"

Wait for their answer. If they say "just do it," proceed; if they want to skip the *optional* parts, honor that. Hive is required — don't skip it.

### 2. Enable the git hooks

```sh
git config core.hooksPath .githooks
```

Explain: this enables `.githooks/post-checkout` (auto-symlinks gitignored private files when you create a worktree) and `.githooks/pre-push` (scans the diff being pushed for project-name leaks; blocks the push if it finds any). One-time, repo-local.

Verify: `git config --get core.hooksPath` should return `.githooks`.

### 3. Seed `registry.yaml`

```sh
cp registry.yaml.example registry.yaml
```

Then read `registry.yaml.example` in chat with the user so they understand the schema (project entries with `path`, `repo`, `ship_method`, `respawn`, `session_name`, optional `docs:` / `linear:` / `notion:` blocks). Note that `repo` is **optional** — omit it for a local-only folder (no git repo, e.g. a synced cloud-drive subfolder); the tooling skips all git for those.

Ask: "Want to add your projects now, or skip and add them later via `/add-project`?" If they want to add now, walk them through 1-2 entries to demonstrate, then let them edit the file directly.

`registry.yaml` is gitignored — it never leaves their machine.

### 4. Verify OS-level dependencies

Check each in parallel:

```sh
which tmux  # for /spawn-session, /respawn-sessions
which gh    # for cross-project GitHub work + the GitHub-events channel
which uv    # for Python deps
which bun   # for claude-hive (the broker + per-session MCP servers run on bun)
```

For any missing, suggest the install command (macOS Homebrew defaults):

- `brew install tmux`
- `brew install gh` (then `gh auth login`)
- `brew install uv`
- `brew install bun` (or `curl -fsSL https://bun.sh/install | bash`)

Don't run the installs yourself — let the user run them so they consent to Homebrew side-effects.

### 5. Install Python dependencies

```sh
uv sync
```

This reads `pyproject.toml` + `uv.lock` and produces `.venv/`. Confirm `.venv/` exists after.

### 6. Install + register claude-hive (REQUIRED)

The team-lead coordinates peer sessions via **claude-hive** — persistent inter-agent messaging (`list_peers`, `send_message`, `set_summary`, …). It's an external dependency (not vendored in this repo), so it must be installed + registered. Requires **Bun** (step 4) and **Claude Code v2.1.80+**.

Install:

```sh
git clone https://github.com/KevinLyxz/claude-hive-mcp.git ~/claude-hive-mcp
cd ~/claude-hive-mcp && bun install
```

Register the MCP server (user scope — available in every session):

```sh
claude mcp add --scope user --transport stdio claude-hive -- bun ~/claude-hive-mcp/server.ts
```

Wire the channel so messages **push instantly** (without it you'd fall back to manual `check_messages`). Add an alias:

```sh
alias claudehive='claude --dangerously-load-development-channels server:claude-hive'
```

> If you launch peers via this repo's tmux respawn flow (`/respawn-sessions`), the channel flag is injected by the `claude` shell function in `~/.zshrc` instead — see the `respawn-sessions` skill. Either way, the flag is what enables instant delivery.

**Multi-user on a shared machine — important.** The broker binds a fixed localhost port (default **7900**) and a per-`$HOME` SQLite DB (`~/.claude-hive.db`). The DB is already per-user (separate homes), but **the port is machine-wide** — so a *second* OS user on the same machine MUST set a unique `CLAUDE_HIVE_PORT` (e.g. `7901`) in their environment, or their sessions will silently join the first user's hive (cross-user message bleed). Single-user machines leave the defaults. Override via:

```sh
export CLAUDE_HIVE_PORT=7901    # unique per OS user on a shared machine
# CLAUDE_HIVE_DB defaults to ~/.claude-hive.db (already per-user)
```

Verify: `bun ~/claude-hive-mcp/cli.ts status` should print the broker + peer count. Or open two sessions (with the channel flag) and ask one: "list all peers on this machine."

### 7. (Optional) Discord channel access

Ask: "Do you want the team-lead session to receive Discord messages? If yes, I can walk you through creating a bot + configuring access. If no, the team-lead runs without Discord — that's fine."

If yes, invoke the `discord:configure` skill (from the discord plugin) and let it handle bot-token + allowlist setup. The output lives at `.claude/discord/` (gitignored, mode 600).

If no, skip — the standard "no-discord" shared state at `~/.config/team-lead/no-discord/` is used automatically for peer sessions.

### 8. (Optional) Notion canonical-parent subscription

Ask: "Do you want the team-lead to listen for comments on a Notion 'Weekly Plans' parent page? Useful if you'll be using `/weekly-plan`."

If yes:

```
notion_watch_page(page_id="<parent-page-id>", include_descendants=true)
```

Stash the parent URL at `.claude/skills/weekly-plan/parent.txt` (gitignored) so the `/weekly-plan` skill can find it next run.

If no, skip — the user can configure later.

### 9. (Optional) GitHub events channel

Delivers GitHub events — CI pass/fail, PR reviews, merges, deploys — as live channel notifications in the running session, using your existing `gh` credentials. No webhooks, no public URL.

Ask: "Do you want GitHub events (CI / PR / review / merge / deploy) to arrive as live notifications in the team-lead session?"

If yes:

```sh
claude plugin install github:fryanpan/github-claude-channel
```

It uses the `gh` auth from step 4. See the plugin's README for which-repo configuration. Like hive, it's a channel — it pushes events into the session as they happen.

If no, skip.

### 10. Confirm done + suggest next step

Recap in one line: "Setup complete. `<count>` projects registered, hooks enabled, deps installed, claude-hive registered`<, plus Discord/Notion/GitHub-events if enabled>`."

Then suggest the natural next move based on what they enabled:
- Has projects + Notion: "Try `/weekly-plan` to set this week's goals."
- Has projects but no Notion: "Try `/daily-review` to see fleet status."
- No projects yet: "Use `/add-project` to register an existing repo, or `/new-project` to scaffold a fresh one."

## What to avoid

- Don't run dependency installs (brew, uv) silently — let the user consent.
- **Don't skip claude-hive** — it's required; the team-lead's coordination (peer pings, daily-review, delegation) depends on it.
- On a **shared machine**, don't forget the per-user `CLAUDE_HIVE_PORT` — without it, a second OS user's agents join the first user's hive.
- Don't ask them to fill in `registry.yaml` field-by-field; show them the template, let them edit.
- Don't enable Discord without explicit yes — bot tokens are sensitive.
- Don't commit `registry.yaml` or any `.claude/discord/` content — they're gitignored for a reason.
- Don't repeat this skill on subsequent runs. If `registry.yaml` already exists, `core.hooksPath` is already `.githooks`, and `claude mcp list` shows `claude-hive`, tell the user "Already set up — nothing to do" and exit.
