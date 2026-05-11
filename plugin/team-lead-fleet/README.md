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

### Rules (alwaysApply — injected at SessionStart via hook)
- `claude-hive-peer.md` — peer protocol (set_summary, list_peers, send_message via to_stable_id, /compact after task close)
- `workflow-conventions.md` — planning, decision framework, commit discipline, LLM turn efficiency, code review, post-implementation, team-lead-notification
- `feedback-loop.md` — capture learnings, periodic retros
- `live-feedback-default.md` — bind markdown / dev-server reviews to the live-feedback widget
- `notion-channel-protocol.md` — handle notion-channel events as peer asks
- `notion-mcp.md` — MCP conventions, retry behavior, agent identification in comments
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

The marketplace lives at `~/dev/ai-team-lead/plugin/.claude-plugin/marketplace.json`. Each peer needs the marketplace registered once (Claude Code remembers it across sessions).

## Updating

Skills + rules update on the next session start (hook re-reads files). For a running session, `/reload-plugins` picks up the latest.

## Project-specific overrides

Project-specific skills/rules stay in the project's own `.claude/skills/` and `.claude/rules/`. Those override or extend what the plugin provides — they're not replaced by it. After the migration, each project's `.claude/` should hold *only* project-specific content; everything generic comes from the plugin.

## Source of truth

Plugin source lives in `~/dev/ai-team-lead/plugin/team-lead-fleet/`. Changes there propagate fleet-wide on next session start — no PRs per project.
