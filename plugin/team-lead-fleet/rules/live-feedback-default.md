---
alwaysApply: true
---

# Workspaces as the Default Review Surface

The plugin's skills carry all the mechanics and the user's standing preferences — `claude-workspaces:working-in-a-workspace`, `:editing-review-docs`, `:diff-review`, `:embedding-widget`, `:project-docs-layout`. This rule is only what has to fire *before* you would think to invoke one.

## Bind it, don't send a path

When you want the user to review a markdown doc, a dev server or an interactive preview, **put it on the workspace** rather than sending a file path or a bare URL.

- **Applies to:** anything you want his voice, structure or content pass on — posts, plans, audits, retros, design and decision docs — plus any dev-server URL or mockup, and anything where you want comment-level input.
- **Skip for:** one-line acks, code review (the PR diff is canonical), your own notes.
- **Once a doc is bound, never Write/Edit the `.md`.** The plugin flushes the live doc to disk about a second after every change and silently clobbers filesystem edits.
- **If this session has a `workspaceId`, or someone said "the board is your task list", read `claude-workspaces:working-in-a-workspace` before your first piece of work.** It is the contract, and nothing else will tell you to open it. This is a once-per-session read, so it does not displace the `post_reply`-first rule below on a turn where comments are arriving — ack, then read, then work.

## A dev server you put in front of him live-reloads

Set by Bryan, 2026-09-22: *"Can you please have the dev server page live reload, so i don't need
to refresh?"*, then *"And do that for all local dev servers"*. It applies to every peer, not to the
one project that prompted it.

- **If you bind a dev-server URL for review, the page reloads itself when the build changes.**
  Asking him to hit refresh to see whether you fixed something is the failure; he is reviewing on a
  phone as often as not.
- **The shape is small and needs no dependency.** Serve the build output, watch the source dirs,
  rebuild on change, inject a short `EventSource` snippet into every HTML response, and broadcast a
  reload when the build finishes.
- **This is a property of the review surface, not of one stack.** A framework that already does it
  satisfies the rule; a static server that does not is the thing to fix before you send the link.

## Ack on the thread first, fix after

**Doing the work is not answering the comment.** Measured across the fleet over 48h: of 245 comments, 19 were acted on and never replied to. On the thread those are indistinguishable from ignored, and they are what produces "are you listening to my comments?".

- **First action of the turn is `post_reply`** — "on it", "noted, fixing now", "fixed". Then the edit.
- **Per thread, not one bulk reply.** A single "addressing all three" is a fallback, not the shape.
- **Shorter turns while comments are flowing.** Channel events arrive at turn boundaries, so ten edits batched into one turn means he sees nothing until it ends.
- **A comment you are NOT acting on still gets a reply** saying why. Same rule as PR review feedback.

## One thread, one answerer — and the deliverable goes in the doc

Every peer watching a doc receives the same `thread.created` event, and each one reads it as an ask addressed to it. Nobody sees the others' replies before writing. Measured 2026-09-04: a request for "2-3 alternative versions of a few sentences" drew **six drafts from two agents, then four more rounds of the two agents adjudicating each other**, none of it in the doc. Bryan's reply was *"Where are my options… you're continuing this huge thread that we've already discussed ad nauseum."*

- **The doc's lead answers. Everyone else stays out**, however good their take is. If there is no lead, the peer that owns the underlying repo answers.
- **Before replying to a thread, read the thread.** If a peer has already answered, you are done — an addition is only warranted when you hold a fact they got wrong, and then it is one paragraph, not a second draft set.
- **Never reply to correct another agent's reasoning.** That conversation is between the two of you and it is running in the user's review surface. Take it to `send_message`. The line against the bullet above: a **fact** they got wrong misinforms the user and is worth one paragraph on the thread; their **reasoning** being weak affects only how they got there, and the user did not ask to watch that.
- **A request for options is a request for options.** They go in the doc body where he can edit them; the thread gets one line saying where they are. Analysis of which option is better is not what was asked for.
- **Answer the count he named.** Three requested means three delivered — not three each.

## A row that says done carries its evidence in the comments

`post_status` writes to the Activity tab, which nobody browsing a board opens. A row marked done with its reasoning only there is indistinguishable from one closed without work. Bryan, 2026-09-04: *"you marked a bunch of tickets done with no evidence in the comments — please make sure that you have evidence in the comments about what your research was, either in a comment or link to a doc."*

- **Comment on the row before you transition it to done** — what you did, what you found, and a link to the artifact. `post_status` is in addition to that, never instead of it.
- **A link is enough when the artifact carries the reasoning**, but put the conclusion in the comment too so the row reads without the click.
- **A task whose deliverable IS a doc or a comment does not close on your own judgement.** Post the deliverable, say on the row that it is ready to read, and leave the row open for him. Marking it done skips the only review a written artifact ever gets.
- **That is not a pause.** Pick up the next task immediately — you are leaving a row open, not waiting on an answer.
- **A thread he resolved is not an artifact that is ready.** Resolving is the cheapest signal a person can send — one click, no content — and he often keeps editing for an hour afterwards. Read the file before you report the state. Measured 2026-09-04: this produced two wrong "ready to send" claims in one day.

## A dispatched build carries its row's id, or the row cannot be traced to the work

A board row is only a record of work if something joins it to the branch, the PR and the subagent that did
it. Nothing does that automatically, and every step where it could be written down is a step a lead or a
builder does from memory.

Measured on the week of 2026-09-07 by the peer that instruments the fleet's weekly numbers, in one
lead's session: **of 101 subagent
lanes, 7 linked clearly to a board task, 26 only because the lane's worktree name happened to appear in some
row's text (18 matching one row, 8 matching several), and 68 to nothing at all.** Dispatch prompts named a
task id in **4 of 264**. The board's rows linked or named **125 PRs against 264 merged**. So for
most of the week's work, the row and the work that satisfied it could not be joined by anyone reading
either one.

**Three places to write the id. Do all three.** Any one of them makes the join possible; all three make it
survive whichever step a builder does for itself.

1. **The dispatch prompt's first line names the row** — `Board task: t-…`. This is the cheapest of the
   three and the one that was almost never done.
2. **The PR body names the row, and the PR URL is attached back to it.** The body is what a human reads;
   the attachment is what a query reads. Neither substitutes for the other.
3. **The worktree is named after the row** (`.claude/worktrees/t-…`). The lead creates the worktree 32
   times in 42, so this is usually the lead's to get right. It is also the step with the most evidence
   behind it: the worktree name was the *only* thread joining a quarter of those lanes to a row, and it
   did that by accident — 8 of the 26 matched several rows, which is a guess rather than a join.

- **This is for work that came off a row.** A spike, a sweep or a one-off investigation has no row and
  needs no id; inventing one to satisfy the rule is worse than leaving the lane unlabelled.
- **Write the id, not a description of the row.** A title drifts when the row is reworded and a paraphrase
  never matched in the first place. The id is the only part that survives an edit.
- **The verbs and paths above will get renamed eventually; the obligation will not.** What the rule asks
  for is that the row, the branch and the PR each carry a pointer to the others — implement it with
  whatever the current tools call it.

## An answer that defers without a date leaves nothing behind

An ask answered with "snooze until next Tuesday", "wait a week", "defer for a month", "we can defer" or
"not until signed" is closed as far as the answerer is concerned, and still sits in `todo` looking live.
Nothing brings it back. Measured 2026-09-16 on one board: **five in a row, none with a date armed**, the
oldest four days past the day it named.

- **When an answer defers, arm the schedule in the same turn.** The answer is not complete until the row
  has a date on it; treat a dateless deferral the way you would treat a task with no owner.
- **Where the answer names no date, say so on the row.** A derived date is fine — a derived date recorded
  as the user's is not. Write what they said and what you inferred from it as two separate facts. On that
  same board a reasonable Dec 1 stood where the user had named nothing, and was reported upward as his.
- **This is the mirror of "an answer closes an item and the new wait lands nowhere."** Both come from
  treating the reply as the end of the exchange. Ask what the row is waiting for now, every time.

## Match BOTH channel-source spellings — transitional, delete when the fleet is fully renamed

**Anything matching on the channel source must accept `source="live-feedback"` AND `source="claude-workspaces"`.** A session emits the new string only once restarted onto the new bundle, so respawned and un-respawned peers coexist. A matcher keyed to one spelling goes silently deaf to half the fleet, indistinguishable from nobody having commented. **Match on the presence of `doc_id` / `thread_id` instead** where you can; those did not change.

Same for anything else keyed to the old name: tool prefix `mcp__plugin_claude-workspaces_claude-workspaces__*`, skills `claude-workspaces:*`, install key `claude-workspaces@claude-workspaces`. Env vars gain `CW_*`; old `FEEDBACK_*` / `LF_*` spellings are permanently dual-read.

## An ask written as prose is invisible to every queue

A question you write into a doc's **body** is not an item. It does not reach `needs:`, the idle nudge or the
stalled nudge — not because a filter is wrong, but because there is nothing filed for them to return. The doc
looks answered, the row looks healthy, and the question sits in prose the user has already scrolled past.

Measured 2026-09-16 on one board: a question put into a review doc's body on 09-04 went **12 days unread**,
and the email it was gating was still unsent when the audit found it. The same sweep found the sibling case —
a comment on a row carrying a `schedule` field, invisible at the time of that measurement for the same
reason from the other direction.

- **If you need an answer, file a review item.** Prose is for the deliverable; the queue is for the ask. A
  paragraph beginning "should we…" inside a doc body is the failure, however clearly it is written.
- **A doc the user has already reviewed is the worst place to add a question.** Resolution is the signal they
  have stopped reading; anything added after it needs its own item to be seen at all.
- **Check both directions when you audit.** "What am I waiting on from them" is the easy half. "What did they
  ask me that I never closed" is the half that ages invisibly, and neither detector covers it.

**Five things are invisible to every status-keyed detector**, and they share one cause — the detector walks
task rows and keys on their state, and none of those states is "has an unanswered question":

1. **A row carrying a `schedule` field.** Whether the ordinary ask path reaches one has already flipped
   twice — unreachable on the morning of 2026-09-16, reachable that evening, and a third change was in
   flight the next day. Trust neither answer: open a deferred row and read it.
2. **A row already marked done.** The user uses closed rows for follow-ups, and a comment on one is as
   unreachable as a comment on a rule.
3. **A question written as prose**, per above — nothing is filed, so there is nothing to return.
4. **A question on a doc with no task behind it.** No status to age, no assignee, no rank: every rung of the
   ladder walks rows, so nothing in the path can see it from either direction. Measured 2026-09-17 on this
   board — 9 docs with open threads and no row, 57 threads, the oldest idle 12 days; the first one opened
   held a 23-day-old commitment of mine whose condition had been met weeks earlier. **The doc-cleanup job
   makes it worse**, reading an idle doc and asking its owner to delete it, with open threads counted only
   as a force-delete warning. Sweep docs separately from rows, and read the last comment's author.

5. **An item on a row still in the un-vetted state.** Filing the item is only half of making an ask
   visible; the row has to be in a state the queue walks. A row nobody has vetted is held out of the
   ready queue by design, and everything hanging off it is held out with it — so a well-shaped,
   one-tap question sits exactly as unseen as the doc comment it replaced. Measured 2026-09-23: an
   approval gating a week's goal was moved out of a doc body onto a row, revised five times to pass
   the quality gate, admitted — and still returned nothing, because the row had never been vetted.
   **Read the row's own state, and get a positive control.** The state is the fact; a filter that
   *sounds* like it asks "what needs an answer" may be reading a field somebody has to set by hand,
   in which case it returns empty on a board where nobody sets it and proves nothing either way. The
   control is the cheap half and it is the stronger evidence: has anything ever filed on this row
   come back answered? If yes, items on it reach the reader; if nothing on it has ever been answered,
   do not assume the row is the reason, but do not assume it is not.
   **Leaving the un-vetted state may not be enough on its own.** An enforced dependency on another
   un-vetted row blocks the transition, so a row can be vetted and still stuck behind one that is
   not — vet the chain, not the row. Sweep your own un-vetted rows periodically; they accumulate
   silently, and on that board six of ten were sitting there, which also left it with no active row
   at all.

**Start from the detector's own output, not from a fresh sweep.** When a stall frame names items, it has
already done the join you would otherwise redo by hand. Re-run its predicate against the docs it named
before widening — a truncated list hides items, not docs, and several items commonly sit on one doc.

- **The identifying test is who spoke last**, not thread count and not quiet time. A timestamp moves for an
  agent reply too, so it ranks candidates and never identifies one. Read the last comment's author.
- **Any board-wide cache on this machine spans every board.** Joining it to your own task list is not an
  optimisation, it is the thing that stops you reading someone else's rows — and unjoined it returns a
  number large enough to look like a finding. Measured 2026-09-17: 1,008 entries, answering nothing.
- **Widening is the expensive wrong move when the narrow answer feels incomplete.** The item that looked
  missing was on a doc already named.

**Never write another repo's internals into a fleet rule.** This section has twice held one — a source file
with a bucket name and an evaluation order, and later an on-disk cache path with its field types. Both were
accurate when written; the first was false within a day because the plugin shipped a PR that moved the
check, and the second sent a reader to a machine-wide directory that answered nothing. Nothing in this repo
could have noticed either. A rule
loaded by every peer on every SessionStart then re-teaches the stale fact indefinitely, which is worse than
a one-time wrong conclusion.

- **State the hazard, not the mechanism.** "A detector keyed on a row's state cannot see an unanswered
  question" stays true across their refactors; "`keep-moving.ts` buckets it as `scheduled-rule`" does not.
- **A behaviour you measured is a dated observation, not a rule.** Write the date next to it so the next
  reader knows what it is, and check it rather than quoting it.
- **The owning agent is not obliged to keep our rules current**, and asking it to is the wrong fix. The
  dependency is the defect.
