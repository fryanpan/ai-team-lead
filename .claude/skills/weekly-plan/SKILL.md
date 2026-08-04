---
name: weekly-plan
description: Set this week's goals with the user in a Notion page the team-lead is listening to. Carry over unfinished work from last week, surface candidate goals, prioritize, estimate hands-on hours, the user picks, then expand kept goals. Each goal title is a specific measurable outcome with a due date and an estimate.
user-invocable: true
---

# Weekly Plan

The shared context the team lead and the team use to stay on the same page and move toward the user's top goals each week. Lives in Notion so the user and Team Lead can both read + edit it; Team Lead subscribes via notion-channel so comments come in as channel events.

**Design intent — keep it simple.** Goals pages have grown too dense to be useful. This skill's job is the opposite: surface fewer, clearer goals that you can scan in 30 seconds.

## Goal shape

Every goal has these parts. Lead every block with a **bold brief label**, and keep one type of information per bullet — never combine (see `feedback_one_info_type_per_point`). Due and Lead are separate bullets, not one line.

| Block | Format | Notes |
|------|--------|---------|
| Title | `### N. <Value-forward outcome> (~Xh) <tag>` | Names the outcome **AND why it matters** — payoff, deadline, or what it unblocks. **The estimate lives in the title** — there is no separate estimate line. e.g. `### 1. ADFA-4128 Quick Build ready for team review so it lands in CoGo before the HOPE talk (~4–5h) ✅` |
| Due | `- **Due**: <Day> YYYY-MM-DD` | its own bullet |
| Lead | `- **Lead**: <agent> · <Bryan's role>` | its own bullet — separate from Due (different info type) |
| Value | `- **Value:** <why it's worth doing this week — payoff / deadline / what it unblocks>` | one idea; use a fitting label (e.g. **Future Goal:** when it's a deadline) |
| Tasks | `- **Tasks**` then a **numbered** list of concrete steps | **these become the Asana tasks** (step 9) — short, doable, one action each |

If a goal needs more than ~5–6 subtasks, it's probably two goals. Match every goal to this shape — Goal 1 on the current week's page is the reference.

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

- The bold total is the week's realistic hands-on hours — the user's number; ask if unknown, don't guess.
- Per-day bullets carry a short reason wherever a day deviates from a normal full day, so the total is legible at a glance.
- After the plan is set, sanity-check committed hours (sum of goal Estimates) against this total and surface the gap: under-committed leaves headroom (say what the slack is for); over-committed means something must drop or defer.

## When to invoke

- **Sunday/Monday** to set up the new week.
- **Mid-week** if the user wants to revise priorities (`/weekly-plan revise`).
- After any week where major scope changed and the user wants to re-baseline.

## Steps

0. **Self-update first.** Run `/self-update` before planning — it updates Claude Code to the latest version, pulls the latest `ai-team-lead` (skills/rules/registry), and propagates both to the fleet. Week-start is the right moment (fleet usually idle). Report anything that changed, then continue planning on the current tooling.

1. **Find or create this week's Notion page.**
   - Search for an existing "Weekly Plans" parent (`notion-search "Weekly Plans"`). If none, ask the user for the parent URL once and stash it in `.claude/skills/weekly-plan/parent.txt` (gitignored).
   - Create child page titled `Week of YYYY-MM-DD (Mon M/D–Sun M/D)` — **weeks run Monday through Sunday**; the `YYYY-MM-DD` is that Monday. One sentence at the top describing the theme of the week — that's the only narrative.
   - Add the **Capacity block** (see above) directly under the theme sentence, before the goals. Required on every page.
   - Call `notion_watch_page` on the new page with `include_descendants: false` so the user's comments arrive as channel events.

2. **Pull carry-overs from last week's page.**
   - Locate the prior `Week of YYYY-MM-DD` page.
   - List every goal whose sub-outcomes aren't all checked OR that's part of a multi-week sequence.
   - Seed them into a `## Candidate goals (carry-over)` section of the new page, preserving title/due/estimate. Mark explicitly as `(carry-over)`.
   - **Also review the user's current Asana task list** (Asana reference in step 9): incomplete tasks assigned to him are carry-over candidates too, and note the stragglers to clean up — complete what's actually done, defer/reschedule what's stale — once the new plan is set. Skip family/others' tasks and Medical self-care items.

3. **Surface new candidate goals.**
   - Pull from: this week's open PRs across the fleet (`gh pr list` per repo), peer summaries (`list_peers` + recent transcripts), Linear/Notion items the user flagged, anything the user said this week that sounded like a commitment.
   - Add them to a `## Candidate goals (new)` section in the same goal shape.

4. **Prioritize.**
   - Sort the combined candidate list by the user's priority (1 = highest). Use the user's recent voice signals: deadlines, dependencies, things he's mentioned more than once, customer-facing > internal > polish.
   - Number them — `1.`, `2.`, etc — in descending priority.

5. **Estimate hands-on hours per goal.**
   - For each goal, write a the user-hours estimate. Note agent-time separately only if it's load-bearing for the goal (e.g., "blocked on Health Tool agent for 2h before the user can review").
   - Be honest, not aspirational. If you don't know, say `?` and ask the user.

6. **the user picks.**
   - Tell the user how many hands-on hours are realistic this week (default 12-15h unless he's said otherwise; ask if unclear).
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

9. **Break committed goals into Asana tasks — the week's primary task surface.**
   - **Asana is the user's primary "what do I work on" surface.** Notion holds the high-level goals (this page); Asana holds the detailed tasks under each committed goal, dated across the week. Do this only AFTER the goals are confirmed (step 8) — never before (`feedback_notion_goals_before_asana` memory).
   - For each ✅ goal, create short, doable Asana tasks: imperative name, assigned to the user, `due_on` a day that has capacity — spread across the week per the Capacity block, don't pile onto one day — and a 1-line note with the relevant link.
   - **Reconcile, don't duplicate.** Update/complete tasks that already exist; delete tasks belonging to dropped goals; clean up the stragglers flagged in step 2.
   - **Leave alone:** family/others' tasks (Good Government project, Joanna / Louise) and the user's Medical self-care items.
   - **Asana reference:** workspace Octoturtle `1211390582921761` · project "Bryan's Projects" `1212817868300931` · Bryan (assignee) `3708345653658`. Non-premium plan → use `asana_get_tasks` (`search_tasks` is gated). The `daily-review` skill keeps this list current each morning.

## What to avoid

- Don't draft a comprehensive plan upfront with all 5+ goals + sub-goals + daily hitlist + retrospective + infrastructure interleave + training table. That's the failure mode this skill replaces.
- Don't bundle the prior week's retro into the new week's page. Retros stay on the prior week's page.
- Don't pre-compute infrastructure / housekeeping items unless they're load-bearing for a committed goal.
- Don't expand dropped or deferred goals.
- Don't invent goals to fill the page. Fewer is better.

## Daily life of the page

Once the plan is set, this page is the team's shared anchor for the week:
- Team Lead watches it via `notion_watch_page` — the user's edits/comments fire as channel events.
- The `daily-review` skill writes a fresh `.claude/reviews/YYYY-MM-DD.md` each day; it pulls the goal list from this page to anchor priority order.
- **Asana carries the day-to-day tasks** derived from these goals (the primary task surface); the `daily-review` skill re-syncs Asana's per-day list every morning.
- When a goal completes, check off all its sub-outcomes and move it to a `## Done` section at the bottom.
- When a goal slips, update the due date in place and note why in one line.
