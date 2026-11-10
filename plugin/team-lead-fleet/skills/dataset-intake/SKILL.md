---
name: dataset-intake
description: Use before a dataset feeds any figure for the first time — a new public, agency, vendor or internal dataset, a new source file, or a dataset used for a new measure.
user-invocable: true
---

# Taking in a dataset the way its publisher uses it

**Read `<project>/.claude/workflow-overrides/dataset-intake.md` first, if it exists.** It names where source notes live, the fetch and query scripts, and the project's check.

## What it is for

One analysis measured differently from the agency it was writing about: the nearest school where assignment is by lottery, a survey of one district used citywide, 2 blocks where the agency uses 1, and a legal distance not measured the way the law measures it. Each figure was computed correctly and measured the wrong thing.

**Before a dataset feeds a figure, read its data dictionary, reproduce one total its publisher publishes, and use the publisher's own unit, distance and counting rule.** A figure that cannot match a published total says so where it is shown.

## Steps

1. **Read the data dictionary** and the dataset's attachments. Write down:
   - **what one row is**, and the key that makes a row unique. Compare `count(*)` with `count(distinct <key>)`;
   - **the unit** of each measure (feet or metres, per direction or centreline, dollars of which year);
   - **the field that starts the clock**, for any duration;
   - **the status values**, and which ones mean the thing actually happened.
2. **Find a total the publisher itself reports from the same data**: its annual report, dashboard, plan or press release, not a news story. Note the figure, the document, its page or table, its as-of date and its definition. If no such total exists, say so in step 6 and skip to step 5.
3. **Reproduce it** with the publisher's filter, unit, counting rule and as-of date, through a script or saved query that can be rerun.
4. **Compare.** Within 2% is a match. Outside that, find why before building on the data: a date, a filter, a unit, a field whose meaning changed. A gap you cannot close is a finding: show both numbers wherever the dataset is used.
5. **Adopt the publisher's rule in the code** that builds the figure, with a comment pointing at the source note. Where you depart from it on purpose, say so in one sentence on the page.
6. **Record a `Published total` section in the dataset's source note:** the publisher's figure with a link and as-of date, your query, your figure and the difference, the rule adopted, and the date each side was read. A dataset with no total to reproduce still gets the section, saying `Not applicable:` and why.
