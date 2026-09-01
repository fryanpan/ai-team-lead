# Fleet ops reference

Moved out of `CLAUDE.md` on 2026-08-27 — it is reference material that fires only when you are touching the health checker, a launchd job, or the leak gate. Grep it then.

## Fleet health check

`scripts/fleet_healthcheck.py` runs 3×/day under launchd (`com.fryanpan.fleet-healthcheck`), silent on green, macOS notification on red. It costs no tokens — no model runs unless something is actually broken.

- **Read current status**: `healthcheck-status.json` in the deploy root (below), or the log at `~/Library/Logs/fleet-healthcheck.log`. Run it on demand with `/usr/bin/python3 <deploy-root>/fleet_healthcheck.py --verbose`.
- **After editing the checker or the registry**, run `python3 scripts/install_healthcheck.py` — it redeploys and regenerates the config. **Editing the repo copy alone changes nothing**: the running copy lives in the deploy root. The `checker version` check enforces this — it sha-compares the running copy against the repo source and goes RED when they differ.

### Deploy root for anything launchd runs: `/opt/fleet`

A launchd-invoked Apple-signed binary is denied every operation on `/Volumes/Data` — exec, read, and even a stat. **`$HOME` does not save you**: `~/.claude`, `~/.config`, `~/.local` and `~/.bun` are each a symlink into that volume, so a path that looks like a home-directory path is often the secondary disk. `/opt` is genuinely boot disk (`disk3s5`) and is not shadowed by a symlink anyone might repoint.

Put the program *and* its config/state there; logs go to `~/Library/Logs` (real boot disk). One-time setup, since `/opt` is root-owned:

```bash
sudo mkdir -p /opt/fleet && sudo chown "$USER":admin /opt/fleet
```

`install_healthcheck.py` uses `/opt/fleet` when it exists and writable, and otherwise falls back to `~/Library/Application Support/team-lead/` with a printed warning.

When a launchd job genuinely must touch the secondary volume (e.g. the plugin cache or transcripts under `~/.claude`), delegate **every file operation** to `~/.bun/bin/bun`. Measured under a live LaunchAgent, 2026-08-25:

| under launchd | on `/Volumes/Data` |
| --- | --- |
| `/bin/bash`, `/bin/ls`, `/usr/bin/head`, `/usr/bin/rsync` | **`Operation not permitted`** — every one |
| `~/.bun/bin/bun` | **works** — read a 162MB transcript, listed all 65 project dirs |

- **The gate is per-binary, not per-path.** `bun` itself lives on the denied volume and launchd execs it fine — Apple code signing is what's gated, not the disk.
- **Root cause (Workspaces peer, 2026-09-01, two rounds).** First test: a bun copy on the SYSTEM volume executes under launchd while the `/Volumes/Data` binary hangs in dyld's `open()` of the executable. That looked like the binary's location was the problem, and **the obvious fix — put the binary on the boot disk — was tried with Bryan's approval and DID NOT WORK.** The relocated binary launches, then hangs on its first Data-volume *read*.
- **It is volume-wide, for every launchd-spawned process.** The clean control is `/bin/cat` of a Data-volume file via `launchctl submit`: it hangs identically, while the same `cat` of a system file returns. `cat` is Apple-signed and has nothing to do with bun. **So no choice of binary or interpreter helps** — anything launchd starts is denied `/Volumes/Data`, and the only fixes are granting Full Disk Access to the service binary or a reboot that restores the cleared grant.
- **Note the shape of the first conclusion.** One differential test isolated the variable it varied (binary location) and was read as isolating the cause. The second control varied a different thing and moved the boundary from a binary to a volume. A test that confirms your hypothesis has not ruled out the broader one you did not test.
- **⚠️ As of 2026-09-01 the bun exemption is NOT holding.** Under launchd, `bun -e 'console.log(1)'` produces no output at all and hangs until its timeout, while running instantly from a shell. Whatever grant bun had is gone — most likely a TCC entry cleared by the reboot. Until it is restored (Full Disk Access for `~/.bun/bin/bun` in System Settings, which only Bryan can grant), **every check that delegates to bun is blind**: plugin drift, checker-version drift, and the transcript archive backlog. They now say so explicitly rather than timing out.
- **Do not generalise the exemption to other user-installed binaries.** A uv-managed CPython — same volume, also not Apple-signed — was measured on 2026-09-01 and produced nothing under a LaunchAgent. `bun` was specific, and is currently not working either.
- **`stat` succeeds where `open` fails**, so a job can confirm a path exists and read zero bytes of it. That is how this masquerades as a working check.
- **Relocating the script fixes `exec` only.** If the work touches that volume, the *program* must be `bun` — not a bash script launched from a safe directory.
- **Every check asserts an end state, never a PID.** A process being up proved nothing in any real outage — see the 2026-08-11 learnings entry. If you add a check, make it fail when the thing stops *working*, not when it stops *running*.
- **Add a session check** by setting `always_up: true` on a registry entry. Don't key it on `respawn: true` — that means "bring back on a fleet restart", and most peers are correctly idle.

## Pre-push leak gate

`.githooks/pre-push` runs `scripts/scrub-check.py` on the diff being pushed and blocks the push if it finds project names (from `registry.yaml`) or denylist patterns. The principle: **once a push lands on GitHub and a PR is opened, the content is public-record forever (PR descriptions and commits can't be removed)** — so the gate has to fire BEFORE the push.

- Patterns: `projects:` keys in `registry.yaml` (auto-pulled; single-word names under 6 chars are skipped to avoid English-word collisions) + the hand-curated denylist at `~/.config/team-lead/scrub-denylist.txt`.
- Cross-repo fleet check: set `SCRUB_FLEET_REGISTRY=~/dev/<your-fleet>/registry.yaml` in your shell rc so the gate works in peer repos too.
- Self-name skip: the current repo's own name is never flagged (a repo legitimately self-references in its README / CLAUDE.md / plugin metadata).
- Bypass (use sparingly, never on a public repo without re-checking): `SCRUB_SKIP=1 git push ...`.
- Periodic audit: `python3 scripts/scrub-check.py --scan-all-tracked` scans every tracked file (not just the diff).
- Extending: edit `~/.config/team-lead/scrub-denylist.txt` (one pattern per line, plain string or `/regex/`).

## Fleet guard — the minutes-scale half

`scripts/fleet_guard.py` runs under launchd (`com.fryanpan.fleet-guard`) **every 120 seconds**, silent unless a band changes. It reads swap, free memory, load per core, orphaned test workers and the resident set of the claude process group — all via `sysctl` and `ps`, nothing on the secondary volume.

**It exists because of cadence, not coverage.** The healthcheck has carried the same swap / free-memory / load checks since 2026-08-18. On 2026-08-31 the machine went from healthy to a hard freeze in about two hours, entirely between two scheduled runs: every one of those checks would have been RED and not one of them executed. An instrument sampled slower than the failure it watches cannot see the failure. The guard's thresholds sit *below* the healthcheck's RED lines on purpose — the point is lead time, and a warning at 95% swap arrives after the machine has stopped painting windows.

- **Notifies on the crossing**, not on the state: on escalation, once on recovery, and at most every 30 minutes while critical. Firing every two minutes while a condition persists is how a check becomes furniture.
- **State**: `guard-state.json` in the deploy root. **Log**: `~/Library/Logs/fleet-guard.log`, one line per run.
- **Deployed by the same installer** — `python3 scripts/install_healthcheck.py` copies both programs and installs both agents.
- **`--probe`** prints exactly what the process can and cannot do in whatever context it is running in. **`--selftest`** proves the revival path against a decoy session, so it can be run against the live fleet without firing a false "loop down" alert.

### A launchd job CAN reach the secondary volume — through the tmux server

Measured 2026-09-01 from a real LaunchAgent: `tmux new-session -d -s <name> <script on /Volumes/Data>` **works**, and the spawned command reads that volume fine.

**The mechanism is inheritance.** The tmux *server* forks the child, so the child inherits the server's disk access rather than launchd's. The launchd process only ever talks to the server socket in `/private/tmp`, which is boot disk. This is a third route alongside `bun`, and it is the one to use for anything that has to be a long-running loop.

**It holds only while a tmux server already exists.** After a cold boot with no Terminal there is nothing to inherit from — a server started *by* launchd would carry launchd's own denied context. The guard detects that case (`no-server`) and notifies rather than reporting a revival that did not happen. Closing that last gap needs Full Disk Access granted in System Settings, which is a manual, user-only change.


## While the workspaces service is tmux-hosted, its deploy path is a silent no-op

`POST /api/deploy` restarts the service via `launchctl kickstart`. The launchd job is currently booted out, so that call does nothing and reports nothing — **a deploy will appear to succeed and change nothing.** Deploys are frozen until Full Disk Access is granted and the launchd job is restored.

The live server is tmux session `cw-server`, started from a shell so it inherits working disk access. **Do not close that session** — it is the only serving instance, and launchd cannot currently replace it. It does not survive a reboot. `healthcheck` reports this correctly as `com.fryanpan.claude-workspaces: not loaded in launchd at all` — that RED is true and should stay red until the job is restored.
