---
alwaysApply: true
---

# Effort Estimates

**Killer item — never state a duration straight from your training priors.** Those priors are pre-agentic: they produce "about two weeks" for work that finishes in 25 minutes. An estimate is *derived*, never guessed.

## Step 1 — build a baseline by decomposition

The baseline is **hours a skilled human engineer would need without LLM assistance**. It is the only number you estimate directly, and you get there by breaking the work up — never by naming a figure for the whole thing.

- **List the components.** Each one a piece you could hand to a person. Can't name them → you can't estimate; say the goal needs scoping.
- **Size each component and add them up.** A whole-goal guess reverts to priors; a per-component guess doesn't.
- **Give every unknown its own line** — the API nobody has read, the data nobody has seen, the decision nobody has made. Each gets a range, not a point.
- **If the unknowns outweigh the known work, stop and report a scoping task.** Padding to cover ambiguity produces a number nobody can argue with.

## Step 2 — convert

- **Hands-on** — baseline ÷ 15. The user's own time: reading, deciding, reviewing, unblocking. Provisional.
- **Wall clock** — baseline ÷ 10. Request to done. The firmer constant.
- **Non-software (ops, admin, writing, design) is ÷5 for both.**
- **Ill-specified or unfamiliar work compresses far less — assume ÷7 and say so.** A quarter of measured software tasks landed there.

**Always show the baseline you divided:** `≈40h baseline → ~2.7h hands-on, ~4h wall clock`. Never surface the baseline alone.

**The hands-on divisor rests on a denominator that was replaced, and its margin is thinner than it looks (noted 2026-09-18).** It was fitted against multipliers measured Apr–Jun 2026, when hands-on time meant keyboard attention. Bryan rejected that measure on 2026-08-17 — it scored watching a build, reading a diff and thinking at zero. Its replacement is strictly larger on every task, which makes every multiplier smaller and eats the safety margin ÷15 was chosen for. **Keep using ÷15 and keep calling it provisional.** The corrected figure does not exist yet for anyone: the owning peer is holding every multiplier until Bryan picks a segmentation method. Do not re-derive one yourself, and do not quote a divisor from an installed plugin copy — 0.5.0 says ÷20, and reading it as current is the mistake this note exists to stop.

## What the divisor does NOT apply to

Two kinds of time are not work an agent can absorb; dividing them yields a confident fake number.

- **Irreducible human time — the user IS the input.** Reading a doc before his name goes on it, product judgement, deciding a rate, a voice pass on his own writing. An agent can prepare the decision — a confirm-or-override sheet instead of a blank page — but can't make deciding faster. Estimate it in his hours; say why it can't be divided.
- **Third-party clocks — waiting is not effort.** Filing turnaround, another team's review pace. Own row, no division, usually no date he owns.

The tell: an estimate that reads as achievable while every hour in it belongs to someone else.
