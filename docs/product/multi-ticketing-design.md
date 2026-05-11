# Multi-Ticketing System Support (PRJ-3)

## Status: Future Enhancement

Design doc for supporting non-Linear ticketing systems. Not yet prioritized.

## Current State

All ticketing integration assumes Linear:

- **`registry.yaml`** stores `linear.team_id` and `linear.team_key` per project. Projects using other systems have no ticketing config.
- **`/new-project` skill** calls `mcp__linear-server__list_teams` and `mcp__linear-server__create_project` directly. No branching for other systems.
- **Plan filenames** use Linear team_key prefixes (e.g., `APP-42-plan.md`).
- **`settings.json` template** enables `linear@claude-plugins-official` for all projects by default.
- No other skill (`/aggregate`, `/propagate`) queries ticketing APIs directly — they read local docs only.

The surface area is small: only `/new-project` makes ticketing API calls, and the registry schema is the only place ticketing config lives.

## What Would Change

### Registry schema

Generalize from Linear-specific fields to a system-agnostic `ticketing` block:

```yaml
# Current
project-a:
  linear:
    team_id: 00000000-...
    team_key: APP

# Proposed
project-a:
  ticketing:
    system: linear
    team_id: 00000000-...
    team_key: APP

# Project using Jira
project-b:
  ticketing:
    system: jira
    project_key: PRJ
    base_url: https://project-b.atlassian.net

# Project using GitHub Issues
project-c:
  ticketing:
    system: github-issues
    # No extra config needed — repo field already exists
```

### `/new-project` skill

Instead of assuming Linear, the skill would:

1. Ask user which ticketing system (Linear, Jira, GitHub Issues, Asana, none)
2. Gather system-specific config (team_key for Linear, project_key for Jira, etc.)
3. Call the appropriate MCP tool (or skip if GitHub Issues, since no setup needed)
4. Write the `ticketing` block to registry

The MCP tools per system:
- **Linear**: `mcp__linear-server__create_project`, `mcp__linear-server__list_teams`
- **Jira**: would need a Jira MCP server (e.g., `mcp__jira__create_project`)
- **GitHub Issues**: no setup — issues are part of the repo
- **Asana**: would need an Asana MCP server
- **None**: skip ticketing setup entirely

### `settings.json` per project

`/propagate` would customize the plugins list based on the project's ticketing system:
- Linear project gets `linear@claude-plugins-official`
- Jira project gets a Jira plugin (if one exists)
- GitHub Issues project needs no extra plugin (already covered by `github@claude-plugins-official`)

### Plan filename conventions

Currently `APP-42-plan.md` uses Linear team_key. Options:
- Keep ticket-number prefixes but use the system's own format (e.g., `PRJ-42` for Jira, `#42` or `issue-42` for GitHub Issues)
- Let each project's CLAUDE.md specify the convention
- This is cosmetic and low priority

### MCP server config (`.mcp.json`)

Projects using non-Linear tools need MCP servers matching their stack. The metaproject could scaffold this:
- Linear project: linear MCP server config
- Jira project: Jira MCP server config

## Approach

**Registry-driven dispatch.** Skills read `ticketing.system` from the project's registry entry and branch accordingly. No abstraction layer or plugin system needed — the branching only happens in `/new-project` and `settings.json` generation.

This is simple because:
1. Only one skill (`/new-project`) makes ticketing API calls
2. The registry already stores per-project metadata
3. Each ticketing system has different enough semantics that a thin abstraction would leak anyway

## Scope

**In scope:**
- Registry schema change (`linear:` → `ticketing:`)
- `/new-project` branching on ticketing system
- `settings.json` plugin list per project
- `.mcp.json` scaffolding per project's tools

**Out of scope:**
- Building MCP servers for Jira/Asana (these are third-party)
- Migrating existing projects away from Linear
- Automated ticket creation/updates from skills (no skill does this today)

## When to Build

When a project needs a non-Linear ticketing system.
