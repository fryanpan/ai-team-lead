---
alwaysApply: true
---

# Continuous Feedback & Learning

## After Completing a Feature
1. **Self-review** before declaring done:
   - Did I miss any edge cases?
   - Is this the simplest solution?
   - Did I update all places that needed updating?

2. **Ask for feedback**:
   - "Does this work as expected?"
   - "Anything that felt clunky or could be improved?"

3. **Capture learnings**: Proactively identify and add things worth remembering:
   - Technical gotchas or surprises
   - Patterns that worked well
   - Mistakes to avoid repeating
   - API quirks or environment issues

   Edit `docs/process/learnings.md` directly with the new entry — don't ask first; this is a reversible additive doc edit. Mention what you added in the session summary so the user can review and amend.

## During Work - Watch for Friction
If the user seems frustrated, confused, or an approach isn't working:
- Pause and acknowledge: "This doesn't seem to be working well. What's off?"
- Ask what they'd prefer instead
- Offer to log the feedback for future sessions

## Periodic Retrospective
After ~2-3 hours of work or completing a major feature, prompt:
> "Quick retro:
> - What worked well?
> - What was frustrating or slower than expected?
> - Anything I should do differently?"

Then offer to log feedback in `docs/process/retrospective.md`

## Retros

`/retro` is user-invocable. Don't auto-prompt for it — that adds friction. If you notice patterns worth capturing during a session (recurring gotchas, slow tasks, broken plan assumptions), edit `docs/process/learnings.md` directly per the section above. The user can run `/retro` themselves when they want a structured pass.

## Elevating to Learnings

**A correction about HOW you write belongs in the fleet communication rule, not in your own memory.** Store it per-agent and it reaches one session; the user then gets the same failure from every other peer and has to give the same correction again. Three of his sharpest writing rules sat in one agent's memory for months this way. Propose the one-sentence version for `plugin/team-lead-fleet/rules/communication.md` and keep the provenance locally.


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
