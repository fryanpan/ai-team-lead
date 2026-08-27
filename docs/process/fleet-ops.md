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
