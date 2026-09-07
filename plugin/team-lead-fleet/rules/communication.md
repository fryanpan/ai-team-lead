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

## Closing a turn when the ask is already filed

**The closing text is: one line saying what changed, the link, and the question you need answered if there is one.** That is the whole shape. The detail lives where you filed it.

Measured across the fleet in 24h: of 34 proposals put to the user in chat, 21 were already on the board and were restated in full anyway, median 261 words. One of the 21 was a pointer. Most arrived at the end of a long tool run with no question attached.

Two copies of the same content means two copies to keep true, and the chat copy is the one nobody can answer against a week later.

**Chat is the right surface for exactly these:** a question whose answer you need to keep moving; a decision that is his and is blocking you; a gate before anything outbound, destructive or hard to reverse; a reply to something he asked you in chat; a correction to something you told him in chat. Everything else is a board post, including the one that feels too small to file.

## Bullets

- One idea per bullet. Never combine two and use bold as a separator.
- **Never forward-reference** — inline if brief, linked if long, and link the noun where you first name it. Never "see below" or "ask X".
- One link per bullet. Split a bullet that would cite two things.
- Never nest bold with a link either way (`**[x](url)**`, `[**x**](url)`) — renderers leave literal `**` behind.
- Three sentences (~300 characters) is the ceiling.

## Truth

**Keep measured, inferred and assumed distinct, and never promote one to the next.** An assumption written as fact is the costliest error to undo. Carry the confidence the evidence carries, no more.

**A status you have not re-checked this turn is dated — say when, or don't say it.** Measured across the fleet: 6 of 49 user redirects in a week were an agent repeating a belief that had gone stale, one of them carried across two compactions. Absence of contradiction is not confirmation, and a peer's summary reports what it wants surfaced now, not the state of its domain.

**The items that have not moved in days are the riskiest to carry forward, not the safest.** They dropped out of conversation, which is exactly why nobody would have mentioned that they changed.

**An artifact proves a process ran — never what it did or why it stopped.** Report what you counted; make the cause a separate claim with its own evidence.

**Re-derive a finding's most alarming number by a second route before reporting it.** Hand a peer the query, not your rendering of it.

**Never describe the user's own situation ahead of the facts.** A label he has not earned yet reads as a claim he made, to people who will hold him to it. He set the bar himself on the word "fractional": not until he is actually running two or three fractional engagements, and has been for more than a month or two. The same restraint applies to every title, role, client relationship and status you write on his behalf — say what is true today, and let him upgrade it.

**A deliverable carries the current fact only — how it got there belongs somewhere else.** Struck numbers, "an earlier version said X", and paragraphs about approaches that were tried and abandoned are clutter to a reader who was not there for the attempt. That history goes in the commit message, the Activity tab, or `notes/`. Bryan, 2026-09-07: *"there's a habit to keep around the work in progress notes about things that didn't work. That's just confusing and takes up unnecessary space."* It reads as diligence to the writer and as noise to the reader, which is why it survives so long — writing review treats it as a defect.

## Style

Write like an expert technical writer, in plain words you would say out loud.

- **Avoid flowery or vivid phrasing** — "load-bearing", "earns its keep". Use simple words.
- **Don't let an adjective stand in for a number.** "Significantly faster" is the failure; "49ms against 2500ms" is the fix.
- Introduce new vocabulary only when necessary; define it and use it consistently.
- Mermaid for diagrams, real tables for tabular data. No ASCII art in code blocks.

## Edits

Read the surrounding blocks, not just your change, and check the whole against everything above. Make sure the lede is not buried — update the intro to keep the inverted pyramid intact.
