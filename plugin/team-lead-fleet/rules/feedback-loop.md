---
alwaysApply: true
---

# Continuous Feedback & Learning

> **Don't manufacture a stop to collect feedback** (Bryan, 2026-08-13). This
> file used to end every feature with "ask the user how that went" and prompt
> for a retro every couple of hours. Both are gone: a question asked in the
> terminal only exists while someone is watching the terminal, and asked after
> every unit of work it turns a queue into a conversation. If you have a
> question about the work, put it where the work is — a comment on the task or
> the review doc — and keep going. Capturing learnings still applies.

## After Completing a Feature
1. **Self-review** before declaring done:
   - Did I miss any edge cases?
   - Is this the simplest solution?
   - Did I update all places that needed updating?

2. **Capture learnings**: Proactively identify and add things worth remembering:
   - Technical gotchas or surprises
   - Patterns that worked well
   - Mistakes to avoid repeating
   - API quirks or environment issues

   Edit `docs/process/learnings.md` directly with the new entry — don't ask first; this is a reversible additive doc edit. Mention what you added in the session summary so the user can review and amend.

## During Work - Watch for Friction
If the user seems frustrated, confused, or an approach isn't working:
- Acknowledge it: "This doesn't seem to be working well. What's off?"
- Ask what they'd prefer instead
- Log the feedback for future sessions

This one is responsive, not scheduled — it fires because a person is already
telling you something, which is the opposite of stopping to ask whether they
have anything to tell you.

## Retros

`/retro` is user-invocable. Don't auto-prompt for it, on a timer or after a
PR — that adds friction. If you notice patterns worth capturing during a session (recurring gotchas, slow tasks, broken plan assumptions), edit `docs/process/learnings.md` directly per the section above. The user can run `/retro` themselves when they want a structured pass.

## Elevating to Learnings

During retros or after fixing issues, actively look for things that should change future Claude behavior:
- Did we hit a gotcha that will recur?
- Did we discover something about the codebase/tools?
- Did an approach work particularly well or poorly?

**Propose specific additions** to `docs/process/learnings.md` - don't just ask "anything to add?"

## When Logging Learnings
Format for `docs/process/learnings.md`:
```markdown
## [Category]
- [Specific gotcha or discovery]
```

Format for `docs/process/retrospective.md`:
```markdown
## YYYY-MM-DD - [Context]
**What worked:** ...
**What didn't:** ...
**Action:** ...
```
