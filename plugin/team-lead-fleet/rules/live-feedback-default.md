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
- **If this session has a `workspaceId`, or someone said "the board is your task list", read `claude-workspaces:working-in-a-workspace` before doing anything else.** It is the contract, and nothing else will tell you to open it.

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
- **Never reply to correct another agent's reasoning.** That conversation is between the two of you and it is running in the user's review surface. Take it to `send_message`.
- **A request for options is a request for options.** They go in the doc body where he can edit them; the thread gets one line saying where they are. Analysis of which option is better is not what was asked for.
- **Answer the count he named.** Three requested means three delivered — not three each.

## A row that says done carries its evidence in the comments

`post_status` writes to the Activity tab, which nobody browsing a board opens. A row marked done with its reasoning only there is indistinguishable from one closed without work. Bryan, 2026-09-04: *"you marked a bunch of tickets done with no evidence in the comments — please make sure that you have evidence in the comments about what your research was, either in a comment or link to a doc."*

- **Comment on the row before you transition it to done** — what you did, what you found, and a link to the artifact. `post_status` is in addition to that, never instead of it.
- **A link is enough when the artifact carries the reasoning**, but put the conclusion in the comment too so the row reads without the click.
- **A task whose deliverable IS a doc or a comment does not close on your own judgement.** Post the deliverable, say on the row that it is ready to read, and leave the row open for him. Marking it done skips the only review a written artifact ever gets.
- **That is not a pause.** Pick up the next task immediately — you are leaving a row open, not waiting on an answer.
- **A thread he resolved is not an artifact that is ready.** Resolving is the cheapest signal a person can send — one click, no content — and he often keeps editing for an hour afterwards. Read the file before you report the state. Measured 2026-09-04: this produced two wrong "ready to send" claims in one day.

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

**Four things are invisible to every status-keyed detector**, and they share one cause — the detector walks
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

**Enumerate them from the plugin's own on-disk index rather than by sweeping the board.** Each doc has
`~/Library/Application Support/claude-workspaces/data/task:<taskId>.index.json` carrying
`threads: {open, total}` and `lastThreadActivityAt`. Filter to rows with an open thread and you pay
`list_threads` only on those — one board went 25 rows to 4. Three things to get right:

- **`lastThreadActivityAt` is epoch milliseconds**, not an ISO string, and it is absent on rows that never
  had a thread.
- **The index carries no status, no schedule and a placeholder title.** Join against your own board's task
  list; the directory is machine-wide, so an unjoined sweep reads other boards' rows.
- **Quiet time is not unanswered time.** The timestamp moves for an agent reply too, so it ranks candidates;
  reading the last comment's author is still what identifies an ask.

**Never write another repo's internals into a fleet rule.** The bullet above named a source file, a bucket
name and an evaluation order in the plugin's code. It was accurate when written and false within a day,
because the plugin shipped a PR that moved the check — and nothing in this repo could have noticed. A rule
loaded by every peer on every SessionStart then re-teaches the stale fact indefinitely, which is worse than
a one-time wrong conclusion.

- **State the hazard, not the mechanism.** "A detector keyed on a row's state cannot see an unanswered
  question" stays true across their refactors; "`keep-moving.ts` buckets it as `scheduled-rule`" does not.
- **A behaviour you measured is a dated observation, not a rule.** Write the date next to it so the next
  reader knows what it is, and check it rather than quoting it.
- **The owning agent is not obliged to keep our rules current**, and asking it to is the wrong fix. The
  dependency is the defect.
