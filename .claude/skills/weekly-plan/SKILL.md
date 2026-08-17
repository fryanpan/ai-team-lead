---
name: weekly-plan
description: Set this week's goals with the user in a markdown doc bound to the Team Lead live-feedback workspace. Carry over unfinished work from last week, surface candidate goals, prioritize, estimate hands-on hours, the user picks, then expand kept goals. Each goal title is a specific measurable outcome with a due date and an estimate.
user-invocable: true
---

# Weekly Plan

The shared context the team lead and the team use to stay on the same page and move toward the user's top goals each week. Lives as a markdown doc bound to the **Team Lead live-feedback workspace**, so the user and Team Lead can both read + edit it and the user's comments arrive as channel events.

**Not Notion.** The user moved weekly planning off Notion on 2026-08-17 — it was too heavy-weight and the workspace is where the work already lives. Do not create a Notion page for the weekly plan, and do not call `notion_watch_page` for it. Existing Notion weekly pages stay where they are as history; nothing needs migrating.

**Design intent — keep it simple.** Goals pages have grown too dense to be useful. This skill's job is the opposite: surface fewer, clearer goals that you can scan in 30 seconds.

## Goal shape

Every goal has these parts. Lead every block with a **bold brief label**, and keep one type of information per bullet — never combine (see `feedback_one_info_type_per_point`). Due and Lead are separate bullets, not one line.

| Block | Format | Notes |
|------|--------|---------|
| Title | `### N. <Value-forward outcome> (~Xh) <tag>` | Names the outcome **AND why it matters** — payoff, deadline, or what it unblocks. **The estimate lives in the title** — there is no separate estimate line. e.g. `### 1. ADFA-4128 Quick Build ready for team review so it lands in CoGo before the HOPE talk (~4–5h) ✅` |
| Due | `- **Due**: <Day> YYYY-MM-DD` | its own bullet |
| Lead | `- **Lead**: <agent> · <Bryan's role>` | its own bullet — separate from Due (different info type) |
| Value | `- **Value:** <why it's worth doing this week — payoff / deadline / what it unblocks>` | one idea; use a fitting label (e.g. **Future Goal:** when it's a deadline) |
| Tasks | `- **Tasks**` then a **numbered** list of concrete steps | **these become the workspace tasks (step 9) and the user's Asana tasks (step 11)** — short, doable, one action each |

If a goal needs more than ~5–6 subtasks, it's probably two goals. Match every goal to this shape — Goal 1 on the current week's doc is the reference.

## Capacity block (required)

Every plan page opens with a capacity estimate, placed directly under the one-sentence theme and above `## Committed goals`. It is **not optional** — it's the frame the whole plan is judged against (committed hours vs. available hours).

Format: a `## Capacity: ~Xh` heading carrying the week total, then a clean per-day bullet breakdown Mon→weekend (include the weekend even when it's small), with a short context note where a day is unusually light or heavy.

```
## Capacity: ~15–21h
- Mon 2h
- Tue 2h or 8h (depends on Tim)
- Wed 4h
- Thu 3h
- Fri 2h
- Weekend 2h (kayak camping)
```

- Per-day bullets carry a short reason wherever a day deviates from a normal full day, so the total is legible at a glance.
- After the plan is set, sanity-check committed hours (sum of goal Estimates) against this total and surface the gap: under-committed leaves headroom (say what the slack is for); over-committed means something must drop or defer.

### Derive the number from the calendar — don't ask (set 2026-08-17)

**Read the user's calendar and compute the capacity yourself.** He asked for this explicitly, in place of the old "ask him for the number" step: *"please instead review my calendar each week and guess at how many hours I might have depending on whether we have any trips or other meetings booked."*

The rule, in his words:

> Assume by default 7h available each weekday to start, and subtract one for each hour between 9-5PM that I'm booked for a meeting. That assumes I have 1h free for lunch and other sundries and that we get Bea to school on time at 8:40AM every weekday and pick her up on time between 5-6PM also.

Mechanics:

- Pull **both** `fryanpan@gmail.com` (primary) and `Bryan's Work Calendar` (`4426rmvudfbaebrkmtj4jhep3g@group.calendar.google.com`) for Mon–Sun. Meetings land on either.
- Only count hours **inside 9am–5pm**. An early or evening call does not reduce the number; the 7h baseline already reserves that time.
- **A medication or self-care reminder is not a meeting.** Don't subtract for it.
- **Prorate the current day.** Planning usually happens Monday partway through — a 7h Monday that starts at noon is really ~4h. Check the clock (`date`), don't assume a full day.
- **Travel, PTO, and all-day events zero out the day.** Say which event did it.
- Show the derivation in the doc, not just the total, so he can correct the inputs rather than argue with the output.

**Where the user's own number disagrees with the rule, keep his and flag the gap.** He may know about a commitment the calendar doesn't carry — that is information you don't have, not an error to fix. Name the discrepancy in one line and move on.

## When to invoke

- **Sunday/Monday** to set up the new week.
- **Mid-week** if the user wants to revise priorities (`/weekly-plan revise`).
- After any week where major scope changed and the user wants to re-baseline.

## Steps

0. **Self-update first.** Run `/self-update` before planning — it updates Claude Code to the latest version, pulls the latest `ai-team-lead` (skills/rules/registry), and propagates both to the fleet. Week-start is the right moment (fleet usually idle). Report anything that changed, then continue planning on the current tooling.

1. **Create this week's plan doc and bind it to the workspace.**
   - Write `.claude/reviews/weekly-YYYY-MM-DD.md` — **weeks run Monday through Sunday**; the `YYYY-MM-DD` is that Monday. That directory is already gitignored, which matters because this repo is public and the plan names private projects.
   - Open with `# Week of YYYY-MM-DD (Mon M/D–Sun M/D)` and one sentence describing the theme of the week — that's the only narrative.
   - Add the **Capacity block** (see above) directly under the theme sentence, before the goals. Required on every plan.
   - Bind it: `create_review_doc(docId: "weekly-YYYY-MM-DD", path: <absolute path>, title: "Week of YYYY-MM-DD", hubWorkspaceId: <Team Lead workspace id>)`. That call both creates the review URL and files the doc under the workspace, and it auto-subscribes you to thread events — no separate `attach_doc` or `watch_doc` needed.
   - **Surface the WORKSPACE URL, not the doc's `reviewUrl`** — `http://mac-mini.<your-tailnet>.ts.net:8787/workspaces/<workspace_id>` (in `parent.txt`). The plan is meant to be read next to the goal bands and the task board; a bare `/review/<docId>` link opens the document alone, stripped of the surface the user asked us to move onto. `create_review_doc` returns a `reviewUrl` and it is tempting to paste it — don't.
   - **Once bound, never `Write`/`Edit` the .md again.** Route every later change through the live-feedback edit tools (`find_and_replace`, `set_doc_content` for a whole-doc rewrite) — a direct file write races the ~1s flush and gets silently clobbered.

2. **Pull carry-overs from last week's doc.**
   - Locate the prior `.claude/reviews/weekly-YYYY-MM-DD.md`.
   - List every goal whose sub-outcomes aren't all checked OR that's part of a multi-week sequence.
   - Seed them into a `## Candidate goals (carry-over)` section of the new doc, preserving title/due/estimate. Mark explicitly as `(carry-over)`.
   - **Check the carry-over against evidence, don't just re-list it.** A goal can look untouched on the page and have absorbed most of the week — see `## Reviewing why a week slipped` below.
   - **Also review the user's current Asana task list** (Asana reference in step 11): incomplete tasks assigned to him are carry-over candidates too, and note the stragglers to clean up — complete what's actually done, defer/reschedule what's stale — once the new plan is set. Skip family/others' tasks and Medical self-care items.

3. **Surface new candidate goals.**
   - Pull from: this week's open PRs across the fleet (`gh pr list` per repo), peer summaries (`list_peers` + recent transcripts), open tasks and decisions already on the workspace board, anything the user said this week that sounded like a commitment.
   - Add them to a `## Candidate goals (new)` section in the same goal shape.

4. **Prioritize.**
   - Sort the combined candidate list by the user's priority (1 = highest). Use the user's recent voice signals: deadlines, dependencies, things he's mentioned more than once, customer-facing > internal > polish.
   - Number them — `1.`, `2.`, etc — in descending priority.

5. **Estimate hands-on hours per goal.**
   - For each goal, write a the user-hours estimate. Note agent-time separately only if it's load-bearing for the goal (e.g., "blocked on Health Tool agent for 2h before the user can review").
   - Be honest, not aspirational. If you don't know, say `?` and ask the user.

6. **the user picks.**
   - Tell the user the capacity you derived from his calendar (see `## Capacity block`) and show the derivation — don't ask him for the number.
   - the user tags each goal: ✅ commit / ❌ drop / 📦 defer (with target week).
   - Drop the dropped + defer the deferred. Keep the page lean — only commits show in the final plan.

7. **Expand kept goals.**
   - For each ✅ goal, add the **Lead** line (owning fleet agent + the user's role) so every area has a clear point-person.
   - Add sub-outcomes only if the title isn't already self-evident.
   - Note any cross-agent dependencies on the Lead line or a one-liner (e.g., "Personal Finance agent owns the prep; the user reviews Wed").
   - Do NOT pre-fill a daily hitlist. The `daily-review` skill handles the day-by-day surface.

8. **Confirm + commit.**
   - Read the page back to the user: "Week of YYYY-MM-DD: N goals, ~Xh committed against ~Yh capacity. Top 3: ..." — always state committed-vs-capacity, not just the goal count.
   - Wait for confirmation. Adjust if needed. Then move on.

9. **Mirror the committed goals onto the workspace board.**
   - Do this only AFTER the goals are confirmed (step 8) — never before (`feedback_notion_goals_before_asana` memory; the rule survives the move off Notion, only the surface changed).
   - `set_goal_list` with one band per ✅ goal, in priority order. A goal that is really a chain of outcomes (build → decide → publish) is one parent band with subgoals, not three peers — peers hide the dependency.
   - `create_tasks` for the work **the fleet owns**, one batch, each row with a body someone not in this conversation could pick up. Use `after` / `afterEnforce` to encode the chain rather than relying on the reader to infer it.
   - **Create tasks for yourself (Team Lead) for anything handled in the ai-team-lead project.** That is the point of the board — the user should not be the only one with a task list.
   - Leave anything not tied to a committed goal in Chores.

10. **Communicate the plan to the team.**
    - Only after the user has reviewed the doc.
    - Message each peer that leads a committed goal via claude-hive `send_message`: the goal, its due date, and its dependencies. Goal and context only — no prescriptive checklists, no "report back when done" (`feedback_delegating_to_peers`, `feedback_dont_wire_in_status_reports`).
    - Spin up any owning agent that isn't running; spin it back down when its task is done.

11. **Refresh the user's own Asana day list.**
    - **Asana remains the user's personal "what do I work on today" surface.** The workspace board carries the team's work; Asana carries his. Keep them from drifting — every Asana task should trace to a committed goal.
    - For each ✅ goal, create short, doable Asana tasks: imperative name, assigned to the user, `due_on` a day that has capacity — spread across the week per the Capacity block, don't pile onto one day — and a 1-line note with the relevant link.
    - **Reconcile, don't duplicate.** Update/complete tasks that already exist; delete tasks belonging to dropped goals; clean up the stragglers flagged in step 2.
    - **Leave alone:** family/others' tasks (shared volunteer and family projects) and the user's Medical self-care items.
    - **Asana reference:** workspace Octoturtle `ASANA_WORKSPACE_GID` · project "Bryan's Projects" `ASANA_PROJECT_GID` · Bryan (assignee) `ASANA_ASSIGNEE_GID`. Non-premium plan → use `asana_get_tasks` (`search_tasks` is gated). The `daily-review` skill keeps this list current each morning.

## What to avoid

- Don't draft a comprehensive plan upfront with all 5+ goals + sub-goals + daily hitlist + retrospective + infrastructure interleave + training table. That's the failure mode this skill replaces.
- Don't bundle the prior week's retro into the new week's page. Retros stay on the prior week's page.
- Don't pre-compute infrastructure / housekeeping items unless they're load-bearing for a committed goal.
- Don't expand dropped or deferred goals.
- Don't invent goals to fill the page. Fewer is better.

## Reviewing why a week slipped

When the user asks why he's behind — or whenever a goal carries over a second time — **measure where the hours went before accepting anyone's account of it, including his.** A plan page records intent, not outcome, and the goal that ate the week is often the one that looks quiet.

- **Read the transcripts, not the plan page.** For each project, the session transcript under `~/.claude/projects/<encoded-cwd>/*.jsonl` is the only record of what actually happened.
- **Count only turns the user typed.** Filter out `<channel>`, `<task-notification>`, `<teammate-message>`, `<agent-message>`, `Another Claude session sent a message:` and skill/system injections. Left unfiltered these inflate a week 2–3× and make an idle project look busy.
- **Report shares, not absolute hours.** The hands-on model in `plugin/team-lead-fleet/skills/retro/scripts/analyze_transcript.py` charges the user reading time for output produced while he was elsewhere, so its totals overshoot real capacity. The relative split between projects is the trustworthy part.
- **Check PR activity as a cross-check, not as the measure.** A repo with zero PRs in the window can still have absorbed the week — deep investigation, benchmarking, and review all leave no PR trail. Never conclude "nothing happened here" from an empty `gh pr list`.
- **Separate an estimate miss from an execution miss.** A goal that came in at 140% of its estimate and still isn't done is an estimating problem; a goal that never got started is a prioritization problem. They need different fixes, and conflating them produces advice that helps neither.
- **Name dependencies the plan hid.** Two goals due the same week where one gates the other were never two goals. That is a planning defect worth fixing in the next plan, not a performance problem.

## Daily life of the plan

Once the plan is set, this doc is the team's shared anchor for the week:
- Team Lead is auto-subscribed from `create_review_doc` — the user's comments fire as channel events on the doc's threads.
- The `daily-review` skill writes a fresh `.claude/reviews/YYYY-MM-DD.md` each day; it pulls the goal list from this doc to anchor priority order.
- **The workspace board carries the team's tasks**; Asana carries the user's own day list. The `daily-review` skill re-syncs Asana every morning.
- Apply the user's comments with the live-feedback edit tools, never by writing the file.
- When a goal completes, check off all its sub-outcomes and move it to a `## Done` section at the bottom.
- When a goal slips, update the due date in place and note why in one line.
