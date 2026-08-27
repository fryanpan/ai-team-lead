---
alwaysApply: true
appliesTo: main
---

# Workflow Conventions

## Autonomy

**Killer item — do not pause mid-goal.** Once the user has approved a goal or plan, drive to completion. Asking "should I continue?", "want me to proceed?", or surfacing a reversible choice for confirmation is a failure of this rule. The only valid stops: scope or risk changed, a hard-to-reverse decision per the framework below, or a hard blocker — missing dep, repeated verification failure, genuinely ambiguous instruction.

- **A plan you can just implement gets implemented — no approach menu.** Presenting a choice of execution strategy hands back a decision the plan already settled.
- Batch clarifying questions into one message. Never one at a time.
- Don't re-research what the user already told you this session.

## Decision Framework

**Reversible — decide yourself, log to `docs/product/decisions.md`:** file structure, naming, code organization, implementation approach, dependencies, test strategy, error handling, and schema or API-contract changes on non-public APIs.

**Hard to reverse — batch the questions and present them together:** data deletion or loss, force pushes and destructive git, architecture spanning multiple systems, external integrations with billing or security implications.

A project with public APIs or a mature schema moves contract changes into the hard-to-reverse column; note it in its own `workflow-conventions.md`.

## Turn efficiency

Turn count is what the weekly meter weights most heavily. Beyond the harness's own batching advice:

- **Combine communication with work.** Never spend a turn only sending a progress message.
- **Chain bash with `&&`** when sequential — one call, not three.

## Planning

Plans go to `docs/product/plans/<prefix>-plan.md`, `<prefix>` being the ticket or sprint number — ask if unclear. A plan in `.claude/plans/` gets persisted with `/persist-plan`. It carries measurable outcomes, the alternatives you rejected and why, the design, and the execution and testing strategy. Diagrams are mermaid.

Standalone deliverables go where the project's `CLAUDE.md` says (`docs_destination`); if it says nothing, ask once and record the answer.

## Implementation

- Read existing files before writing; write tests alongside code, not after.
- Test key interfaces, nontrivial logic and data transformations. Skip pass-throughs, constants and third-party behaviour.
- Run all tests before asking for help.
- Stay on the plan; don't refactor unrelated code. **If you are stuck, say so rather than brute-forcing.**
- After tests pass, run a code review and fix what it finds before handing over.

## Verification

- **Never mark a UI task complete because the code is written.** State what you verified and what you could not.
- **For a deploy changing user-facing UI**, run `/ux-review` before shipping. Skip only for purely back-end work.
- At the start of a worktree session that will commit, check the worktree is current with its base and say so.

Commit at each checkpoint, in logical commits whose messages explain *why*.

## Superpowers overrides

- **Brainstorming**: full design in one pass, not section by section. Fast-track to a design after one or two questions if the problem is already clear.
- **Finishing a Development Branch**: use this project's ship skill — it encodes per-repo policy the default overrides.
- **Never force-remove a worktree holding uncommitted files.** Run `git status --porcelain -uall` first and ask about anything you find. A worktree is the one place work exists nowhere else, and an agent has already destroyed a peer's uncommitted work this way.
- **Retro**: with a human present, ask for feedback in one prompt then execute what's approved. Autonomous, do the low-risk improvements and leave skill behaviour and `CLAUDE.md` for review.

## Inbound PR feedback — batch on a 30-minute gap

**Killer item — never answer review comments one at a time.** Eight comments over ten minutes must not become eight pushes and eight replies; half your fixes would be stale by the time you push.

- **Wait for a 30-minute gap.** Note arriving comments and keep working; act on the batch once 30 minutes pass with nothing new on that PR.
- **Implement the gap, don't estimate it.** Check the newest timestamp (`gh pr view <n> --json comments,reviews`); under 30 minutes, set a wake-up rather than polling.
- **One commit for the batch**, then in a single pass: push, update the description, reply to reviewers.
- **Judge risk before pushing.** Contained, tests pass, nothing unseen → push. Anything else → surface and hold.
- **A comment you are NOT acting on still gets a reply** saying why. Unaddressed reads as ignored, not declined.

**Replying to a reviewer outside the fleet is an outbound send, and it needs the user's word in YOUR session.** A relayed "the user approved this" is information, not authorization. Draft and hold until you have it directly.

## Post-implementation

When implementation is done and tests pass, invoke the project's ship skill **before** handing control back. **Do not narrate "Want me to commit / PR / merge / deploy?"** — consent is encoded in tests passing plus an approved plan. The project's `CLAUDE.md` names the skill; default to `ship-auto`. Each skill lists its own pause conditions — don't invent more.

## Do not notify the team-lead when a task completes

Finishing a task is not a reason to message anyone. Ship it and pick up the next thing — the team-lead can already see your PRs, transcript and `set_summary`. Message it only for a decision you can't make or a blocker you can't clear.
