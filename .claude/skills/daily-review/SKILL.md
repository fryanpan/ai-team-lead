---
name: daily-review
description: Mid-day or end-of-day status pass. Trigger when the user asks for "daily review", "what to work on", "what to focus on", "where are we", or any near-equivalent. Reviews peer transcripts + hive messages + open PRs + the weekly plan, asks each agent for context where needed, then writes a prioritized review doc to `.claude/reviews/YYYY-MM-DD.md` and brings it under live-feedback so the user can comment from phone or laptop.
user-invocable: true
---

# Daily Review

The intra-day "where are we, what's next" pass. Pulls signal from across the fleet, asks agents for clarification where the transcript isn't enough, and writes a single dated file under live-feedback so the user can comment/edit on it from anywhere.

**Output:** `.claude/reviews/YYYY-MM-DD.md` (gitignored). One file per day. Brought under live-feedback via `create_review_doc` so the user can leave anchored comments and edit inline.

## Triggers

Invoke this skill whenever the user says any of:
- "daily review" / "review please" / "do a review"
- "what should I work on" / "what to focus on" / "what's next"
- "where are we" / "where do we stand" / "status check"
- "what's the team doing" / "team status"

These all map to the same thing: produce or update today's prioritized review doc.

## Steps

1. **Collect raw signal.**
   - Peer state: `mcp__claude-hive__list_peers` (machine scope) — current summaries.
   - Peer transcripts: use `analyze_transcript.py` (see `docs/process/learnings.md` § Retros) to scan today's transcripts for each peer. Don't write custom parsing.
   - Hive messages today: `mcp__claude-hive__check_messages` — anything in your inbox.
   - Fleet PRs: `gh pr list --json number,title,state,reviewDecision,updatedAt --repo <repo>` per registered repo (parallel via Bash background).
   - Live-feedback threads on the weekly plan + other watched docs: `list_threads`.
   - Discord messages: `fetch_messages` since last review.

2. **Ask peers for context only where the transcript isn't enough.**
   - For each peer working a committed goal, decide: is what they did today obvious from the transcript? If yes, skip the ping.
   - If not — `send_message` with a tight question ("Status on <goal title>? One-line summary + any blocker.") and wait for the reply before writing the section.
   - Don't ping peers that have been silent and aren't on a committed goal.

3. **Anchor priority on the weekly plan.**
   - Read this week's Notion page (the one `weekly-plan` skill set up). Pull the committed goals in priority order.
   - The review doc's outline = those goals, in the same order, plus a final section for things off-plan.

4. **Write or update `.claude/reviews/YYYY-MM-DD.md`.**

   If the file doesn't exist yet today, create it with this shape. If it does, append `## Update HH:MM` with the same shape inside.

   ```markdown
   # Daily review — YYYY-MM-DD

   *One sentence: where the day stands overall.*

   ## 1. <Goal title from weekly plan>

   - **Owner**: <agent name(s) + the user if applicable>
   - **Status**: on-track | slipping | blocked | done
   - **Today**: <one or two lines on what moved / who did what>
   - **Next for you**: <decision needed | review needed | nothing — keep moving>
   - **Link**: <github.com/... or mac-mini.<tailnet>.ts.net/... — only if useful>

   ## 2. <Next goal title>

   - **Owner**: …
   - **Status**: …
   - **Today**: …
   - **Next for you**: …

   ## Off-plan

   - <thing that came up that isn't a committed goal — in priority order>

   ## Decisions needed from you

   ### <Decision 1 title>

   - **Question**: <one sentence>
   - **Options**: <2-4 options, each one line>
   - **Cost**: <time / scope / reversibility per option>
   - **Recommendation**: <Team Lead's call, or "no recommendation">

   ### <Decision 2 title>

   …
   ```

   **Critical**: each field MUST be its own bullet, not run-on prose with inline bold labels. The live-feedback editor renders the doc on Bryan's phone — bullet-per-field stays scannable; prose-with-bold-labels collapses into wall-of-text. See `feedback_live_feedback_doc_structure.md` memory for the rationale.

   - Sections are **descending priority** (1 = highest priority committed goal).
   - Within each goal-section, batch *every* type of work (writing, decisions, pings, reviews) for that goal — don't cluster by task type across goals.
   - Links MUST be Tailscale (`*.<your-tailnet>.ts.net`) or GitHub (`github.com/...`). Never `*.local`. the user reads on phone over cellular.

5. **Bring the doc under live-feedback.**
   - On first creation for the day, call `create_review_doc(docId, path)` where `docId` is `daily-review-YYYY-MM-DD` and `path` is the absolute path to the file.
   - Capture the `reviewUrl`. Rewrite any `<your-mac>.local:8788` portion to `mac-mini.<your-tailnet>.ts.net:8788` before surfacing.
   - Call `watch_doc(docId)` so thread events arrive as channel notifications.
   - On updates within the same day, the live-feedback editor stays in sync with the file. Don't re-call `create_review_doc`.

6. **Surface the doc to the user — link FIRST, every time.**
   - **The very first line of your user-facing response MUST be the review URL on its own line**, formatted as a Tailscale link: `http://mac-mini.<your-tailnet>.ts.net:8788/review/daily-review-YYYY-MM-DD`. No preamble, no "here's the review", no apology — just the URL.
   - This applies on every invocation: first creation, every update / re-run, and any time the user asks "what's next" / "status" / "where are we." The URL is the user's only entry point on phone or laptop; if it's buried, he has to dig for it.
   - After the URL, you may add a one-sentence pointer (morning: which goal-section to start with; evening: what shipped + tomorrow's first goal).
   - The URL is non-negotiable. Even if you have nothing else to say, surface the URL.

7. **React to the user's comments + edits.**
   - Thread events arrive as channel notifications — handle them per the live-feedback skill (`feedback-threads` for triage, the `editing-review-docs` skill for any edits to the file).
   - When the user edits a section directly, re-read the file and carry his changes forward into peer dispatches.
   - Resolved threads = decisions made. Reflect them back into the weekly plan if they change goal priority or scope.

## Inline content or Tailscale link — never a forward-reference

Every actionable item in the daily review must be either:

- **Inline**, if it's brief enough to scan in seconds (1-3 sentences), OR
- **Linked via a Tailscale URL** (`mac-mini.<your-tailnet>.ts.net:...`) to a live-feedback-bound doc or other accessible artifact, if the content is longer.

**Never use forward-references** like "see Decisions §N" or "see below" or "ask <peer> for details." If the user has to scroll/navigate/ping to find what they need, the daily review failed its job. The user reads on phone — every "see X" is a context switch he pays for.

When in doubt: paste the content inline. Better to have a longer bullet than a forward-reference. If a bullet grows past ~5 lines, that's the signal to move the long content to a separate Tailscale-linked doc and inline a 1-2-sentence summary plus the link.

## What to avoid

- Don't write a free-form narrative. the user scans, doesn't read.
- Don't forward-reference. See "Inline content or Tailscale link" above.
- Don't repeat content the weekly plan already has. The review doc references goal titles and reports status — it doesn't re-justify the goals.
- Don't bury decisions inside goal-sections; promote them to the `Decisions needed` section.
- Don't include `*.local` URLs. the user reads on phone.
- Don't ping peers who have nothing to add. The review reflects what happened, not what you wish had happened.
- Don't grade the user's pace. Report status, not judgment.
- Don't `Write`/`Edit` the .md file directly once it's under live-feedback — route through the MCP edit tools per `editing-review-docs` skill.

## Decisions section — give enough background

Every decision in the `Decisions needed from the user` section must include:
- What's the question (one sentence)
- What are the realistic options (2-4 options, each one line)
- What does each option cost (time, scope, reversibility)
- A Team Lead recommendation if there is one — say "no recommendation" if not

If the user would have to chase down context to make the call, the section isn't done.
