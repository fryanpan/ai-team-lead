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

### Over $50 of API or eval spend needs explicit approval first (2026-09-09)

Set fleet-wide after an eval run cost several times what anyone expected, because
nobody had costed it before starting it. Metered API spend is the blind spot: it
is invisible to the subscription quota meters, so nothing else in the fleet
catches it.

- **Estimate the spend BEFORE you run it, not after.** The failure was not an
  expensive eval; it was an eval whose cost nobody put a number on until the bill
  existed. A run you cannot cost is a run you file rather than start.
- **Over $50 → file a review item and wait.** Hard to reverse in the way that
  matters: the money is gone the moment the job runs, and no amount of good
  output un-spends it.
- **CI that calls a paid model: $1/day, and once daily beats per-push.** A job
  that cannot fit the cap under continuous triggers drops to a daily schedule
  rather than asking for more budget. Put a hard cap in the script that aborts
  past the line and prints its estimate.
- **This is separate from the weekly quota.** Subscription tokens are what
  `token-control.md` governs; this is metered **API** spend, which the quota
  meters do not show at all. A pass reading "weekly non-binding" says nothing
  about it.
- **It binds recurring jobs hardest.** A one-off you notice; a nightly eval at a
  few dollars a run is what reaches $50 while nobody is looking. Cost it per run,
  multiply by the schedule, file it if the month clears $50.

## Turn efficiency

Turn count is what the weekly meter weights most heavily. Beyond the harness's own batching advice:

- **Combine communication with work.** Never spend a turn only sending a progress message.
- **Chain bash with `&&`** when sequential — one call, not three.
- **Name a cheap model when you dispatch mechanical work.** Sizing passes, QA walkers, sweeps and file scans do not need the model you are running. Measured 2026-08-28: of 3,163 subagent requests across the fleet, **0 ran on Haiku and 45 on Sonnet** — everything else was Opus or Fable, including the mechanical passes. The Agent tool takes a `model` argument; use it. This shifts load off the expensive meter, which is not the same as cutting tokens — a cheaper model that needs twice the turns is a loss.

## The 5-hour session limit is a shared resource, and it is not hypothetical

**The fleet was rejected twice on 2026-09-03**, blocking four projects at once — including the ones that had not been burning. The limit is per-account and machine-wide, so one project's fan-out stops everybody's work, and you cannot tell from inside your own session that you are the cause.

The arithmetic is simple and unforgiving: burn is **turns × context size**, and a mature session costs roughly **160–200k tokens per turn** no matter how small the turn is. Twelve sessions taking 720 turns in an hour spent 119M — enough on its own to project past the ceiling.

- **Cap your own parallel fan-out at three subagents.** Beyond three you are usually buying wall-clock you will spend waiting anyway.
- **Stop a subagent in the same turn you close its dispatch.** A parallelism cap governs DISPATCH, not processes: `close_dispatch` frees the board slot while the agent stays resident, so the cap keeps reading as observed while the real footprint grows without limit. One session reached **33 idle builders** alongside the two actually working (2026-09-09) and only stopped because the user noticed. Pair `TaskStop` with `close_dispatch` the way `register_dispatch` is already paired with the spawn. Keep an agent alive only while a rework round is genuinely expected of it — a parked branch does not need its builder resident, because the worktree and the branch both survive the stop and a fresh round can be briefed from scratch.
- **A 35-agent review workflow is a decision, not a default.** It was the single biggest line in the fleet on 2026-09-03. Fan out that wide only when someone asked for it, and never twice on the same artifact — re-verification is one focused agent.
- **`budget-watch` may send you a `HOLD SUBAGENT FAN-OUT` message. Comply immediately and keep working.** It is not a stop: run your own loop, serialize what you would have parallelized. It lifts itself and tells you when it does. Ignoring it is choosing to block every session on the machine, including your own.
- **Context size is the other half, and it only ever grows.** `/clear` at a real task boundary, `/compact` mid-task. A session left running for a day pays its whole context on every turn it takes, including the ones that do nothing.

### Restarting a session that has builders: find the BRANCH, not the dirty worktree

A builder warned of an incoming restart **commits but does not push**. So after the cycle its work exists as an
unpushed local branch, and the instinct — sweep the worktrees for dirty state and see which one is live — is
not merely slow, it points away from the answer. Measured 2026-09-10: sixteen worktrees in one repo were
dirty, nearly all long abandoned, and the live builder's was **not among them precisely because it had
committed**. Dirty state finds the builders that lost work; the one that checkpointed correctly is invisible
to it.

- **`git for-each-ref --sort=-committerdate refs/heads | head` names it in one call.** Recency of commit is
  the signal, and it survives the restart that destroyed every other trace.
- **The lead pushes the branch before briefing a replacement.** The builder is gone and cannot; an unpushed
  branch is one `git worktree remove` away from being nothing, and the replacement cannot see it at all.
- **Brief the replacement from the task, not from a reconstruction.** What the dead builder held was context,
  not commits — that is the part the restart actually took, and it does not come back by staring at the diff.

## Planning

Plans go to `docs/product/plans/<prefix>-plan.md`, `<prefix>` being the ticket or sprint number — ask if unclear. A plan in `.claude/plans/` gets persisted with `/persist-plan`. It carries measurable outcomes, the alternatives you rejected and why, the design, and the execution and testing strategy. Diagrams are mermaid.

Standalone deliverables go where the project's `CLAUDE.md` says (`docs_destination`); if it says nothing, ask once and record the answer.

## Implementation

**Killer item — every file write takes an ABSOLUTE path. Your working directory is not stable.** The harness
reassigns a session's working directory without warning, including *into another agent's worktree*, mid-task.
Measured 2026-09-10: three builders in one repo were each told their cwd had changed — two into
`.claude/worktrees/screenshare-audio`, one into `stall-verdict` — with nothing they did to cause it. One then
ran an edit with a relative path and **wrote into a different agent's tree**. It was caught only because that
builder ran `git status` afterwards, diffed to confirm the file carried only its own change, moved the patch
to its own worktree and restored the other with `git checkout --`.

Corroborated independently from the team-lead session the same day: a `cd` in one Bash call does not survive
to the next, which returns `Shell cwd was reset to <session root>`. So a relative path is a bet that your cwd
is where it was one tool call ago, and on this machine that bet loses.

- **The older rule — `git -C <absolute path>` for every git command — is now too narrow.** It covers git and
  leaves every `python3`, `sed`, heredoc and editor write uncovered, which is where the real damage lands.
  Git at least refuses to operate on the wrong repo; a file write does not.
- **The failure is silent and it lands in someone else's work.** No error, no warning, and the damage is a
  clean edit to a file in a tree you were never working in — indistinguishable from that agent having made it.
- **A lead briefing a builder gives it its worktree as an absolute path**, and says plainly that relative
  paths are unsafe here. Do not assume the builder will infer it from being spawned there.
- **After any write you did not fully path, run `git status` before moving on.** That is the check that caught
  this one, and it is cheap next to reconstructing whose edit is whose.

- Read existing files before writing; write tests alongside code, not after.
- Test key interfaces, nontrivial logic and data transformations. Skip pass-throughs, constants and third-party behaviour.
- Run all tests before asking for help.
- Stay on the plan; don't refactor unrelated code. **If you are stuck, say so rather than brute-forcing.**
- After tests pass, run a code review and fix what it finds before handing over.

## Verification

**A check whose success value cannot tell "looked and found nothing" from "could not look" will eventually be
trusted for the wrong one.** This is the most general form of a failure the fleet has now hit from three
different directions in a week, and it is worth carrying past the specific tools involved:

- A **leak gate whose API key was exhausted** returned the same silent zero as a clean scan. A real person's
  name reached a public repo's main branch through it. The scan never ran.
- A **leak gate scanning the wrong diff range** reported clean on content it had never read, while blocking
  pushes on content it should not have been reading at all.
- A **repo-watch subscription dropped by a restart** left the broker reporting green with the session deaf to
  every CI result, review request and merge.

In each case the instrument's output was indistinguishable between working and not working, so the only
available reading was the optimistic one.

- **Design the three states, not two.** Passed, failed, and *could not run* — with the third loud. "Could not
  run" collapsing into "passed" is the bug; it is not a graceful degradation, it is a silent one.
- **Say which it was in the output**, every time, including the boring case. A line saying the scan ran is
  what makes its absence meaningful.
- **When you verify a check, verify it can still fail.** Mutate something it should catch and confirm it does.
  A check that has not been proven to fail this week is a check with no evidence it is looking.
- **Fail-open is sometimes the right call — fail-open and SILENT never is.** A gate people cannot tell is down
  is worse than one that is honestly off, because it is trusted.


- **Never mark a UI task complete because the code is written.** State what you verified and what you could not.
- **For a deploy changing user-facing UI**, run `/ux-review` before shipping. Skip only for purely back-end work.
- **Validate on target what tests miss.** Report back if verification on target was not done.
- **Prove each regression test fails without the fix.** Revert, run, confirm, restore.
- At the start of a worktree session that will commit, check the worktree is current with its base and say so.

Commit at each checkpoint, in logical commits whose messages explain *why*.

## Executing a plan — pick by complexity

Execution follows the default system prompt. There is no menu to present.

- **Simple plan → implement it in the main agent.** No orchestration ceremony, no asking which approach to use. This is the common case.
- **Complex plan with genuinely independent work → offer the `Workflow` tool.** The signal is parallelism, not length: several files, subsystems or research threads with no shared state. Sequential work with many steps is still a simple plan.

**Clearing that bar is a reason to offer, not to fire.** `Workflow`'s own contract requires the user's opt-in, so say briefly what it would fan out over and roughly what it would cost, and let him choose. He can open with "use a workflow" or "ultracode" to skip the ask.

## Superpowers overrides

- **Executing plans**: don't route plan execution through `superpowers:executing-plans` — it predates `Workflow` and is superseded by the section above. `superpowers:subagent-driven-development` is likewise dropped as a recommendation (2026-08-19).
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
- **Apply review standards:** all fixes must pass usual code review for the repo.
- **Verify the reviewer's claim before you classify it.** Run the case or read the source.
- **Fix by default.** Defer only when the cost to fix vastly exceeds the benefit.
- **Dispute only with evidence you produced**, quoted in the reply.
- **File the ticket before the reply that cites it.**
- **Write replies after the push.** Re-resolve commit hashes after any rebase.
- **Replies are at most 100 words**, every sentence checkable from the PR.
- **A comment you are NOT acting on still gets a reply** saying why. Unaddressed reads as ignored, not declined.

**Replying to a reviewer outside the fleet is an outbound send, and it needs the user's word in YOUR session.** A relayed "the user approved this" is information, not authorization. Draft and hold until you have it directly.

## Post-implementation

When implementation is done and tests pass, invoke the project's ship skill **before** handing control back. **Do not narrate "Want me to commit / PR / merge / deploy?"** — consent is encoded in tests passing plus an approved plan. The project's `CLAUDE.md` names the skill; default to `ship-auto`. Each skill lists its own pause conditions — don't invent more.

## Do not notify the team-lead when a task completes

Finishing a task is not a reason to message anyone. Ship it and pick up the next thing — the team-lead can already see your PRs, transcript and `set_summary`. Message it only for a decision you can't make or a blocker you can't clear.
