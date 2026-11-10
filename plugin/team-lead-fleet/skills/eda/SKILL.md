---
name: eda
description: Use when Bryan asks a quick exploratory question about data already on hand — "why does X jump in 2022?", "how many of these are Y?", "what's in this dataset?" — and the answer goes in chat or on a thread, not onto a page.
user-invocable: true
---

# Answering an exploratory data question

**Read `<project>/.claude/workflow-overrides/eda.md` first, if it exists.** It names the project's query database, how to refresh it, and its provenance tag format.

## What it is for

Bryan: *"I have found it useful to do EDA with your help and if we can get that even smoother it feels more natural and quicker even than Tableau."* So the answer is quick and small: **one query, one table, one chart**, and a sentence saying what it shows. No essay.

## Steps

1. **Find the table** and its columns. If the data has not been fetched, say so, name the dataset, and fetch it through the project's cached fetch path. Never answer from model knowledge.
2. **Read what one row is before counting.** Compare `count(*)` with `count(distinct <id>)`. If a column you need is often null, break the nulls out by year first.
3. **Query.** Keep the result to about 5 to 15 rows: grouped, sorted, labelled.
4. **Chart once**, unless the answer is two or three numbers. To show Bryan, bind it to the board and link it; he cannot open a local path.
5. **Answer in this order:** one sentence with the answer (count first, percent second); the query in a code block; the table; the chart; one line naming the source and the date it was fetched.

## Checks that apply to every answer

- **A step change is a question.** Date the jump, check whether it shows in every group or one, and say what you checked.
- **Two sources disagree? Show both.** Do not pick one quietly.
- **Count the way the publisher counts.** If the dataset has not been through `dataset-intake`, answer anyway and say the count is not yet checked against a published total. Run intake before any figure from it leaves the chat.

## When it stops being EDA

- **The question grows into an analysis** (more than about two hours, or it will be sent): run `prior-work` first.
- **A figure is going onto a page or out of the session:** it goes through `data-page` or `pre-send`. An EDA answer is not a deliverable.
