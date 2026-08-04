---
name: daily-review
description: Status pass — where are we, what's next. Trigger when the user asks for "daily review", "what to work on", "what to focus on", "where are we", or any near-equivalent. ALSO runs automatically every morning before the user wakes (team-lead daily cron) to produce the day's status + hit list and sync the user's Asana task list. Reviews peer transcripts + hive messages + open PRs + the weekly plan, asks each agent for context where needed, then writes a prioritized review doc to `.claude/reviews/YYYY-MM-DD.md` under live-feedback.
user-invocable: true
---

# Daily Review

The intra-day "where are we, what's next" pass. Pulls signal from across the fleet, asks agents for clarification where the transcript isn't enough, and writes a single dated file under live-feedback so the user can comment/edit on it from anywhere.

**Output:** `.claude/reviews/YYYY-MM-DD.md` (gitignored). One file per day. Brought under live-feedback via `create_review_doc` so the user can leave anchored comments and edit inline. On the automated morning run, the output also includes the user's Asana task list synced to today's hit list (see `## Automated morning run`).

## Triggers

Invoke this skill whenever the user says any of:
- "daily review" / "review please" / "do a review"
- "what should I work on" / "what to focus on" / "what's next"
- "where are we" / "where do we stand" / "status check"
- "what's the team doing" / "team status"

These all map to the same thing: produce or update today's prioritized review doc.

**Automated morning run:** this skill also fires **every morning before the user wakes** (team-lead daily cron, ~5:30am local) — no ask needed. See `## Automated morning run` below.

## Automated morning run (fires each morning — no ask)

Each morning the team-lead's daily cron runs this skill automatically so the user wakes to a current status + a ready hit list. Do the normal Steps below, plus:

1. **Frame the output as a status + today's hit list** — the doc leads with where things stand after overnight, then the 2–4 things worth doing *today* (drawn from the committed weekly goals + whatever is newly unblocked or now needs the user).
2. **Sync the user's Asana so today's tasks match today's hit list.** Asana is the user's primary task surface (populated by `weekly-plan`).
   - Mark done any task whose work actually shipped overnight (peer summary / merged PR confirms it) via `asana_update_task completed=true`.
   - Make sure today's hit-list items are the tasks dated today; **shift other tasks for the week to later days** as needed so today isn't overloaded (respect the weekly Capacity block — don't cram).
   - Add any newly-surfaced must-do that needs the user: short imperative name, dated today, 1-line note + link. Keep the day's list short and doable.
   - **Leave alone:** family/others' tasks (a shared volunteer project, a family member / a family member) and the user's Medical self-care items.
   - Asana reference: workspace `1211390582921761` · project "Bryan's Projects" `1212817868300931` · Bryan (assignee) `3708345653658` · non-premium → use `asana_get_tasks`, not `search_tasks`.
3. **Send one morning push** — `PushNotification`: `"Good morning — today: <2–4 hit-list items>."` Nothing else unless something genuinely can't wait (then flag it in the push).

Keep it cheap and otherwise silent. The review doc + the synced Asana list ARE the morning communication — don't also message the user separately. Still surface the review URL first (step 6) in-session for when the user checks.

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

- **Don't write "blocked on" / "waiting on" / "holding" without checking the peer's transcript.** The tmux pane is a render, not state — it cannot show what a session received. Grep `~/.claude/projects/<encoded-cwd>/*.jsonl` for its last processed turn; if that turn is newer than the supposed blocker, the session was never blocked. Text on the `❯` line is inert, not a pending message. This skill is where a fabricated five-day fleet blocker got escalated three days running (2026-08-03); see the killer item in `CLAUDE.md`.
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
