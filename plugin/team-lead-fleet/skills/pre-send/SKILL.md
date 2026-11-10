---
name: pre-send
description: Use before a doc, page, post, comment, report or PR body whose figures matter goes to Bryan for sign-off or to anyone outside the session — including a benchmark, a demo with measured numbers, or a model's headline. Not for a quick exploratory answer in chat.
user-invocable: true
---

# The gate before figures leave the session

**Read `<project>/.claude/workflow-overrides/pre-send.md` first, if it exists.** It names the project's evidence log, assumption checks, citation audit and decision log, and may add checks.

## What it is for

Bryan, on a model sent out for public comment: *"The model we submitted at time for public comment was very wrong. I only realized after poking at things for another day."* The checks that would have caught it existed and ran after the send. On a demo whose build times were several times off the benchmark in the post it supported: *"When we write a blog post, the parts must line up with each other or else we have a reason they don't."*

**The reviewer should never be first to spot that the parts disagree.**

## Size it

- **One figure changed after a passed gate:** run the review checks on that figure, then the dependents check below.
- **Anything else:** all three stages.

## The three stages, each a fresh agent

Run them in order. A builder's own passing check is not evidence for any stage.

1. **Review.** Re-derive every figure, and record each check run with its result:
   - **Publisher's definitions.** Each figure about an organization is measured the way that organization measures it (`dataset-intake`).
   - **Evidence.** Every study a figure leans on is graded for fit and size (`grade-evidence`). With no evidence log, the grades go in a table in the deliverable's notes. An applied effect larger than the study's own is a finding unless the doc says why.
   - **Assumptions.** Each input and causal link the headline needs has a source, a low and a high, and a test. Flag any input carrying more than a third of a headline.
   - **Pipeline.** Trace source to figure: fields, row counts, date ranges, nulls; joins that drop or duplicate rows; units that change; eligibility or seats counted as people who acted.
   - **Numbers agree.** Put each number beside the published or earlier figure for the same quantity: absolute values, not only ratios, with units and conditions matched. A gap over about 1.5x is explained in the notes or the setup is fixed and measured again.
   - **One figure, one source.** Two places computing the same headline both get reported.
   - **Citations.** Every cite links to its source. Every quote and every "he said", "per the spec" was checked against a document opened in this pass.
2. **Skeptic.** A fresh agent gets only what the reader gets, plus the model and its data. **Never the review, the notes, the build history or the threads.** It writes the headline as a reader takes it, then finds: an unanswered question, an unsourced claim, an unstated assumption, a missing comparison (baseline, the publisher's figure, the cheaper option, do nothing), and a figure that does not match its source. For each conclusion it asks:
   - Is the data complete for every group compared, and is each denominator from the same years and area?
   - Would another reasonable method choice (dedup rule, window, threshold, grouping) flip or halve it?
   - Does an outside figure for the same thing land within about 3x?
   - Is a causal word ("because", "worked") backed by more than a correlation?

   Where there is a model, it reruns the input that moves the headline most at the weak edge of its evidence, and reports the headline there.
3. **Verify.** One agent per item either stage could not check, up to three rounds each, until it is verified, shown wrong, or shown impossible to settle from public sources. Bryan gets results, not a list of open checks.

`pre-send.js` in this skill's directory runs all three stages as a Workflow. Use it only where the user has opted in; otherwise dispatch the stages with the Agent tool, at most three agents at once.

## Act on the result

**A finding blocks when a reader would be misled about the headline or a figure.** The gate passes when no blocking finding, `wrong` result or unsettled item is left.

- **A blocking finding**, a `wrong` verify result, or a headline that fails the skeptic's rerun: fix it, or rewrite the headline to what survives or to its range. Then run the gate again. Do not argue a blocking item away in the review item.
- **When a figure changes, grep the whole artifact for it before revising.** The same number often stands in a later paragraph that the piece builds to. Where an instance sits in text Bryan approved, put every instance on the decision item instead of fixing it yourself.
- **Write the comparison into the deliverable's notes:** each figure beside its reference figure, with both sources.

## Hand off

File the sign-off with `claude-workspaces:handing-off-for-review`. The item carries the headline as a reader takes it and where it goes, the skeptic's rerun in one line, anything impossible to verify and what would settle it, and the options with a cost each. **An external send needs his yes on that item; a yes on an earlier draft does not carry to a changed one.**
