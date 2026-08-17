# Project: AI Team Lead (Team Lead + Metaproject)

## Killer item — a tmux pane is a render, not state

**Never claim a session is blocked, waiting, stuck, holding a message, or BUSY / mid-task based on `tmux capture-pane`.** The pane shows pixels; it cannot show what a session received. Check its transcript first — `~/.claude/projects/<cwd with / _ . replaced by ->/*.jsonl` — and find the last turn it actually processed. If it processed anything after the supposed blocker appeared, it was never blocked. **"esc to interrupt" in the footer is a render like everything else** — reading it as "busy" made a peer get skipped as mid-task while it sat idle with `Still blocked on /mcp reconnect` on its own screen (2026-08-13).

**Text on the `❯` line is not the editor — it is usually a GHOST over an empty one.** Not a draft, not pending, not blocking, and frequently not *there*. Measured 2026-08-13: a box rendering `Cancelled Fantastic` had an empty editor — typing `X` yielded `X`, and one BSpace restored the ghost; `C-u`, `C-e`+`C-u` and `C-a`+`C-k` had all done nothing because there was nothing to delete. **The only way to know is to type one sentinel character and read back whether it replaced or appended.** Until you do, say "the pane renders X", never "the user has unsent text."

**Never bake pane-inference into a script.** A wrong reading in conversation costs one turn; the same reading compiled into a tool keeps making the error automatically, forever. `pane_is_busy()` in `.claude/skills/mcp-reconnect/` was exactly this and had to come out.

**Driving a pane is riskier than reading one.** Typing `/` opens the command palette, and a stray Down+Enter fires whatever is highlighted — that is how `/superpowers:brainstorming` got submitted into a peer's session (2026-08-13).

This is the most repeated correction in this project — it has produced a fabricated quote encoded into CRM records (2026-05-16), a fabricated five-day fleet blocker escalated across three daily reviews (2026-08-03), six non-existent "unsent messages" reported to the user as his own words, and an idle peer skipped as busy three times in one hour (both 2026-08-13). Same family as trusting the process table for MCP health. **An external surface is not state. Read the transcript.**

## Overview

Cross-project team-lead and management toolkit. Two roles in one:

1. **Team Lead** — handles DMs, routes work to the right project, tracks tasks across all managed products, and coordinates the autonomous agent system.
2. **Metaproject** — reviews, improves, and scaffolds other projects. Reads from project repos and proposes changes via GitHub PRs.

## How It Works

### Project Registry
`registry.yaml` maps each managed project to its local path, GitHub repo, and Linear team. The metaproject reads from main worktrees (`~/dev/{project}`) using absolute filesystem paths.

**Before reading from a project**, ensure freshness: `git -C <path> pull --ff-only` to update to latest origin/main. If pull fails (dirty worktree or diverged history), investigate before reading.

**Local-only projects (no `repo`):** a registry entry with no `repo` field is a plain local folder — not a git repo (e.g. a synced Google Drive subfolder, a scratch dir). For these, **run no git commands at all** — no pull/fetch/status, and no PR flow. Read and edit the folder directly. `respawn: true` still works (respawn.py never touches git). The tooling (`respawn.py`, `refresh_team_state.py`) already treats a missing `repo` as "local-only, skip git."

### Cross-Project Changes
Never edit files in other project repos directly. Always propose changes via GitHub PRs using `gh pr create --repo <repo>`. The GitHub MCP plugin is unreliable for private repos and new repos — see `docs/process/learnings.md`.

### Worktree Interaction
Each project has 2–5 active Team Lead worktrees. The metaproject always reads from the main worktree at `~/dev/{project}`. Feature branch worktrees are not read — they may have uncommitted or in-progress work.

### Agent Lifecycle — keep long-running agents up only when needed
Default to a **lean fleet**. A peer session should be running only when it has live work — this week's committed goals, an always-up agent, or a task the user just handed it. Idle agents cost cache-read tokens every turn and add noise; don't keep them up "just in case."

**Task-driven spin-up/spin-down.** When the user asks for a specific task and the owning agent isn't up:
1. **Bring it up** (`/respawn-sessions` mechanics — spawn just that one session; don't wake the whole fleet).
2. **Hand off the goal** and let it own the loop (no status-report pings — see `feedback_dont_wire_in_status_reports`).
3. **Spin it back down when the task is done** — coordinate the shutdown (`/shutdown-session`): if the agent is mid-flight, ask it to checkpoint first; for a deploy-gated task, wait for the single "done/deployed" signal, then kill. Track the pending shutdown so it isn't forgotten.

**Exceptions — leave running:** always-up agents (per `project_always_up_agents` memory) and peers actively on this week's committed goals. Don't tear those down as part of task-scoped cleanup; their lifecycle is owned by `/weekly-plan`, not ad-hoc task requests.

## Skills

### Team Lead / Registry
| Skill | Purpose |
| --- | --- |
| `/team-lead` | Coordinate peer Claude Code sessions across managed products via claude-hive |
| `/add-project` | Append an existing repo to `registry.yaml` |
| `/new-project` | Scaffold a new project from scratch (GitHub repo, Linear project, `.claude/`, then registers via `/add-project`) |
| `/respawn-sessions` | Re-open all long-running Claude Code sessions in detached tmux sessions based on `registry.yaml` (`respawn: true` projects). Used manually after a Mac reboot or whenever sessions need to be rebuilt. |
| `/shutdown-session` | Cleanly shut down peer session(s) by project name. Maps to actual claude binary PIDs (not the hive-mcp child PIDs that `list_peers` returns), kills them, sweeps orphans, verifies. Use when spinning down agents not on this week's goals. |

### Team coordination
| Skill | Purpose |
| --- | --- |
| `/weekly-plan` | Set this week's goals with the user in a markdown doc bound to the Team Lead live-feedback workspace (**not Notion** — moved 2026-08-17). Carry over unfinished work, prioritize, estimate hands-on hours, the user picks, then expand kept goals. Each goal title is a measurable outcome with due date + estimate. |
| `/daily-review` | Intra-day status pass. Pulls peer transcripts + hive messages + open PRs + weekly-plan progress, asks each agent for clarification where needed, writes a prioritized review doc to `.claude/reviews/YYYY-MM-DD.md` brought under live-feedback for inline comments. |

### Agent Operations (used by peer sessions working on tickets)
| Skill | Purpose |
| --- | --- |
| `/ship-auto` | Full ship pipeline (review → PR → CI → Copilot → merge → deploy) with no mid-flow pauses. Default for personal repos. |
| `/ship-guarded` | Same pipeline + risk-surface assessment before merge (tools you rely on in production) |
| `/ship-push-only` | Push branch and stop; humans own PR + merge + deploy (advisory / team-owned repos) |

### Cross-Project
| Skill | Purpose |
| --- | --- |
| `/aggregate` | Pull learnings and retros from all registered projects, identify cross-cutting patterns |
| `/retro` | Meta-level retrospective on a session's transcript |
| `/persist-plan` | Persist an internal plan to `docs/product/plans/` |
| `/ux-review` | Walk a UI feature as a real user before shipping it |

## Key Directories

| Directory | Purpose |
| --- | --- |
| `plugin/team-lead-fleet/` | Canonical source of fleet-wide skills + alwaysApply rules. Every peer enables this plugin. |
| `.claude/skills/` | Team-Lead's own skills. Fleet-shared skills (`ship-*`, `retro`, `persist-plan`, `ux-review`) appear here as **symlinks** into `plugin/team-lead-fleet/skills/` — single source of truth, no recursion-via-plugin-self-enable. |
| `.claude/rules/` | Team-Lead's own rules. Same symlink pattern: each rule is a symlink into `plugin/team-lead-fleet/rules/` except `claude-hive-peer.md` which is peer-only and intentionally not symlinked here. |
| `docs/process/` | This project's own learnings + retros |

## Conventions

### Before Making Changes
- Read `registry.yaml` to understand which projects are managed
- Check `docs/process/learnings.md` for metaproject-specific gotchas

### After Making Changes
- Shared skills + rules ship via `plugin/team-lead-fleet/` — peers pull updates by reloading the plugin, no per-project sync needed
- Log non-obvious decisions in `docs/process/learnings.md`

### Privacy in Commits and PRs
The names and details of managed projects are private. When writing commit messages or PR descriptions for this repo:
- Do NOT include project names (e.g., use `registry: add new project`, not `registry: add my-project`)
- Do NOT describe what a new project does or who it's for
- PR descriptions should only cover what changed in *this* repo (skills, plugin, scripts, hooks), not the project that triggered the work

### Code Style
- Keep skills focused — one skill per workflow, not monolithic multi-purpose skills
- Fleet-wide skills + rules live in `plugin/team-lead-fleet/`; Team-Lead-only skills live in `.claude/skills/`
- **When creating or editing a skill**, follow `superpowers:writing-skills` PLUS `plugin/team-lead-fleet/rules/skill-authoring.md` — the latter adds the fleet steps (research the Claude System Prompt archive to avoid duplicating harness detail; keep it minimal; trigger-only descriptions)

## Private Files

Some files are gitignored because they contain project-specific data (project names, team members, IDs). These are symlinked from the main worktree in Team Lead worktrees.

| File | Contains |
| --- | --- |
| `registry.yaml` | Project list, team metadata, Linear/Notion IDs |
| `docs/process/retrospective.md` | Session retros (auto-generated, project-specific) |
| `docs/process/propagation-log.md` | Propagation audit log with PR URLs (gitignored) |
| `docs/process/aggregation-log.md` | Aggregation pass output (per-project learnings; gitignored) |

**After creating a worktree**, run `./scripts/setup-private.sh` to symlink these from the main worktree.

**One-time setup after a fresh clone** — enable the git hooks that auto-run `setup-private.sh` on worktree creation AND scan content for leaks before push:
```bash
git config core.hooksPath .githooks
```

See `registry.yaml.example` for the registry schema.

## Fleet health check

`scripts/fleet_healthcheck.py` runs 3×/day under launchd (`com.fryanpan.fleet-healthcheck`), silent on green, macOS notification on red. It costs no tokens — no model runs unless something is actually broken.

- **Read current status**: `healthcheck-status.json` in the deploy root (below), or the log at `~/Library/Logs/fleet-healthcheck.log`. Run it on demand with `/usr/bin/python3 <deploy-root>/fleet_healthcheck.py --verbose`.
- **After editing the checker or the registry**, run `python3 scripts/install_healthcheck.py` — it redeploys and regenerates the config. **Editing the repo copy alone changes nothing**: the running copy lives in the deploy root.

### Deploy root for anything launchd runs: `/opt/fleet`

A launchd-invoked Apple-signed binary is denied every operation on `/Volumes/Data` — exec, read, and even a stat. **`$HOME` does not save you**: `~/.claude`, `~/.config`, `~/.local` and `~/.bun` are each a symlink into that volume, so a path that looks like a home-directory path is often the secondary disk. `/opt` is genuinely boot disk (`disk3s5`) and is not shadowed by a symlink anyone might repoint.

Put the program *and* its config/state there; logs go to `~/Library/Logs` (real boot disk). One-time setup, since `/opt` is root-owned:

```bash
sudo mkdir -p /opt/fleet && sudo chown "$USER":admin /opt/fleet
```

`install_healthcheck.py` uses `/opt/fleet` when it exists and writable, and otherwise falls back to `~/Library/Application Support/team-lead/` with a printed warning.

When a launchd job genuinely must read the secondary volume (e.g. the plugin cache under `~/.claude`), delegate that read to `~/.bun/bin/bun` — the gate is per-binary, and a user-installed binary is not subject to it.
- **Every check asserts an end state, never a PID.** A process being up proved nothing in any real outage — see the 2026-08-11 learnings entry. If you add a check, make it fail when the thing stops *working*, not when it stops *running*.
- **Add a session check** by setting `always_up: true` on a registry entry. Don't key it on `respawn: true` — that means "bring back on a fleet restart", and most peers are correctly idle.

## Pre-push leak gate

`.githooks/pre-push` runs `scripts/scrub-check.py` on the diff being pushed and blocks the push if it finds project names (from `registry.yaml`) or denylist patterns. The principle: **once a push lands on GitHub and a PR is opened, the content is public-record forever (PR descriptions and commits can't be removed)** — so the gate has to fire BEFORE the push.

- Patterns: `projects:` keys in `registry.yaml` (auto-pulled; single-word names under 6 chars are skipped to avoid English-word collisions) + the hand-curated denylist at `~/.config/team-lead/scrub-denylist.txt`.
- Cross-repo fleet check: set `SCRUB_FLEET_REGISTRY=~/dev/<your-fleet>/registry.yaml` in your shell rc so the gate works in peer repos too.
- Self-name skip: the current repo's own name is never flagged (a repo legitimately self-references in its README / CLAUDE.md / plugin metadata).
- Bypass (use sparingly, never on a public repo without re-checking): `SCRUB_SKIP=1 git push ...`.
- Periodic audit: `python3 scripts/scrub-check.py --scan-all-tracked` scans every tracked file (not just the diff).
- Extending: edit `~/.config/team-lead/scrub-denylist.txt` (one pattern per line, plain string or `/regex/`).

## Linear (optional)
Set per-project Linear team info in `registry.yaml`. The metaproject uses these to file issues and check status.

@docs/process/learnings.md
