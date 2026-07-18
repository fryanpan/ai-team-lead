# Skill Authoring — Fleet Additions

Complements `superpowers:writing-skills` (the RED→GREEN→REFACTOR process). These are the fleet-specific steps to run *in addition*, whenever you create or edit a skill.

## Check the harness before you document anything

Before writing skill-body content, **dispatch a research agent to check the Claude System Prompt archive** (the published Claude Code system-prompt / harness instructions) and confirm the skill is NOT duplicating detail the harness already gives every agent.

- **Cut anything the harness already covers** — how to use a tool, how to `ToolSearch`-load deferred MCP tools, how to take a screenshot, standard tool mechanics, permission conventions. Re-documenting it wastes tokens every time the skill loads and drifts out of date as the harness changes.
- **Keep only what's NOT in the harness:** the judgment call, the fleet-specific rule, the one non-obvious technique.
- If the research agent can't locate the archive, fall back to the test: *"Would a competent agent already know this from its own tools and system prompt?"* If yes, cut it.

## Keep it minimal

A skill is a rule plus the one non-obvious thing — not a tutorial. No background essays, no re-explaining the harness. If the body reads like documentation an agent could have written from its tools, it's too long. (`qa-delegate` is the reference for length: two rules, ~180 words.)

## Description discipline

The `description` states *when to use* only — triggering conditions and symptoms. Never summarize the workflow in it: agents follow the description and skip the body (per `superpowers:writing-skills` SDO section).
