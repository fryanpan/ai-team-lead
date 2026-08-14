---
alwaysApply: true
---

# Live-Feedback as the Default Review Surface

When you want the user to review a markdown doc OR a dev server / interactive preview, **bind it to the live-feedback widget by default** rather than just sending him a file path or a URL. The plugin is stable and is the fleet-wide standard.

## When this applies

- Drafting any markdown for the user's voice / structure / content pass (blog posts, plans, audits, retros, design docs, decision docs)
- Sharing a dev server URL or HTML mockup for UX feedback
- Surfacing any document where you want comment-level input, not just a thumbs up

## When to skip

- One-or-two-line acks where there's no review surface
- Code review — PR diff is the canonical surface for code
- Your own logs / private notes (no the user input expected)

## How

**Markdown docs** — bind via `mcp__plugin_live-feedback__create_review_doc(docId, path, title?)`. Share the review URL (`http://mac-mini.<your-tailnet>.ts.net:8787/review/<docId>`) in your message to the user. Don't hand-build this URL — `create_review_doc` returns the live `reviewUrl`; use that and only rewrite the host to the Tailscale name.

**Dev servers / HTML mockups** — use the `live-feedback:embedding-widget` skill (it covers the `<script>` tags + `setContext` calls).

**Apply the user's comments via the live-feedback edit tools** — once a doc is bound, NEVER edit the .md file directly with Write/Edit. Use `find_and_replace`, `rewrite_thread_region`, `insert_blocks_after_thread`, etc. The plugin serializes the live doc back to disk ~1s after every change; direct filesystem edits get silently clobbered by the next flush. See the `live-feedback:editing-review-docs` skill for the full pattern.

**Watch for comments** via `watch_doc(docId)` — comment events arrive as `<channel source="live-feedback" doc_id="..." thread_id="..." event="...">` blocks. Resolve threads when you've addressed the feedback (`resolve_thread`).

**Working from a workspace board** — if a session has a live-feedback
workspace (a `workspaceId`, `next_tasks`, "the board is your task list"), read
the `live-feedback:working-a-workspace-board` skill and follow it. It is the
contract for whoever is working a board: priority order, fanning out on what
can run in parallel, keeping the board current, asking your questions as task
comments, and not stopping between tasks.
