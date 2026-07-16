---
name: writing-for-readers
description: Use when you have been dispatched to write or edit a document as your assigned task. If you are a top-level session and the doc is the deliverable, do not load this — dispatch the team-lead-fleet:writing-editor subagent, which loads it for you.
---

# Writing for readers

Your system prompt already covers leading with the outcome, writing for a teammate who stepped away, and staying readable rather than merely short. That's the target, and it holds for documents. These are the failures it doesn't catch.

Four are measured — each one came out of a nine-run baseline of agents writing a spike doc from raw notes, not from anyone's intuition about good writing.

**Say who is asking, and for what.** A writeup with no ask silently becomes a status report: the reader learns what happened and has no idea what you want from them. Before you draft, name the reader and the decision or feedback you need, and put it on the first screen. If nobody told you who reads this, pick the most likely reader and say which you picked — don't stall to ask.

**Claim only what the source supports.** Three ways this breaks, in the order they actually happen:

- *Inventing.* Every fact in the doc must trace to the material. "The branch is green", "most rides are short", "that's where this usually hurts" — if the source doesn't say it, you don't either. Hedging the caveats your source already flagged is not the same as being careful; it's copying its hedges. The claims that need marking most are the ones you introduced.
- *Drifting.* "Feels like the right tradeoff" is a hunch. Writing it up as **Recommendation** promotes it. Carry the source's confidence, don't upgrade it.
- *Not doing the arithmetic.* The reverse failure: if the material makes a finding available — two data points that reveal how something scales, a ratio that only holds under one condition — derive it, and mark it as your inference rather than the source's finding. A doc that only reorganizes its notes has added nothing the reader couldn't get from the notes. The three rules resolve through provenance: don't invent your inputs, do compute your outputs, label what you computed.

**Every number states the condition it holds under.** A measurement is not a constant. Anything that varies with input size (build time, download size, latency under load) names what it was measured at *and* flags that it scales — otherwise the number gets quoted later, detached, as a property of the thing. Naming the machine is the easy half; naming the workload is the half that gets skipped.

**A table cell must not mix inference with observation.** Consistent units and consistent treatment across rows. "Unknown" is not "n/a": one means nobody measured, the other asserts the question doesn't apply — and an unmeasured cell dressed as inapplicable is a claim you didn't make anywhere else. If one cell says "skipped" and another gives a number for the same underlying mechanism, that's a defect; resolve it before shipping. If you can't stand behind a cell, mark it provisional or cut it.

Three more, from craft rather than the baseline:

**Bold is a budget, not a highlighter.** Spend it on the one phrase per section a scanning reader must not miss. If a sentence needs bold to find its point, rewrite it shorter — concision is the emphasis mechanism, formatting is the fallback. Bold inside a table cell is noise; the cell is already the scanning unit. If more than one phrase in a paragraph is bold, none of them are.

**One ID scheme per doc.** If you number or label anything, number it one way. Competing schemes (B1 vs Step 1 for the same thing) come from editing rather than drafting — so they show up in revision passes, when you're least likely to reread the whole doc.

**Introduce your vocabulary once, up front.** When a doc must lead with something that depends on nouns you define later, give a one-screen primer first. Forward-referencing your own terms is the most common way a well-structured doc still reads as heavy.
