---
alwaysApply: true
---

# Effort Estimates

**Killer item — never state a duration straight from your training priors.** Those priors are pre-agentic. They produce "about two weeks" for work that finishes in 25 minutes, and every one of those costs the user a correction. An estimate in this fleet is *derived*, never guessed.

## The method — three numbers, in this order

1. **Baseline** — hours a skilled human engineer would need to do it **without LLM assistance**. This is the only figure you estimate directly, and your priors are good at it.
2. **Hands-on** — baseline ÷ 20. The user's own time: reading, deciding, reviewing, unblocking.
3. **Wall clock** — baseline ÷ 10. Request to done, including agent working time.

Always show the baseline you divided, so the number can be argued with:

> ≈40h baseline → **~2h hands-on, ~4h wall clock**

Never surface the baseline alone — an unconverted pre-LLM number is the failure this rule exists to prevent.

## Scope

- **Well-planned software tasks** — the case these constants were measured on. Use them by default there.
- **Non-software work** (ops, admin, writing, design chores) compresses about **half** as much. Halve both multipliers, or estimate hands-on directly.
- **Unplanned or ill-specified work** doesn't hit these numbers. Say the work needs scoping first — don't quietly pad the estimate to cover the ambiguity.

## Calibration

Measured across 711 recorded tasks (Apr–Jun 2026), each carrying a baseline estimate plus actual hands-on and wall-clock time:

| Slice | baseline ÷ hands-on | baseline ÷ wall clock |
| --- | --- | --- |
| Software tasks, median | 37x | 9.3x |
| Software tasks ≥10h baseline, median | 83x | 26x |
| All tasks incl. ops/admin, median | 10x | 5x |

÷20 and ÷10 sit at or below the software-task medians, so they **under-promise by design** — and under-promise most on the largest tasks. If an estimate feels too aggressive, the data says it probably isn't.
