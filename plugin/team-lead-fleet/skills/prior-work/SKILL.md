---
name: prior-work
description: Use before an in-depth analysis or model of outside data — one that will take more than about two hours, or whose result will be sent or published, such as a policy analysis, a market sizing or an effect estimate. Not for debugging, the fleet's own systems, or product-discovery questions.
user-invocable: true
---

# Looking for prior work before an analysis

**Read `<project>/.claude/workflow-overrides/prior-work.md` first, if it exists.** It names where the notes go and the owning organizations and methods for the project's domain.

## What it is for

Bryan, 2026-11-10, after a model built from scratch turned out to match a published state appraisal method and to miss three benefits the agency's own study had counted: *"before we dive into any in depth analysis can you check if there are existing analyses others have done ... Open source or public tools, methods and data sets. ... it would have gone faster if we built on the foundations that already exist."*

## Steps

1. **Before the build, send one background research agent.** It covers every source below. Use the Workflow script only where the user has opted in to it (see below).
2. **The agent searches five sources**, each with its own queries:
   1. The owning organization's own studies, evaluations and published data.
   2. Peer organizations that did the same work, at home and abroad.
   3. Researchers, think tanks, clearinghouses and advocacy groups.
   4. Standard published methods: agency guidance, appraisal manuals, professional handbooks, method papers. Say what each counts that a naive approach would miss.
   5. Open source code, public tools and public datasets.

   Then it asks which kind of source is obviously missing, and searches that too.
3. **The agent opens the sources it cites.** It marks any source it did not open, and never invents a title, figure or URL.
4. **Write three lists at the top of the plan**, or of the analysis notes if there is no plan: **adopt** (methods, tools or data used as they are), **compare against** (results ours must be checked against), **build** (the gap). Below them, every source found, with a link, and every search that found nothing.
5. **Build only the gap**, and say in the deliverable which method or tool it follows.

**If nothing usable turns up, name the sources searched.** An empty result with no sources named reads as a skipped search.

## Workflow script (opt-in only)

Where the user has opted in, `prior-work.js` in this skill's directory runs the five sources as parallel lanes, a gap check, and a writer. Call the Workflow tool with `scriptPath` set to it and `args`: `question`, `date` (`YYYY-MM-DD`), `out` (absolute path of the notes file), and optionally `context`, `owner` and `methods`.
