# Look for prior work before an in-depth analysis

Bryan, 2026-11-10: *"before we dive into any in depth analysis can you check if there are existing analyses others have done ... Open source or public tools, methods and data sets. ... it would have gone faster if we built on the foundations that already exist."*

## When it applies

- **An analysis or model of outside data that will take more than about two hours, or whose result will be sent or published.** Examples: a policy analysis, a market sizing, an effect estimate.
- **Not for:** debugging, analysis of the fleet's own systems (tokens, CI time, memory), or the questions in `product-discovery`, which keeps its own rules on outside research.

## The rule

- **Before the build, send one background research agent to look for prior work.** One agent covers every source below. Use a `Workflow` only where the user has opted in to one.
- **Sources to search:**
  1. The owning organization's own studies and data.
  2. Peer organizations doing the same work.
  3. Researchers, think tanks and advocacy groups.
  4. Standard published methods for this type of analysis.
  5. Open source and public tools and datasets.
- **Write three lists at the top of the plan, or of the analysis doc if there is no plan:** what to adopt as it is, what to compare your result against, and what is left to build. Then build only what is left.
- **If nothing usable turns up, name the sources searched.** An empty result with no sources named reads as a skipped search.
