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

## A workspace URL is not a durable address

**The review URL embeds a workspace id that changes when the workspace is recreated.** Every link written against the old one dies silently — no error, no redirect, dead for you as well as the reader.

- **In a durable doc** — committed, exported, or sent to someone — cite relative repo paths or GitHub URLs.
- **In live chat** — a message, a thread reply, a hand-off — the URL is correct and is what he wants, because he's clicking it now.

## Match BOTH channel-source spellings — transitional, delete when the fleet is fully renamed

**Anything matching on the channel source must accept `source="live-feedback"` AND `source="claude-workspaces"`.** A session emits the new string only once restarted onto the new bundle, so respawned and un-respawned peers coexist. A matcher keyed to one spelling goes silently deaf to half the fleet, indistinguishable from nobody having commented. **Match on the presence of `doc_id` / `thread_id` instead** where you can; those did not change.

Same for anything else keyed to the old name: tool prefix `mcp__plugin_claude-workspaces_claude-workspaces__*`, skills `claude-workspaces:*`, install key `claude-workspaces@claude-workspaces`. Env vars gain `CW_*`; old `FEEDBACK_*` / `LF_*` spellings are permanently dual-read.
