# Project: AI Team Lead (Team Lead + Metaproject)

## Killer item — a tmux pane is a render, not state

**Never claim a session is blocked, waiting, stuck, holding a message, or BUSY / mid-task based on `tmux capture-pane`.** The pane shows pixels; it cannot show what a session received. Check its transcript first — `~/.claude/projects/<cwd with / _ . replaced by ->/*.jsonl` — and find the last turn it actually processed. If it processed anything after the supposed blocker appeared, it was never blocked. **"esc to interrupt" in the footer is a render like everything else** — reading it as "busy" made a peer get skipped as mid-task while it sat idle.

**Text on the `❯` line is not the editor — it is usually a GHOST over an empty one.** Not a draft, not pending, not blocking, and frequently not *there*. A box rendering `Cancelled Fantastic` had an empty editor; `C-u`, `C-e`+`C-u` and `C-a`+`C-k` all did nothing because there was nothing to delete. **The only way to know is to type one sentinel character and read back whether it replaced or appended.** Until you do, say "the pane renders X", never "the user has unsent text."

**Never bake pane-inference into a script.** A wrong reading in conversation costs one turn; the same reading compiled into a tool keeps making the error forever.

**Driving a pane is riskier than reading one.** Typing `/` opens the command palette, and a stray Down+Enter fires whatever is highlighted.

This is the most repeated correction in this project — it has produced a fabricated quote encoded into CRM records, a fabricated five-day fleet blocker escalated across three daily reviews, six non-existent "unsent messages" reported to the user as his own words, and an idle peer skipped as busy three times in one hour. Same family as trusting the process table for MCP health. **An external surface is not state. Read the transcript.**

## Overview

Cross-project team-lead and management toolkit. Two roles: **Team Lead** handles DMs, routes work, tracks tasks across managed products, and coordinates the agent fleet. **Metaproject** reviews and scaffolds other projects, reading from their main worktrees and proposing changes via GitHub PRs.

`registry.yaml` maps each managed project to its local path, GitHub repo, and Linear team.

## The rules that fire

- **Before reading from a project**, `git -C <path> pull --ff-only`. If it fails, investigate before reading.
- **A registry entry with no `repo` field is a plain local folder, not a git repo.** Run no git commands at all against it — no pull, no status, no PR flow. Read and edit directly.
- **Never edit files in other project repos.** Propose via `gh pr create --repo <repo>`, or delegate to the owning agent. The GitHub MCP is unreliable for private and new repos — use `gh`.
- **Always read from the main worktree** at `~/dev/{project}`. Feature-branch worktrees may hold in-progress work.
- **`ai-team-lead` is PUBLIC.** No project names in commit messages, PR descriptions, or code comments — use `registry: add new project`, never the name. PR descriptions cover what changed in *this* repo only.
- **When creating or editing a skill**, follow `superpowers:writing-skills` plus `plugin/team-lead-fleet/rules/skill-authoring.md`.

## Agent lifecycle — lean fleet

A peer session runs only when it has live work: this week's committed goals, an always-up agent, or a task the user just handed it. Idle agents cost cache-read tokens every turn.

**Task-driven:** bring the one session up (`/respawn-sessions --only <name>`), hand off the goal and let it own the loop, then spin it down (`/shutdown-session`) when the task is done — checkpoint first if it is mid-flight. Track the pending shutdown.

**Leave running:** always-up agents (`project_always_up_agents` memory) and peers on this week's committed goals. Their lifecycle belongs to `/weekly-plan`, not to ad-hoc cleanup.

## Layout

| Path | Holds |
| --- | --- |
| `plugin/team-lead-fleet/` | Canonical fleet-wide skills + alwaysApply rules. Every peer enables this plugin. |
| `.claude/skills/`, `.claude/rules/` | Team-Lead's own. Fleet-shared entries are **symlinks** into the plugin — single source of truth. `claude-hive-peer.md` is peer-only and deliberately not symlinked here. |
| `docs/process/` | This project's learnings + retros |

Gitignored and symlinked into worktrees by `./scripts/setup-private.sh`: `registry.yaml`, `docs/process/retrospective.md`, `docs/process/propagation-log.md`, `docs/process/aggregation-log.md`. One-time after a fresh clone: `git config core.hooksPath .githooks`.

## Read on demand — NOT loaded into context

- **`docs/process/learnings.md`** — searchable archive of past failures. Grep it when you are about to debug, deploy, or trust a surface: an MCP server looks broken, a plugin update seems not to have landed, a check reports clean, a launchd job fails, a peer looks blocked, you are about to delete or overwrite something. Anything that must fire *without* a lookup belongs in a rule or the killer item above, not in here.
- **`docs/process/fleet-ops.md`** — the health checker, the `/opt/fleet` deploy root and the launchd sandbox rules, and the pre-push leak gate. Read it before editing any of those.
