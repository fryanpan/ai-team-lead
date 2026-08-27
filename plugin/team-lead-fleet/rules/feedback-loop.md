---
alwaysApply: true
appliesTo: main
---

# Continuous Feedback & Learning

## Capture learnings as you go

When you hit a technical gotcha, an API quirk, an environment surprise, or a pattern worth repeating: **edit `docs/process/learnings.md` directly — don't ask first.** It is a reversible additive doc edit. Mention what you added in the session summary so the user can amend it.

Propose specific additions. Never ask "anything to add?"

**Don't manufacture a stop to collect feedback.** A question asked in the terminal only exists while someone is watching the terminal, and asked after every unit of work it turns a queue into a conversation. Put the question where the work is — a comment on the task or the review doc — and keep going.

## Where a learning belongs

**Before filing it, ask: would a future agent know to grep for this?**

- **No** — the normal signal is actively wrong, so nobody will think to look. It must fire without a lookup, and it belongs in `CLAUDE.md` as a guard.
- **Yes** — archive it in `learnings.md`.

**Sort by grep-ability, never by importance.** The guards that matter most are the ones where a surface lies to you, and importance ranks those low while grep-ability ranks them high.

## A correction about HOW you write is a fleet rule, not a memory

Store it per-agent and it reaches one session; the user then gets the same failure from every other peer and has to give the same correction again. Three of his sharpest writing rules sat in one agent's memory for months this way.

**Propose the one-sentence version for `plugin/team-lead-fleet/rules/communication.md`** and keep the provenance locally.

## Retros

`/retro` is user-invocable. **Don't auto-prompt for it** — that adds friction. If you notice patterns worth capturing mid-session, edit `learnings.md` directly per above. The user runs `/retro` when he wants a structured pass.

## When the work is going badly

If the user seems frustrated or an approach isn't landing, say so and ask what's off, rather than pushing harder on the same approach.
