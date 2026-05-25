---
name: ux-review
description: Walk a UI feature as a real user before shipping it. Identifies the cognitive friction the user will hit — confusion, dead-ends, wrong-target clicks — and reports it with severity. Use this BEFORE calling a UI feature done. Breaks the rework loop where the user reviews in prod and sends it back.
user-invocable: true
---

# UX Review

The biggest source of rework on UI work is shipping something that the agent has only ever read in code form. The user opens it in production, can't figure out where to click, tries the wrong thing, and sends it back. This skill makes the agent USE the page as a fresh user and catch the friction before the user does.

## When to invoke

- After implementing a UI change, before calling it done
- Before opening a PR for any UI feature
- Before merging on UI-impacting ship-skill runs (`/ship-auto`, `/ship-guarded`)
- Anytime an agent says "the UI is ready" — invoke this first

## What you'll need

- The UI running locally (a dev server URL) OR a deployed URL
- The user goal(s) the page is supposed to enable
- `claude-in-chrome` MCP tools (the skill assumes they're available)

If `claude-in-chrome` isn't connected, stop and ask the user to start Chrome with the extension. Don't try to "review from the code" — that defeats the purpose.

## The walkthrough

Walk each user goal as if you've never seen the page before. For each goal:

1. **Land on the page cold.** First impression — what does this page tell you it does, without reading carefully? If a first-time user couldn't answer "what is this?" in 3 seconds, that's a finding.

2. **Find your starting point.** Where does your eye go first? Is the primary action visually dominant? If the most prominent element isn't what the user should click first, that's a finding.

3. **Try to complete the goal.** Click, type, navigate. Note every moment of:
   - "Where do I go now?" (next-step ambiguity)
   - "Is that clickable?" (affordance failure)
   - "What does this button do?" (label clarity)
   - "Did that work?" (feedback failure)
   - "I made a mistake — how do I undo?" (recovery failure)

4. **Try to mess it up.** Submit empty fields. Click the wrong thing first. Use keyboard only. What breaks?

5. **Try to do it on mobile.** Resize to ~375px width. Targets too small? Layout broken? Important content below the fold?

Take screenshots at the key states — initial load, mid-flow, completion, error states.

## The heuristic checklist

After the walkthrough, evaluate against these. For each, mark Pass / Issue / Critical:

**Visibility & feedback (Nielsen #1)**
- Does the system tell the user what's happening at all times?
- After every action, is there visible confirmation within ~1 second?
- Every numeric value the UI shows (estimate, count, file size, ETA, transfer total) should be within ~2× the truth. If you can compute the real number cheaply, do — don't ship a synthetic guess. Cross-check one number on screen against a quick out-of-band calc. *(found in a recent geo-bbox review)*

**Match between system and real world (Nielsen #2)**
- Are labels in the user's language, not technical jargon?
- Do icons match what the user would expect them to mean?
- Icon-only buttons and toggles need an inline label, microcopy, or tooltip. If a user has to flip a toggle to learn what it does, it fails. *(found in a recent form-wizard review)*

**User control and freedom (Nielsen #3)**
- Is there an obvious way to undo a mistake?
- Can the user back out of any flow without losing progress?

**Consistency and standards (Nielsen #4)**
- Do similar things look similar? Different things look different?
- Does the page follow conventions the user already knows from other sites?
- Cross-browser parity. Test all tap/click handlers on iOS Safari, not just desktop Chrome. Synthetic `click` events sometimes don't fire on dynamically-attached or canvas/SVG elements — bind `pointerup` (or both) when reliability matters. Works-in-Chrome-but-not-iOS = Critical. *(Live Feedback mobile — tap-to-pick broken on iOS Safari, PR #43)*

**Error prevention (Nielsen #5)**
- Are dangerous actions confirmed before they happen?
- Are inputs validated as the user types, not after submit?

**Recognition over recall (Nielsen #6)**
- Are options visible, or does the user have to remember what's available?
- Is necessary information available where it's needed?
- Defaults reflect the user's current context, not zero. Maps start at the user's location, not the equator. Dates default to today, not 1970. Filters default to the most useful state, not "all." If you can detect what the user probably wants, use it. *(found in a recent geo-bbox review)*
- For any object the user just created (shape, selection, item), they must be able to manipulate it: resize handles, move grip, delete. Creating without editing is a Critical. *(found in a recent geo-bbox review)*

**Flexibility and efficiency (Nielsen #7)**
- Are there shortcuts for power users (keyboard, bulk actions)?
- Can the user customize what they see frequently?

**Aesthetic and minimalist design (Nielsen #8)**
- Is every element on the page necessary for the user's goal?
- Does decoration compete with the primary action for attention?
- One way to do each action. If long-press AND a delete button AND swipe all do the same thing, pick the most-discoverable one and remove the rest. Multi-affordance adds decision cost; it doesn't add accessibility. *(found in a recent form-wizard review)*

**Recover from errors (Nielsen #9)**
- When something goes wrong, does the message say what happened, why, and what to do next?
- Are error messages constructive, not blaming?
- Permission-blocked flows (location, camera, notifications, storage) must tell the user: what's missing, why it's needed, exactly which Settings toggle to flip, and what unblocks once granted. "Permission denied" is never enough copy. Reviewers: don't classify a permission-blocked flow as "host issue" until you've tried fixing the copy — permission UX is your problem. *(found in a recent geo-bbox review where a permission flow was misclassified)*

**Help and documentation (Nielsen #10)**
- If help is needed, is it findable in context?
- Can the user accomplish the goal without leaving the page to read docs?

**Motor / Fitts's Law**
- Are click targets ≥44×44px on mobile, ≥24×24px on desktop?
- Is the most-used target closest to where the user's hand/cursor naturally rests?
- Are destructive actions far from frequent ones?
- Mobile virtual-keyboard test. Open every form on mobile with the keyboard up; the submit/primary action must stay reachable. Use `visualViewport` API or `position: fixed` + `safe-area-inset-bottom`. Critical action below the keyboard fold = Critical finding. *(Live Feedback mobile — comment submit displaced off-screen by keyboard, PR #44)*
- Adjacent-target spacing ≥8px on mobile (Material). Stack tiny icons in a row with no gap and the user will hit the wrong one. *(general — found by walking against examples)*

**Visual hierarchy**
- Does the visually dominant element on the page match the user's most likely first action?
- Does the eye naturally flow in the order the user needs to read/act?
- Is there enough contrast between primary and secondary actions?
- WCAG AA contrast on text: ≥4.5:1 for normal, ≥3:1 for large text and UI components. Squint test on primary actions — blur your view; if you can't find the button, it fails. *(found in a recent form-wizard review)*
- Spacing rhythm consistent across the flow. Cramped rows AND a half-empty footer = wasted space and illegible density at once. Use a baseline grid; visually balance content vs whitespace per step. *(found in a recent form-wizard review)*
- Two interactive elements that look identical must do identical things; two that do different things must look different. Same glyph reused for "help" and "show legend" will get clicked wrong. *(general — found by walking against bike-map review)*

**Goal completion**
- Did you actually accomplish the goal?
- How many steps did it take vs. how many it should have?
- At any point did you guess what to do, or did the page tell you?
- Walk both the first-time empty state AND the repeat with-data state. They're different code paths and present different friction. *(general — found by walking against examples)*
- If a flow ends with the user holding new state (an object created, a value selected, a record saved), confirm they can edit/undo/delete it. Creation without a matching modification path is half a feature. *(general — generalizes from a recent geo-bbox review)*

## The report

Produce a single markdown report with:

```markdown
## UX Review — [feature name]
**Reviewed:** [URL] at [timestamp]
**Goals tested:** [list]

### Goal completion
- Goal 1: ✅ completed in 3 steps (expected 3)
- Goal 2: ⚠️ completed in 7 steps (expected 4) — got lost looking for X

### Critical issues (block ship)
1. **[Heuristic]** — [what's wrong, where, why it matters]

### Issues (should fix before ship)
1. **[Heuristic]** — [what's wrong, where, why it matters]

### Polish (post-ship OK)
1. **[Heuristic]** — [what's wrong, where, why it matters]

### Screenshots
- [path/to/initial.png] — initial load
- [path/to/midflow.png] — mid-flow
- [path/to/error.png] — error state

### What worked well
- [Notable strengths — keep doing these]
```

## How to use the report

- **Critical issues:** do not ship. Fix and re-run the review.
- **Issues:** fix before opening PR, or open PR with explicit acknowledgement and follow-up tickets.
- **Polish:** open as follow-ups, ship the main feature.

## Anti-patterns

- **Don't review from the code.** The whole point is to see what the user sees. If you can't run it, say so and stop.
- **Don't grade your own homework.** If the agent that built the feature is doing the review, dispatch a fresh subagent without context to walk it cold. Familiarity hides friction.
- **Don't over-engineer the heuristics.** The point is to catch obvious problems quickly, not write a 10-page evaluation.
- **Don't skip the goal-completion test.** Heuristic violations can be wrong; failure to complete a goal can't.
- **Don't accept a subagent's walk without screenshots on disk.** If the subagent reports findings but its screenshots are only in-memory IDs (not saved to disk paths the human can open), make it re-export — a walk without visual evidence is unverifiable. *(general — found by walking against bike-map review)*
- **Don't ship "blocked on host" without trying a copy fix first.** Permission denials, missing capabilities, and platform quirks frequently have a copy + UX path that unblocks the user. Classifying as "host issue" is an escape hatch, not a finding. *(found in a recent review)*

## Future enhancements (not yet implemented)

- Wire `npx -y a11y-mcp-server` for automated WCAG/keyboard scans
- Wire Lighthouse for performance + accessibility scoring
- For high-stakes UI changes, dispatch multiple persona-driven walkthroughs (UXAgent-style)
