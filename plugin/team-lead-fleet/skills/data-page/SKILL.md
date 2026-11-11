---
name: data-page
description: Use when building a page, chart, dashboard or write-up whose value is its numbers — spend breakdowns, usage analysis, metrics reports, research findings with figures — for Bryan or anyone he will share it with.
user-invocable: true
---

# Data Page or Analysis

**Read `<project>/.claude/workflow-overrides/data-page.md` first, if it exists.** It replaces or adds steps by name (for example a project's own reconcile script). Every step it does not name runs as written here.

## Size it first

**A fix Bryan named in a comment** (a label, a unit, a colour): make it, re-render the changed view at 430 wide and one desktop width, reply on the thread. Nothing else applies.

**A fix that changes a figure** also runs `pre-send`'s one-figure check on it.

**Anything else** runs every step below.

## Steps, and the done-when line each one leaves

Put these on the task row as done-when lines when you start (`rewrite_task` keeps existing line ids), and report each with `report_done_when`. A line is `met` only with the proof named here.

| # | Step | Done-when line | Proof |
|---|---|---|---|
| 1 | Rough pass with Bryan: quick exploratory charts, the figures that stand out, the story in three sentences. File it as a review item and **do not build the polished version until he answers.** | Story agreed with Bryan | The review item, and his answer |
| 2 | Build the polished version against the agreed story. | Built | The page link |
| 3 | Run `pre-send` (sources, pipeline, figures, a blind skeptic, verify) and, in parallel, a `ux-review` walk by a separate agent that the insights read clearly and the chart rules below hold. A defect sends the page back to step 2. | Sources, figures and clarity reviewed | The pre-send result and the ux-review report, with defects marked fixed |
| 4 | For a page: render at 1920, 1180 and 430 wide. Fails on a sideways scroll at 430. A markdown write-up skips this line. | Renders at three widths | The three screenshots |
| 5 | Hand off: file the review item. | Filed for review | The review item link |

**Step 1 is a step of the work, not a permission request.** An approved task does not skip it, and neither does Bryan being asleep: file the item, pick up other work, and resume when he answers. If his answer will land after the deadline, say that on the item.

**Your own spot-check does not satisfy step 3.** Recomputing totals by a second method is worth doing, but in 4 of 4 measured runs where the builder's own checks passed, a figures-only reviewer still found defects. The proof is the separate reviewers' reports.

## Data that updates (Bryan, 2026-11-09)

*"if we build any data tool that is meant to have updated data that it has a update interval that's been set, that it does update, and that there's a data quality check that checks that the update happened properly. Plus, each report that has data that gets updated should have up top a very short blurb that just says when the data was last updated."*

- **Before the first draft, say whether the data updates.** A frozen snapshot needs no interval; say so on the task.
- **If it updates:** name the interval, at most weekly, and the scheduled job that runs it. A freshness check fails when the newest data is older than the interval allows, and has been seen failing once.
- **The page opens with one short "Data updated <date>" line**, the date the data was read.
- **Before hand-off, give the last scheduled run's log line** with the step 3 result.

## Chart rules (Bryan, 2026-11-03)

Every chart meets these. The step 3 `ux-review` walk checks them.

- **Scale every axis so its trends are readable.** Choose log or linear on purpose. When the series in small multiples differ a lot in size, give each panel its own y-scale. His words: *"the vertical axis is scaled in a way where you can barely see the differences."*
- **Size each panel so its slopes are visible**, near 45°, and choose the grid layout to fit that. Do not default to one row per category.
- **Never use colour without a key.** *"No color use in a chart without legend — that's bad."* Prefer coloured labels that point straight at their category; use a legend only when there is no room for labels.
- **On a percent chart, put "%" on the axis ticks and on the value labels**, *"so it's clear these are percentages. Without having to look up other info."*

## No workspace board?

Put the same lines and proofs in the hand-off message or the PR body, one line each.
