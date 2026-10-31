---
alwaysApply: true
appliesTo: main
---

# Skill Authoring — Fleet Additions

Complements `superpowers:writing-skills` (the RED→GREEN→REFACTOR process). These are the fleet-specific steps to run *in addition*, whenever you create or edit a skill.

## Check the harness before you document anything

Before writing skill-body content, **ask the research agent to check the Claude System Prompt archive** (the published Claude Code system-prompt / harness instructions) and confirm the skill is NOT duplicating detail the harness already gives every agent. Ask it for the verdict and the citations — the archive text itself stays in its context.

- **Cut anything the harness already covers** — how to use a tool, how to `ToolSearch`-load deferred MCP tools, how to take a screenshot, standard tool mechanics, permission conventions. Re-documenting it wastes tokens every time the skill loads and drifts out of date as the harness changes.
- **Keep only what's NOT in the harness:** the judgment call, the fleet-specific rule, the one non-obvious technique.
- If the research agent can't locate the archive, fall back to the test: *"Would a competent agent already know this from its own tools and system prompt?"* If yes, cut it.

## A process that runs on a schedule and on request lives in a skill (2026-10-27)

Set by Bryan: *"The daily task should reference a checked in skill for the reply doc process rather than have
that logic be in a task. Sometimes we trigger reply drafting or editing not from a task."*

- **Put the procedure in a checked-in skill.** A scheduled task's body is invisible to every run that does
  not start from that task.
- **The scheduled task names the skill** and holds only what belongs to the schedule: when it runs, and what
  window it covers.

## A skill built from a finished project opens with what it is for (2026-10-31)

Set by Bryan on a skill written from a finished project: *"You got the mechanics but missed a lot of the high level goal stuff. Review all the things [a collaborator] and I asked for and please add here (eg about persuasion and brevity)"*. The steps alone reproduce the shape of the work and lose the reason for it.

- **Before writing the steps, collect every ask the owner and collaborators made**, on the project's docs and in chat. Include resolved threads.
- **Put them in a short "What it is for" section ahead of the steps**, quoting their words.
- **Every agent prompt in the skill or workflow reads that section first.**

## Keep it minimal

A skill is a rule plus the one non-obvious thing — not a tutorial. No background essays, no re-explaining the harness. If the body reads like documentation an agent could have written from its tools, it's too long. (`qa-delegate` is the reference for length: two rules, ~180 words.)

## Description discipline

The `description` states *when to use* only — triggering conditions and symptoms. Never summarize the workflow in it: agents follow the description and skip the body (per `superpowers:writing-skills` SDO section).
