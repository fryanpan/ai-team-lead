---
alwaysApply: true
---

# Notion

## Identify yourself

Comments and pages created via the Notion MCP appear as the user, since the integration uses his personal auth. **Prepend every comment and every page you create with `**From: <Your Agent Name>**` on its own line**, then a blank line, then your content. Use the friendly `session_name` from `registry.yaml`, not a technical ID.

Applies to `notion-create-comment`, `notion-create-pages`, and any major rewrite via `notion-update-page` (note it in a comment — don't rewrite a page silently). Skip only for a page the user asked you to maintain, or for your own logs.

## MCP quirks

- **`notion-update-page` and `notion-fetch` often fail on the first attempt and succeed on retry.** Retry once before investigating.
- **Fetch by page ID, never by URL** — a URL fails with `invalid_type`.
- **Prefer `replace_content_range` or `insert_content_after`** over `replace_content` with `allow_deleting_content: true`. The latter archives child pages embedded in the old content — destructive and hard to undo. Preserve `<page url="...">` tags in replacement content.

## Channel protocol

Notion events arrive through the bridge as `<channel source="claude-hive">` messages, same shape as a peer ping.

**Setup, once per session:** if your project has a canonical Notion parent — a Drafts folder, a CRM root, a planning page — subscribe to it and its descendants so new pages are covered automatically: `notion_watch_page(page_id="…", include_descendants=true)`. `notion_list_my_watches` shows what you have; subscribing is idempotent. **One canonical parent per project** — overlapping subtrees mean two agents answer the same comment.

**What arrives:**

- **Comments are explicit asks.** Act if it's yours, route via `send_message` if it isn't.
- **Page edits arrive only when they contain `TODO:` or `Claude`.** The receiver pre-filters and the routed message carries the matching snippet with context, so don't re-fetch. These are asks too, but **the directive may be for a different agent** — don't act on one just because it routed to you.
- **Structural events** (created, deleted, moved, locked) are awareness only.

**Answering:** if the ask is ambiguous, leave a clarifying comment on the same Notion page rather than messaging the user — it keeps the conversation next to its context. Confirm on the page or through claude-hive only when you have done something or have a question. Acknowledging every event makes threads unreadable.
