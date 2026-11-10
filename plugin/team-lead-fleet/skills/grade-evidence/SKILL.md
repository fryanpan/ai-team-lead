---
name: grade-evidence
description: Use before a model, estimate or doc applies a study's effect or cost — a new input, a changed low, central or high, a new source for a rate or effect size — and whenever a local data series is about to set an effect size.
user-invocable: true
---

# Grading evidence before a number uses it

**Read `<project>/.claude/workflow-overrides/grade-evidence.md` first, if it exists.** It names the project's evidence log, its schema and its check command, and may add domain ratings.

## What it is for

A model sent out for public comment leaned on studies nobody had checked would transfer: a citywide result applied to one small zone, percentage points read as percent, seats read as riders, a volunteer program's cost paired with a paid program's effect. Bryan: *"It's been a struggle getting valid data and estimating what research is high quality, relevant, and then applying it correctly."* On thin local data, 2026-11-09: *"Use higher quality longer term data from elsewhere."*

A grade records how far the evidence is from our use. It does not fix the number.

## Steps, one row per study

1. **Open the study itself**, or the note that read it. A grade from the abstract alone says so in its gaps. Never grade from memory.
2. **Quality.** Rate the design, and name the feature that sets the rating:
   - Behaviour or outcome study: Maryland Scientific Methods Scale. 1 cross-section or after-only; 2 before and after, no comparison; 3 before and after with a comparison group; 4 quasi-random or strong matching; 5 randomized.
   - A field with its own rating registry (for example the FHWA CMF Clearinghouse for crash effects): record that rating and its id, or `unrated` with the reason.
   - A cost figure carries neither.
3. **Fit: GRADE's four kinds of indirectness.** Population, intervention, comparison, outcome: rate each none, serious or very serious, with one sentence comparing the study's with ours. Outcome is where units go wrong.
4. **Size match (benefit-transfer test).** Write the study's effect in its own units beside ours, and its treated extent (area, hours, who) beside ours. Mark `mismatch` when the size or extent of the change differs substantially.
5. **Cost from the same program.** True only when the cost paired with this effect is the cost of the program that produced it. If not, name where each came from.
6. **Test values.** The low and high the model runs, and the source of each.
7. **Write the row** to the project's evidence log, in the schema of the rows already there. A cited source that is not a study (a plan, budget, dataset, news story) is listed as such, with why it is not graded. No log yet: put the rows in a table in the analysis notes.

## Local data against outside evidence

One comparison table took a short local series with a few dozen injuries on each side, whose interval ran from a large drop to a large rise, used the rise end as its low estimate, and printed it as more injuries a year. That is noise from a small count, read as a finding.

- **Do not size an effect from a local series whose interval spans zero or is wide**, for example wider than the effect itself. A short window or small count usually causes it. Check the interval and the count before a figure uses it.
- **Take the effect from the strongest long-term evidence instead:** meta-analyses, and multi-year evaluations that control for regression to the mean.
- **Report the local result beside it, with its interval**, and say whether the two are consistent.
- **Never use one end of an interval as a point estimate.**

## When the grade is bad

A serious indirectness or a size mismatch goes to Bryan as a ruling on the central value and range, with the row linked. Do not adjust the number on your own judgement. File it with a default he can accept in one tap: the value from the strongest evidence, with the mismatch stated beside it.
