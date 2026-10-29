---
alwaysApply: true
appliesTo: main
---

# Workflow Conventions

## Autonomy

**Killer item — do not pause mid-goal.** Once the user has approved a goal or plan, drive to completion. Asking "should I continue?", "want me to proceed?", or surfacing a reversible choice for confirmation is a failure of this rule. The only valid stops: scope or risk changed, a hard-to-reverse decision per the framework below, or a hard blocker — missing dep, repeated verification failure, genuinely ambiguous instruction.

- **A plan you can just implement gets implemented — no approach menu.** Presenting a choice of execution strategy hands back a decision the plan already settled.
- Batch clarifying questions into one message. Never one at a time.

### A request is the go-ahead; a conflict is raised in the reply, not later (2026-10-08)

- **Start what he asked for in the same turn.** A request is not an opening for a menu of whether to do it.
- **If the deadline, the run length or the cost is a problem, say so in your reply to the request**, while he
  is still there, and start anyway unless the problem means it must not start. The spend-approval rule below still
  applies.
- **Spending the fleet's own device, build or compute time is reversible; publishing is not.** His ask is the
  go-ahead for a run, a measurement or a build, including when the team-lead relays it. A decision item is for
  what leaves the fleet — publishing, sending, spending over the approval line — not for whether to run it.
- **Never turn a request into a review item that asks whether to do it.** The queue is where asks get lost;
  the conversation he is in is where they get answered.
- **A wait that idles hours of device, build or compute time puts him on the critical path.** Say so in the
  item's first line with what is idle and for how long, message the team-lead at once so it can reach him,
  and keep the work moving on your best reversible guess while you wait.
- Don't re-research what the user already told you this session.

### A project lead drives the work, so he doesn't have to (2026-10-19)

Set by him on one project's lead: *"I want you as project lead to be driving this work. Critiquing
it. Moving it along and getting it polished so I don't have to keep utzing."*

- **After each answer of his, line up the next runs yourself.** His answer starts the next step; it
  is not the end of the exchange.
- **Critique your own output before it reaches him.** He should not be the one to find what is weak,
  wrong or unpolished.
- **Settle what the decision log already covers.** Ask him only what it does not.
- **When he says he will be away, give him a timed plan:** what is running, what he will see when he
  is back, and what is at risk.

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

## Machine memory is shared too: heavy jobs run on a cloud VM (2026-10-20)

Set by the user after memory pressure on the shared machine took the fleet down more than once: *"can you
please run optimizers and sims on VMs? It seems like we've trashed memory repeatedly on this machine"*.
Every session runs on one machine, so one project's heavy job can take memory from every other session.

- **A model, optimizer or simulation that loads more than a few GB runs on a cloud VM**, never on the
  shared machine. This includes any agent sent to read that job's caches or outputs.
- **A brief to such an agent names the VM's ssh host** and says the local machine is for reading code
  and logs only.
- **Local runs are for light jobs** that stay inside the existing worker and memory limits.

## Planning

Plans go to `docs/product/plans/<prefix>-plan.md`, `<prefix>` being the ticket or sprint number — ask if unclear. A plan in `.claude/plans/` gets persisted with `/persist-plan`. It carries measurable outcomes, the alternatives you rejected and why, the design, and the execution and testing strategy. Diagrams are mermaid.

Standalone deliverables go where the project's `CLAUDE.md` says (`docs_destination`); if it says nothing, ask once and record the answer.

**Before proposing a solution to a product improvement, run `product-discovery`.** This includes a request that already names its solution.

### Look for signal within a week (2026-10-23)

Set by Bryan on a spec that measured success at 30 days: *"we should look for signal in a week. Move faster. Make that part of fleet wide default"*.

- **Every success measure, pilot or experiment names a signal you can read within one week.** A 30-day or quarterly check can follow it. It does not replace it.

### Reuse the vendor the project already runs before adding one (2026-10-21)

Set by Bryan after a plan moved a workload to a new cloud provider and asked him for its account, when the
same workload already ran on another: *"If GCP is fine, let's just keep using GCP and not bring on yet
another cloud provider."*

- **Before a plan adds a vendor, cloud provider or paid service, check what the project already runs for
  that job**: its repo, its lead, its accounts.
- **Say in the plan why the existing one won't do.** A new account is a new bill, a new credential and a
  new thing to ask him for.

### Research the options broadly before agents converge on one (2026-10-15)

Set by the user on a multi-agent design thread, after a recommendation switched providers and every
agent on the thread moved straight to checking details of the new pick: *"Don't just glom on to single
solutions and all group think your way into a solution. Research solutions broadly. This holds for all
discussions."*

- **When the user questions a recommendation, widen the option set before narrowing it.** List the
  distinct classes of solution, not variants of the current pick, and say what each costs and gives up.
- **Agreement among agents is not evidence.** Several peers checking their own fit against one option
  only tells you that option fits; it says nothing about the options nobody looked at.
- **Say which options you did not research.** An unexamined option left unnamed reads as rejected.

### Plan tight and realistic: no sandbagging, no cut corners (2026-10-19)

Set by the user after pulling three dates in a weekly plan forward himself: *"your job is to make a plan
that's tight in the future. No sandbagging. But realistic also. No cutting corners to make it work."*

- **Test every date a lead proposes before it goes in a plan.** Ask what the earliest honest date is and
  what sets it: a run time, a review, a person's turnaround. A date with no such reason is padding.
- **When a date is set by slow tooling, make the tooling faster.** A long compute run is a problem to fix
  inside the week, not a reason to push the outcome out.
- **Never meet a date by dropping a review, a test or a check.** If the honest date is too late, say so
  and let the user choose.
- **Name the one risk that would move the date**, with its cost in hours.

### A reply to a person is due within 1–2 business days (2026-10-19)

Set by the user on a weekly plan that scheduled a reply to an outside contact four days out: *"No we try
to get back to people within 1-2 business days."*

- **Date a reply from when they wrote, not from when it suits the plan.** A reply owed to a person is
  never a late-week outcome by default.
- **Have the draft and the review item ready the same day it arrives**, so the user can send it in time.
- **If something holds the reply past that window, say so on the item** and name what is holding it.

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
- **When repo rules stop you before the ship skill** (agents may not commit or push), run the code-review skill yourself before handing the change over. Stopping early does not skip the review. Set 2026-10-28, after a change reached Bryan with no review.

### Key flows keep written goals, and a change that breaks one is flagged (2026-10-28)

Bryan: *"documents the goals and requirements of key flows… during design and review we try to maintain those goals -- if we violate them, flag it."*

- **Each project keeps a short list of goals per key user flow**, such as "photo upload never waits for GPS". Each goal names its source (a doc, a decision record, an issue) or is marked inferred.
- **Every design and every diff review checks the goals of the flows it touches.**
- **A change that breaks a goal names it in the explanation, and needs the user's OK before it is built.**
- **Write a flow's goals the first time you change that flow.**

### Writes that belong together go in one transaction (2026-10-28)

Bryan, on a change that wrote linked records separately: *"review for whether changes need to go together or not, and use transactions where appropriate."*

- **When a request writes more than one record, list them.** Ask whether a failure between two writes leaves a half-done state a reader can see.
- **If it does, write them in one transaction**, using the pattern the repo already uses. Say what happens when one record's condition fails: retry without it, or fail the request.
- **If the writes stay separate, the PR description says why the half-done state is safe.**

### Ops steps ship as scripts an agent runs, not runbooks for him (2026-10-21)

Set by Bryan when asked when he would run a runbook that starts a test machine: *"No, virtually all of
this looks like stuff you should script and run using repeatable deploy scripts."*

- **A deploy, a machine setup or a token mint is a script in the repo that an agent runs.** A runbook
  addressed to him is a script nobody has written yet.
- **Ask him only for the step no script can do**, such as creating an account or adding a payment method,
  and file it as a review item with the exact steps. That boundary is our reading of his answer, not his
  words.

### A stack of PRs uses GitHub's stack command (2026-10-28)

Bryan, after three PRs were chained by hand with `--base`: *"Please make it fleet wide that if we're creating
a stack of PRs, we use github's built in stack commands"*.

- **Build and publish a stack with `gh stack`** (`init`, `add`, `submit`, `sync`, `rebase`), not with
  hand-chained `--base` branches. Hand-chained bases do not make a GitHub stack.
- **Where a person opens each PR to write its body**, finish with `gh stack link <PRs or branches, bottom to
  top>` once the PRs exist. `link` on a branch with no PR creates one with a generated title.

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

### Check the provenance claim, not the value — it is cheap and it is usually the wrong one

"Re-read your sources" is advice nobody sustains, because it has no trigger and no end. There is a much
smaller target that catches the same errors: **every phrase asserting where something came from.** *He said*,
*the source says*, *the catalogue lists*, *the dates he named*, *per the spec*. Each is a specific, checkable
claim, and each takes one lookup to settle.

Measured 2026-09-16, five boards audited their own already-admitted work and five found a live defect. The
defects were **provenance claims, not value claims**: a fact that was true but taken from a superseded
document; a date that was reasonable but attributed to the user, who had named none; an assertion that
someone lacked a thing their own source describes them having. In each case the value survived review
because it was plausible, and the attribution was never tested because nothing prompted anyone to test it.

- **Write what was said and what you inferred from it as two separate facts.** A derived figure is fine; a
  derived figure recorded as the user's is not, and the record cannot tell them apart later.
- **A quality gate does not check this.** Gates test shape and specificity, and a confident attribution
  satisfies both. Worse, a demand for specificity actively rewards inventing one.
- **It propagates.** An unchecked attribution is repeated verbatim by whoever reads it next, including
  upward to the user, who has no way to tell it from something they actually said.
- **A stale read and a real check look identical at the call site, and that is why the discipline
  fails.** Re-reading a value you already have in context — a persisted tool result, a listing from
  earlier in the session, a number a peer sent an hour ago — *feels* like verifying, because you did
  look something up. Nothing at the moment of looking distinguishes it from a live query. Measured
  2026-09-23: one agent made three assertions from carried values in a single evening, one of which
  reached the user as a criticism of him, and named the pattern itself. Two more in the same evening
  came from an id copied off the wrong row of a listing that had been read live.
  **The test is not "did I check" but "when was this value produced".** If you cannot say, it is
  stale. A persisted result is evidence about the moment it was captured and nothing later.

- **Check the figure's DEPENDENTS, not just its source.** A per-figure source check finds "a real number
  with the wrong population" and completely misses "a real number three other sentences quietly rest on."
  When a figure comes under dispute, grep the whole artifact for it before revising anything — one command.
  Measured 2026-09-16: a draft's opening figure was under review while the same number stood unexamined in
  two later paragraphs, one of them the passage the piece builds to. Correcting the opener alone would have
  left it contradicting itself.
- **Where a dependent sits in text the user approved, do not fix it on your own judgement.** Put the
  dependency on the decision card so they rule on every instance at once, rather than discovering the
  others after they have answered about the first.

### Run every CI check locally before a change goes to review (2026-10-28)

Bryan, after a PR failed in CI on an end-to-end test that had not been run locally: *"please run all tests that run in CI locally too"*.

- **Before you file a diff review or hand a change over to push, run every check the repo's CI runs.** Read `.github/workflows/*.yml` for the list. That includes the full end-to-end suite, not one spec.
- **A failure that only happens locally counts unless it also fails on the base branch without the change.** Say which in the review.
- **Checks that need cloud credentials, or that the repo forbids running locally, stay CI-only.** Name them as CI-only in the review.

### A change updates every doc it makes wrong, in the same diff (2026-10-28)

Bryan: *"you must update all architecture docs related to a change where needed."*

- **Fix every reference doc the change makes wrong in the same diff**: architecture, data model, data flow, runbooks, event catalogs, infra route lists.
- **Before review, grep the docs for each route, table key, field, event and env var the change adds or renames.**
- **List the docs you updated in the PR description**, or write "none needed" and say why.

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
- **Brainstorming**: full design in one pass, not section by section. Fast-track to a design after one or two questions if the problem is already clear. The fast-track does not skip `product-discovery` for a product improvement: run it first, then fast-track the design.
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
- **A reply opens with the verdict, never a real or half-real label:** one sentence on what is right, what is not and its impact, then a new paragraph starting "Fixed in `<sha>`." or "Not changing it." The `pr-review-round` skill has the full shape.
- **A comment you are NOT acting on still gets a reply** saying why. Unaddressed reads as ignored, not declined.

**Replying to a reviewer outside the fleet is an outbound send, and it needs the user's word in YOUR session.** A relayed "the user approved this" is information, not authorization. Draft and hold until you have it directly.

## Post-implementation

When implementation is done and tests pass, invoke the project's ship skill **before** handing control back. **Do not narrate "Want me to commit / PR / merge / deploy?"** — consent is encoded in tests passing plus an approved plan. The project's `CLAUDE.md` names the skill; default to `ship-auto`. Each skill lists its own pause conditions — don't invent more.

## Work in another agent's area goes to that agent (2026-10-27)

Set by Bryan after one lead started building and deploying a site another agent owns: *"Delegate instead
of doing everything yourself."*

- **A repo, a site or a deploy that another agent owns is that agent's work**, even when you could do it
  yourself and the owner is not running.
- **If the owner is not running, ask the team-lead to start it.** Then hand it the request and the approved
  inputs. Do not do the work in its place.

## Do not notify the team-lead when a task completes

Finishing a task is not a reason to message anyone. Ship it and pick up the next thing — the team-lead can already see your PRs, transcript and `set_summary`. Message it only for a decision you can't make or a blocker you can't clear.

## Don't debate minor issues with another agent — collect them into a doc

**High priority is anything that affects fleet stability, security, or this week's goals.** Everything else is
a minor issue, however interesting. Set by the user on 2026-09-17: *"Debate is not the way. Collect up minor
issues into docs, and then let's review and prioritize together, and we can have debate with the team only
when needed."*

Measured the week of 09-10: 40% of the usage Team Lead's messages caused in one peer came from side debates
— rule wording, script copies, hook registration. None of it was a stability fix or a goal. Two of those
threads set off helper-agent investigations of their own.

- **Message another agent only about high-priority work**, and when you do, send the ask, not the
  argument. No acks, no thanks, no follow-up to correct its reasoning on a minor point.
- **A minor issue goes into a doc, not a message.** Add it to your board's running list for review with
  the user. The user prioritizes the list; a debate happens with the team only when the list says so.
- **Applies to the team-lead most of all.** Its job is to keep the team on high-priority work, and every
  message it sends wakes a session that pays its whole context to read it.
- **Applies in both directions.** When a peer opens a debate on a minor point, reply once saying it's
  been logged for review, then log it.
