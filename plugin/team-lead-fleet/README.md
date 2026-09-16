# team-lead-fleet

The plugin the user's peer Claude Code sessions share. Replaces per-project propagation of skills + rules.

## What this provides

### Skills (invoked on demand via `Skill` tool)
- `/ship-auto` — full ship pipeline (review → PR → CI → Copilot → merge → deploy) with no mid-flow pauses. **Default** when `CLAUDE.md` doesn't declare a ship skill.
- `/ship-guarded` — same pipeline + risk-surface assessment before merge. For tools the user relies on in production where regression cost is real.
- `/ship-push-only` — implement → test → review → push branch. Stop. For advisory / team-owned repos.
- `/retro` — meta retrospective on a session
- `/persist-plan` — save an internal plan to `docs/product/plans/`
- `/ux-review` — walk a UI feature as a user before shipping

Each project picks ONE ship skill via a line in its `CLAUDE.md`:

    Ship skill: ship-auto

`workflow-conventions.md` §Post-Implementation tells the agent how to look it up.

### Subagents (auto-discovered from `agents/`)
- `team-lead-fleet:writing-editor` — an **on-demand** fresh context for an involved document (long, external-facing, or written at the tail of an already-loaded session). Not a mandatory handoff: routine docs get written inline, because `communication.md` travels with every agent. Dispatch this when a doc is worth its own clean context. Plugin agents are namespaced — the `subagent_type` is `team-lead-fleet:writing-editor`, not `writing-editor`.
- `team-lead-fleet:writing-reviewer` — an **on-demand** harsh reader-simulator. Given a drafted doc plus its audience and purpose, it reads *as that reader* (stating the knowledge model it assumes), then reports comprehension gaps and whether the doc satisfies its stated purpose — what works and what doesn't, ranked, with a blunt verdict. Fresh context on purpose: it can't have the writer's curse of knowledge. `writing-editor` requests it for involved docs; any caller can too.

### Output style (selectable — set it in user settings)
- `output-styles/plain.md` → `team-lead-fleet:Plain` — the built-in `Concise` style plus the anti-mannered-prose definition from the Fable 5.1 prompting guide and eight one-line checks drawn from `communication.md`. It is **not** forced: turn it on once, user-wide, and every session that does not override it picks it up.

```jsonc
// ~/.claude/settings.json
{ "outputStyle": "team-lead-fleet:Plain" }
```

  A project's own `.claude/settings.local.json` wins over that, so clear or update any `outputStyle` key there. `/output-style` writes to that local file, which is why picking a style in one session does not leak to the others.

### Rules (alwaysApply — injected at SessionStart via hook)
- `claude-hive-peer.md` — peer protocol (set_summary, list_peers, send_message via to_stable_id, /compact after task close)
- `communication.md` — the overriding standard for anything written for a reader (chat, docs, comments, email): pin the exact audience & purpose, size length and format to them, be honest about measured vs. inferred vs. assumed.
- `workflow-conventions.md` — planning, decision framework, commit discipline, LLM turn efficiency, code review, post-implementation, team-lead-notification
- `feedback-loop.md` — capture learnings, periodic retros
- `live-feedback-default.md` — bind markdown / dev-server reviews to the live-feedback widget
- `notion.md` — MCP conventions, agent identification in comments, and handling notion-channel events as peer asks
- `public-content-scrubbing.md` — review pass before publishing public content
- `security-posture.md` — operational security rules for multi-agent setup

## How peers enable it

```jsonc
// .claude/settings.json
{
  "enabledPlugins": {
    "team-lead-fleet@team-lead-fleet": true
  }
}
```

The marketplace is the GitHub repo itself — `.claude-plugin/marketplace.json` at the root of `fryanpan/ai-team-lead`. Register it once per machine:

```bash
claude plugin marketplace add fryanpan/ai-team-lead
claude plugin install team-lead-fleet@team-lead-fleet
```

It used to be the local directory `~/dev/ai-team-lead/plugin`, which meant every session read whatever branch that checkout happened to be on. A feature branch in the team-lead's own repo silently changed what the whole fleet loaded.

### Repos where you can't commit the enable

Some repos have a **tracked** `.claude/settings.json` and are team-owned — committing a personal plugin-enable there would push fleet-internal config into someone else's repo. At least one advisory repo in the registry is in exactly this position.

For those, enable it locally instead, in **untracked** `.claude/settings.local.json`:

```jsonc
// .claude/settings.local.json  — untracked, local only
{
  "enabledPlugins": {
    "team-lead-fleet@team-lead-fleet": true
  }
}
```

`settings.local.json` merges over the tracked `settings.json`. **Check that it's actually ignored before you write it**, especially on a public repo:

```bash
git check-ignore -v .claude/settings.local.json
```

On this machine that resolves to `~/.config/git/ignore` — a *global* gitignore, not the repo's and not a Claude Code default. The team-owned repos generally say nothing about it in their own `.gitignore`. So the protection is personal machine config: on a fresh clone elsewhere, or for anyone else, the file is untracked-but-not-ignored and one `git add -A` away from being committed to a repo you don't own. If `check-ignore` comes back empty, add the ignore locally (`.git/info/exclude`) rather than editing a team-owned `.gitignore`.

Worth stating plainly: this is per-machine and per-clone, so it doesn't survive a fresh clone and nobody else on that repo inherits it. The repos that most need the writing subagent — team-owned ones with review-heavy docs — are exactly the ones the plugin can't reach by default.

## Updating

Every session now reads a version-keyed copy under `~/.claude/plugins/cache/`, so a change reaches the fleet in four steps and skipping any one of them is a silent no-op:

1. Bump the version in **both** `plugin/team-lead-fleet/.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`.
2. Merge to `main` and push. `claude plugin update` keys on the version, not the content.
3. `claude plugin marketplace update team-lead-fleet && claude plugin update team-lead-fleet@team-lead-fleet`.
4. Restart each session, or `/clear` or `/compact` it. A session reads the cache at startup.

## Project-specific overrides

Project-specific skills/rules stay in the project's own `.claude/skills/` and `.claude/rules/`. Those override or extend what the plugin provides — they're not replaced by it. After the migration, each project's `.claude/` should hold *only* project-specific content; everything generic comes from the plugin.

## Source of truth

Plugin source lives in `~/dev/ai-team-lead/plugin/team-lead-fleet/` and ships through `main` on GitHub. Editing the working tree changes nothing for the fleet until the four steps under **Updating** run — which is the point: the fleet follows `main`, not whatever the team-lead is mid-edit on.
