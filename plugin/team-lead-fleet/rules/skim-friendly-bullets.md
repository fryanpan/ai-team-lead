---
alwaysApply: true
---

# Skim-Friendly Bullets & Bold-and-Links Don't Mix

Two rules for user-facing markdown — daily reviews, live-feedback docs, status briefs, deliverables, chat messages, hive messages. Issued by user 2026-05-17; clarified by user direct 2026-05-18 after two Team-Lead-relayed versions drifted broader than intended.

## Rule 1 — one link per bullet point

When a bullet would cite or link to multiple files / sections / docs, split into multiple bullets (or a sub-list, one link per sub-item). User skims bullet, clicks, returns, goes to next; bundled references break the skim pattern.

Bad (bundled):
```
- See AUDIT.md plus the PR #2 diff and METHODOLOGY.md section 3 for context.
```

Good (split):
```
- AUDIT.md — overall framing
- PR #2 diff — what changed
- METHODOLOGY.md §3 — original rationale
```

## Rule 2 — bold and links don't mix

Bold (`**...**`) is fine for emphasis, labels, headings, and section markers — in chat, in docs, anywhere. The only restriction: do NOT nest bold with links in either direction.

Bad (bold wraps a link):
```
**[label](https://example.com)**
```

Bad (link contains bold):
```
[**label**](https://example.com)
```

Both forms break: some markdown renderers parse the link first and can't close the bold cleanly, leaving literal `**` characters after the link; some editor inline-mark engines fail to merge the two marks; live-feedback's editor mishandles the nesting in its own way.

Good (link plain):
```
[label](https://example.com)
```

Good (bold near the link, not wrapping it):
```
Important — see [the design doc](https://example.com).
```

## What is NOT in scope

Bold for emphasis on plain text (no link inside) is fine in chat messages and in docs. `**Status:** done` as a bullet label is fine. Italics are fine. Hash-mark headings are fine.

The separate live-feedback doc structure rule (use `##` headings + flat bullets, not prose-with-bold-labels-collapsing-into-paragraphs) is about phone-screen readability, not the bold marker itself. It lives in per-agent memory `feedback_live_feedback_doc_structure.md`.

## A bullet has a hard ceiling — three sentences, and an edit that breaks it SPLITS rather than appends

**Killer item — the wall of text is never written, it ACCUMULATES.** A daily review is amended all day; each correction gets appended to the bullet it corrects, every individual edit looks reasonable, and nobody re-reads the whole block. One bullet in the 2026-08-18 review reached **3,600 characters** that way, across five appends. Bryan's response was *"What the shit is this mass of text. This is actively harmful for a daily review — how am I supposed to use this?"*

- **Three sentences, roughly 300 characters, is the ceiling for any bullet on a surface he reads on a phone.** This is a checkable limit, not a matter of taste — you can count it.
- **After ANY edit to a block, re-read the whole block, not your diff.** The rule the writing guidance never had: it governs authoring and says nothing about amending, and amending is what these docs get all day.
- **If your correction doesn't fit, it is a NEW bullet with its own bold label.** A retraction, a superseding number, a follow-up finding — each is its own information type, so by the one-type-per-bullet rule above it was never allowed to share a bullet in the first place.
- **Never write "Correction, and it supersedes what I wrote here" inside an existing bullet.** That sentence is the tell that you are appending to something that should have been replaced or split. Replace the stale text, or add a bullet labelled **Retracted**.

## Self-check before publishing

Before binding a review doc, sending a hive message, or sending a chat reply:
1. Scan for any bullet with more than one URL or file reference — split it.
2. Scan for `**[...](...)**` or `[**...**](...)` patterns — unwrap so bold and link aren't nested.
3. If editing via live-feedback's `find_and_replace`, visually verify on the Tailscale URL before declaring ready.

## Provenance note

This rule was relayed across three rounds — first as "no trailing `**`" (about unmatched markers), then as "no `**` anywhere" (over-broad), finally clarified by Bryan direct as "bold is fine; bold and links nested don't mix." The third version is the correct one. Both directions of nesting (bold-wraps-link AND link-contains-bold) are in scope.
