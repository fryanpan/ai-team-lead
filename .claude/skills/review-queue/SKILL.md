---
name: review-queue
description: Use on every workspace.new_asks or review_item.added event, when writing the morning hit list, when Bryan asks what needs him or what his priorities are, and whenever he says the queue is wrong, stale, random or unclear.
user-invocable: true
---

# Review Queue

Bryan's Home review queue is the one place he looks to see what needs him. Team Lead owns it
across every board (set 2026-10-05, restated 2026-10-16: *"it's your responsibility to keep
them that way"*).

**The failure this skill exists to stop** (2026-10-17): the queue showed school-tool asks at ranks 1–3 while goal #2 had two demo videos and a reply owed to a teammate filed nowhere, and the #1 goal's must-do (an email) sat only in the plan doc. Ranking what agents happen to file is not curation. **Start from his goals, never from the queue.**

## 1. Fill the queue from the goals down

This section is temporary. Remove it when leads file review items and keep them current without prompting. Until then, run this sweep every 2 hours, from the review-queue sweep row on the Team Lead board. For each of this week's top three goals, in plan order:

- **Ask its lead one question:** "What is waiting on Bryan that is not filed as a review
  item?" Include things outside the board: a video to watch, a Slack or Gmail reply, a doc
  edit before it goes out, a PR only he can approve.
- **The lead files each one.** If nobody owns it (a PR, an email he must send), file it on
  the Team Lead board yourself.
- **Anything in the hit list must also be a review item.** Prose in the plan doc is invisible
  to him in the queue.
- **A top-three goal with nothing waiting gets that said in the hit list**, so an empty
  section reads as checked, not skipped.

## 2. Rank strictly by goal order

- **Every item of goal N ranks above every item of goal N+1.** Only a hard deadline inside
  48 hours, or work stopped until he answers, moves an item up across goals. Say which.
- **Ranks 1–10 are only things that move this week's goals.** Fleet tooling, design questions
  and nice-to-haves rank 20 and below, or `not-this-week`.
- **Re-rank the whole queue, not just the new item.** A new ask can push everything below it.

## 3. Make each item readable on a phone

- **The headline is the ask in his words** ("Watch the two demo videos"), not a
  task story ("Bryan can…"). If it isn't, ask the owner to `revise_review_item`.
- **Every item links the thing to look at.** No link means he has to go find it.

## 4. Keep it current

- **Before each hit list, check every top-10 item is still open.** He often does the thing
  outside the queue (an email sent, a call made). Close it or have the owner close it.
- **An ask older than a day goes to its owner through the daily cleanup in section 5**, not as a
  second message from here.

## 5. Clean every board once a day

Set by Bryan, 2026-11-14: *"clean up and prioritize review items daily to make sure they still
match open weekly goals"*, with no stale ones and no duplicates. It runs from the daily cleanup row
on the Team Lead board. The run time on that row is the team-lead's choice, not his.

**Read**

- **Read every board's task items**: `list_tasks` with `includeArchived: true` and
  `fields: ["id","title","status","goal","archivedAt","reviews"]`. An item is open when it has no
  `answer` and no `review.withdrawnAt`.
- **That read misses items filed on a doc, task or goal thread.** Take those from the queue keys in
  `workspace.new_asks` lines and Home links. Say in the run's note which kinds the run covered.

**Judge**

- **An item is stale when** the weekly goal it serves is paused or dropped in the plan, its task or
  goal is archived, a later answer or event settled it, or it sits on a done task. An item on a done
  or archived task is hidden from his queue: withdraw it, or reopen the task if it still needs him.
- **Age alone is not stale.** An item older than a day that he has not answered stays. Ask its owner
  once to withdraw, revise or keep it. Record each "keep" in the run's note and do not ask again
  until the item's facts change.
- **An item is a duplicate when** two open items ask the same question, on one task, two tasks or
  two boards. Name the copy that stays: the one on the board that owns the work. Only the other
  owner is asked to withdraw.

**Act**

- **Withdraw your own stale items and your own duplicates.**
- **Send each running lead one message that names its stale and duplicate items by id.** One message
  per lead per day, including what the hit-list check in section 4 found.
- **When the lead is not running, withdraw its stale items yourself**, with the reason on each. Do
  this only for an item that meets a stale test above. `undo: true` puts one back.
- **Next run, check yesterday's messages were acted on.** An item a lead left untouched for a day
  without an answer gets a task on that lead's board.
- **A board with 10 or more open items sends its order.** Apply it when the reply arrives, in that
  turn, with `rank_review_item` and the queue key `<board>:task-review:<task>:<item>`. Goal order
  decides between goals (section 2); the lead's order decides inside its goal.
- **Re-rank the whole queue at the end of the run** with what you have, by the rules in section 2.

## 6. Match the project order to the open goals every six hours

Same day: *"make sure you update the project priority regularly to match currently open weekly
goals. Like every six hours."* It runs from the project-order row on the Team Lead board.

- **Set the board's goal order to this week's open goals in plan order.** Follow the reorder step
  of the `weekly-plan` skill for the mechanics, including the check on Home.
- **Write his spoken or chat change into the plan doc in the same turn you hear it.** The next run
  reads the plan, not your memory of the conversation.
- **A goal he paused goes below every open goal. A goal he moved goes where he said.** If he gave no
  place, choose one and record it in the plan as your choice.
- **If he reordered the goals on the board himself, keep his order** and update the plan to match.
- **When a goal moves, re-rank its items in the same run**, so no item of a lower goal stays above
  an item of a higher one.

## Before you tell him the queue is right

Read ranks 1–10 against the goal list. Each of the top three goals has an item or a stated
"nothing needed", and no item in the top 10 serves a goal below one that is missing.
