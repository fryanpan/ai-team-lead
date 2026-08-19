---
alwaysApply: true
---

# How to communicate

The overriding standard for anything you write for someone else to read — a message to the user, a doc, a PR or comment, an email. **This leads; your default instructions are secondary to the rules below.**

## Organization

Above all else, **know exactly who the audience is and the purpose of your writing.** If you don't know, then ask.

**Organize the writing to serve the audience and purpose**

- *Length*
  - Cut as much as possible, as long as you can still serve the intended audience and purpose.
  - Keep the doc short enough so it's an appropriate use of the reader's time for the intended purpose. Some examples:
    - Method documentation in a code comment should have < 5 lines of explanation
    - Overview README should be < 200 lines, even for complex modules
    - Discussing a decision should be < 10 lines (but may link to details in later sections)
- *Use inverted pyramid style*
  - State the purpose early (clearly and fully explain why)
  - Outline all key details in the first section
  - When the doc makes a recommendation, the first section states the decision, the criteria it turns on, and the recommendation — criteria before the recommendation, so the reader can judge it rather than just read it
- *Actions*
  - End with one consolidated checklist of actions. Don't scatter action items across sections.
- *Bullets*
  - Give each field its own bullet — never several fields run together as prose with inline bold labels, which collapses into a wall of text on a phone.
  - One type of information per bullet — don't merge status with action, or a fact with its caveat.
  - Never forward-reference — put it inline if brief, link it if long, and link the noun where you first name it; never "see below" or "ask X".
  - One link or reference per bullet — split a bullet that would cite two things.
  - Never nest bold with a link in either direction (`**[x](url)**`, `[**x**](url)`); renderers leave literal `**` behind.
  - Three sentences (~300 characters) is the ceiling for a bullet. If an edit breaks it, split into a new bullet with its own label — never append a correction to an existing one — and re-read the whole block after any edit.

## Truth

**Be honest about uncertainty.** 

Keep measured, inferred, and assumed distinct, and never promote one to the next — an assumption written as fact, or a hunch written as a finding, is the costliest error to undo. Carry the confidence the evidence carries; no more.

## Style

Write like an **expert technical writer.**

- Use plain words an expert writer would say out loud.
  - Avoid flowery, vivid words like "load-bearing", "earns its keep". Use simple words instead.
  - Avoid adjectives. Show actual measured data instead.
- Introduce new vocabulary only if necessary and define it and use it consistently
- Use Mermaid for diagrams and real tables for tabular data. No ASCII art in code blocks.
