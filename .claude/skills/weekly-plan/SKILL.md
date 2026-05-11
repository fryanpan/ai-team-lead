---
name: weekly-plan
description: Set this week's goals with the user in a Notion page the team-lead is listening to. Carry over unfinished work from last week, surface candidate goals, prioritize, estimate hands-on hours, the user picks, then expand kept goals. Each goal title is a specific measurable outcome with a due date and an estimate.
user-invocable: true
---

# Weekly Plan

The shared context the team lead and the team use to stay on the same page and move toward the user's top goals each week. Lives in Notion so the user and Team Lead can both read + edit it; Team Lead subscribes via notion-channel so comments come in as channel events.

**Design intent — keep it simple.** Goals pages have grown too dense to be useful. This skill's job is the opposite: surface fewer, clearer goals that you can scan in 30 seconds.

## Goal shape

Every goal has four parts. Each is required.

| Field | Format | Example |
|------|--------|---------|
| Title | Specific measurable outcome (the headline IS the outcome — no vague verbs like "work on") | `Live-feedback blog post published to your-blog.com — 1500 words merged` |
| Due | `Due: <Day> YYYY-MM-DD` | `Due: Wed 2026-05-13` |
| Estimate | the user-hours hands-on | `Estimate: 3h the user-time` |
| Sub-outcomes (only if needed) | `[ ]` checklist of the concrete pieces that prove the title was hit | `[ ] voice pass · [ ] hero image · [ ] PR merged` |

If a goal needs more than ~5 sub-outcomes, it's probably two goals.

## When to invoke

- **Sunday/Monday** to set up the new week.
- **Mid-week** if the user wants to revise priorities (`/weekly-plan revise`).
- After any week where major scope changed and the user wants to re-baseline.

## Steps

1. **Find or create this week's Notion page.**
   - Search for an existing "Weekly Plans" parent (`notion-search "Weekly Plans"`). If none, ask the user for the parent URL once and stash it in `.claude/skills/weekly-plan/parent.txt` (gitignored).
   - Create child page titled `Week of YYYY-MM-DD` (Monday). One sentence at the top describing the theme of the week — that's the only narrative.
   - Call `notion_watch_page` on the new page with `include_descendants: false` so the user's comments arrive as channel events.

2. **Pull carry-overs from last week's page.**
   - Locate the prior `Week of YYYY-MM-DD` page.
   - List every goal whose sub-outcomes aren't all checked OR that's part of a multi-week sequence.
   - Seed them into a `## Candidate goals (carry-over)` section of the new page, preserving title/due/estimate. Mark explicitly as `(carry-over)`.

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
   - For each ✅ goal, add sub-outcomes only if the title isn't already self-evident.
   - Add a one-line note for any cross-agent dependencies (e.g., "Personal Finance agent owns the prep; the user reviews Wed").
   - Do NOT pre-fill a daily hitlist. The `daily-review` skill handles the day-by-day surface.

8. **Confirm + commit.**
   - Read the page back to the user: "Week of YYYY-MM-DD: N goals, X hands-on hours total. Top 3: ..."
   - Wait for confirmation. Adjust if needed. Then move on.

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
- When a goal completes, check off all its sub-outcomes and move it to a `## Done` section at the bottom.
- When a goal slips, update the due date in place and note why in one line.
