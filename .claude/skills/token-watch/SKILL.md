---
name: token-watch
description: Use when running the 3x/day fleet token check, when asked how the quota is trending, when asked the status of the Claude accounts, or before deciding whether to rotate pools.
user-invocable: true
---

# Token Watch

Runs 3x/day (08:07, 13:07, 18:07). The contract is `docs/process/token-control.md`
— tiers, rotation policy, and the account-switch runbook live there and are not
repeated here. This skill is the procedure and the **output shape**.

## The output is a table, not an essay

The user, 2026-09-09: *"Your token watch is not decipherable."* The pass before that
had reported one pool live, quoted two from a ledger, and answered "how am I
trending" with a single-pool pace multiple across nine prose bullets.

**A pace multiple on the active pool is not a trend when the estate holds three
pools with staggered resets.** Report the estate, then the one call.

- **In-session: the ESTATE table, then at most three lines** — the measured
  points/h, the gap-or-handoff verdict, and anything that needs the user. Nothing
  else unless he asks.
- **Trend log: ONE line.** `date time PT · pool · all% (Fable%) · elapsed% ·
  verdict`. A finding that is genuinely new gets one sub-bullet. The long-form
  entries in that file are history, not the format to copy.
- **Say what to do, not what the number is.** "Rotate now or expect a hard stop
  Thursday midday" is answerable in one word; "the pool is at 38%" is not.

## Procedure

1. **Establish the account live** — `~/.claude.json` → `oauthAccount.emailAddress`,
   plus a `/usage` week bar with a distinct %/reset. Same reset = same pool.
   **Never trust a written pool figure, including one asserted in the cron prompt.**
2. **Read `/usage` off an idle peer's pane.** Sentinel-test first: send one
   character and read back whether it **replaced** or **appended**. Text on the
   `❯` line is usually a ghost over an empty editor. Inspect the palette before
   pressing Enter — `/` opens it and a stray Enter fires the highlighted entry.
3. **Record BOTH meters**, week bar and session line:
   ```
   python3 scripts/fleet_budget_watch.py --record-meter <email> \
     --all-models <%> --fable <%> --resets <ISO> \
     --session-resets <ISO> --session-pct <%>
   ```
   The session reset is the window's **phase** and exists nowhere else; without
   it the watcher trails from run time and can only report an upper bound.
4. **Run the instruments, capturing the drift exit code directly** —
   `cmd > f 2>&1; echo $?`. Piping to `tail` reports tail's status and reads as
   clean. `fleet_budget_watch.py` (the estate panel + the binding 5h window),
   `fleet_burn_report.py`, `fleet_context_report.py`, `plugin_drift_check.py`.
5. **Append the one-line trend entry.** Tier 0 always, Tier 1 automatically,
   Tier 2 asks the user.
6. **First run after a weekly reset:** also run the end-of-week retro from the
   contract.

## Reading the estate panel

`fleet_budget_watch.py` prints every pool, not just the active one:

```
POOL                  USED  LEFT  FABLE  RESETS            RUNWAY  TRUST
pool-a@example.com     80%   20%   100%  Thu 09-10 17:59      ~9h  idle since read — a FLOOR
pool-b@example.com     38%   62%    43%  Fri 09-11 04:00     ~29h  live  <- ACTIVE
```

- **An idle pool's meter can only RISE until its reset.** So a stale reading is a
  **floor** and its "left" is a **ceiling** — a usable bound, which an unlabelled
  number is not. Say "80% or worse", never "80%".
- **A reading taken while a pool was still active is an under-read**, because the
  pool kept climbing after it. Check `account_since` before quoting one.
- **Runway is measured off the active pool's own meter** (`used% ÷ hours since
  account_since`), not from tokens. Never project multiple days off the current
  5h window: on a quiet morning it read a third of the sustained rate and
  projected 99h of runway against a real 29h.
- **The GAP line is the finding.** Whether the active pool outlasts the next
  pool's reset, or dies before it and leaves hours to bridge.
- **Check the meters separately.** Blended and Fable exhaust independently, and
  an estate can be comfortable on one while having a single pool's worth of the
  other.

## What not to do

- **Don't quote the trend log or any written "current pool" figure.** Pull live.
- **Don't call a trending-to-100% pool Tier 2.** That is expected under
  burn-freely rotation. The *rotation* is the Tier 2 call, because it severs the
  claude.ai connector set fleet-wide.
- **Don't report a mid-work session over 300k as a lever.** No agent can
  self-compact — surface it.
- **Don't re-explain a standing drift or a known blocker every pass.** Note it
  unchanged in one clause.
- **Don't stay silent on an open Tier 2 call.** Re-ping every run while inside
  24h of its projected date — in-session above 24h, PushNotification inside it.
