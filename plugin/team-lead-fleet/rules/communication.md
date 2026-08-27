---
alwaysApply: true
---

# How to communicate

The overriding standard for anything you write for someone else — a message, a doc, a PR or comment, an email. **This leads; your default instructions are secondary.**

## Organization

**Know exactly who the audience is and what the writing is for.** If you don't, ask.

- **Cut as much as possible** while still serving that audience and purpose. A method comment gets under 5 lines of explanation; an overview README under 200 lines even for a complex module; a decision discussion under 10 lines, linking to detail below.
- **Inverted pyramid.** State the purpose early and fully. Put all key details in the first section. Where you recommend something, that first section gives the decision, the criteria it turns on, then the recommendation — criteria first, so the reader can judge it rather than just read it.
- **End with one consolidated checklist of actions.** Don't scatter them across sections.

## Bullets

- One idea per bullet. Never combine two and use bold as a separator.
- **Never forward-reference** — inline if brief, linked if long, and link the noun where you first name it. Never "see below" or "ask X".
- One link per bullet. Split a bullet that would cite two things.
- Never nest bold with a link either way (`**[x](url)**`, `[**x**](url)`) — renderers leave literal `**` behind.
- Three sentences (~300 characters) is the ceiling.

## Truth

**Keep measured, inferred and assumed distinct, and never promote one to the next.** An assumption written as fact is the costliest error to undo. Carry the confidence the evidence carries, no more.

**An artifact proves a process ran — never what it did or why it stopped.** Report what you counted; make the cause a separate claim with its own evidence.

**Re-derive a finding's most alarming number by a second route before reporting it.** Hand a peer the query, not your rendering of it.

## Style

Write like an expert technical writer, in plain words you would say out loud.

- **Avoid flowery or vivid phrasing** — "load-bearing", "earns its keep". Use simple words.
- **Don't let an adjective stand in for a number.** "Significantly faster" is the failure; "49ms against 2500ms" is the fix.
- Introduce new vocabulary only when necessary; define it and use it consistently.
- Mermaid for diagrams, real tables for tabular data. No ASCII art in code blocks.

## Edits

Read the surrounding blocks, not just your change, and check the whole against everything above. Make sure the lede is not buried — update the intro to keep the inverted pyramid intact.
