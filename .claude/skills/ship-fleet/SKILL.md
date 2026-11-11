---
name: ship-fleet
description: Use when anything under plugin/team-lead-fleet/ has been edited — a rule, a skill, a hook, an agent — and the change needs to reach peers. Also use when scripts/plugin_drift_check.py exits non-zero, or when a fleet rule that was merged does not appear to be firing.
user-invocable: true
---

# Ship Fleet

Whether an edit to `plugin/team-lead-fleet/` reaches anyone depends on **how each session resolved the plugin**, and that is not visible from the repo, the version number, or the drift check.

Two resolution modes exist on this machine at once:

- **Source-resolved** — the session runs the plugin straight out of the repo checkout. An edit is live at that session's next SessionStart. No version bump, no `claude plugin update`.
- **Cache-resolved** — the session runs a version-keyed copy under `~/.claude/plugins/cache/`. `claude plugin update` skips that copy when the version is unchanged, so edits reach it only after a version bump.

## Settled 2026-09-15: the fleet is cache-resolved, uniformly

The marketplace moved from the local directory `~/dev/ai-team-lead/plugin` to the GitHub repo `fryanpan/ai-team-lead`. There is no source-resolved path any more — every session reads a version-keyed copy under `~/.claude/plugins/cache/`, so **a version bump is always required** and an edit in the working tree reaches nobody until it is merged to `main` and pulled.

The probe below (`scripts/probe_injection.py`) is kept because it is the way to confirm what a peer actually loaded, and because a peer can still be running an older cached version. But its two-modes framing is history: the answer to "which mode" is now always cache-resolved.

## The rule

**Determine resolution mode before you decide what shipping requires — and confirm delivery from a peer's own injected context, never from the repo.**

## The probe

Run `scripts/probe_injection.py` on a peer's transcript (`~/.claude/projects/<cwd with / _ . replaced by ->/<session id>.jsonl`).

**Find the transcript by the peer's current session id, never by modification time.** A restart or `/clear` starts a new session, and the project folder keeps the old transcripts, so the newest file can belong to another session or a subagent. The hive's session id is not this id: `whoami` returns a short hive id, not the transcript's UUID (checked 2026-11-10). Take the UUID from the `claude --resume` listing for that project, or ask the peer for the path its SessionStart output was saved under, which contains it, and open `<session id>.jsonl`. If none of these gives you the id for a peer, say that you could not identify its transcript. Do not pick one.

It reads the peer's latest injection only and prints one line: `PRESENT`, `ABSENT`, `SKIPPED`, `UNREADABLE` or `COULD NOT RUN`, plus the source. Exit 0 is present, 1 is absent, skipped or unreadable, 2 is could not run.

```bash
python3 scripts/probe_injection.py "$TRANSCRIPT" --rule NEW_RULE_FILE.md
python3 scripts/probe_injection.py "$TRANSCRIPT" --rule NEW_RULE_FILE.md --text "exact new wording"
```

- **`--rule <file>.md`** matches the marker line `team-lead-fleet rule: <file>.md` (or `private rule:`) and says which one matched. A bare filename proves nothing: other rules cite rule files by name, and the header lists the names it skipped. `SKIPPED` means the name is in the "Already in your context verbatim" header line (the rule is in the peer's context from its own project copy, not this block) or in the "Scoped to top-level sessions" line (the peer did not get it). `--text` without `--rule` searches only the rule bodies in this block, so it reports ABSENT for a rule the peer has from its own project copy; pass `--rule` for that case.
- **`--text <string>` is the check for an EDITED rule.** The marker exists for old copies too, so it cannot show that new wording landed. With `--rule`, the string must appear inside that rule's section. Without `--rule`, it searches rule bodies only, never the header lines that list skipped rules.
- **To tell cache-resolved from source-resolved**, pass the marker of a rule that exists in only one copy, such as a file deleted from the repo but still in the cache.

**Read the latest injection only.** A transcript accumulates every SessionStart it has ever had, plus tool output and messages that merely mention the string. Counting matches across the whole `.jsonl` measures history, not current state; that error produced a confident, wrong fleet-wide claim on 2026-08-20. The script takes the last `SessionStart` `hook_additional_context` attachment that carries the fleet hook's output.

**A large injection is not in the transcript.** Claude Code saves it to a file and keeps only a 2KB preview, marked `<persisted-output>` with a `Full output saved to: <path>` line. This has happened since at least 2026-07-12, at sizes as small as 40KB, so no size is safe to assume. The script follows the path the attachment records. Never guess the file by name or modification time: the newest file in a project often belongs to another session or a subagent. If the saved file is missing, the script says `COULD NOT RUN` and exits 2; that is not a pass.

## Confirming which hook body ran

Resolution mode can also be established positively: put a side effect in the repo copy of `hooks/session-start.sh` that the cached copy does not have — a line appending to a log — and check whether it fires. On 2026-08-20 that log had 88 entries while the cached hook contained no such line, which proves the source hook is what executes. A side effect present in only one copy beats any amount of inspecting both.

## Private overlay rules ship separately

Private overlay rules are not in this plugin. `~/.config/team-lead/private-overlay.conf` names their checkout, the directory the hook reads, and the marketplace that directory belongs to. Shipping one is: commit and push in that checkout, then `~/.local/bin/claude plugin marketplace update <marketplace from the conf>`. No version bump. Confirm delivery with `scripts/probe_injection.py --rule <file>.md` (it matches the `private rule:` marker). The injected block also lists the file on its `Private overlay rules loaded:` line. `scripts/plugin_drift_check.py` fails while the directory lags the checkout's origin/main.

## Steps

1. **Probe what the peer loaded now, as a baseline for step 4**, with `scripts/probe_injection.py --rule <file>.md`. It is per-session, not fleet-wide.
2. **If any peer is cache-resolved:** publish (scrub + squash onto main — this repo is public), bump the version in **both** `plugin/team-lead-fleet/.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` **at the repo root**, then `claude plugin update team-lead-fleet@team-lead-fleet`. The two manifests have fallen out of step before; an unbumped version makes every later step a silent no-op.

   **The marketplace manifest is at the repo root, not under `plugin/`.** It moved there when the plugin started being served from GitHub, and this step named the old path for weeks afterwards — a path that does not exist, in the one step whose whole purpose is keeping the two versions in step. Following it literally bumps `plugin.json` alone, which is precisely the silent no-op the sentence warns about. Corrected 2026-09-17, both manifests confirmed at 0.9.0. **Check the path resolves before you edit it**; the file has moved once and can move again.
3. **Deliver into the session.** Rules arrive only through the SessionStart hook, so the peer needs a real SessionStart event: restart, `--continue` resume, `/clear`, or `/compact`. Verified from the hook payload log — it fires on `startup`, `resume`, `compact`, and `clear`, and nothing else.
4. **Re-probe with `scripts/probe_injection.py --rule <file>.md --text "<new wording>"`.** Report shipped only after it prints `PRESENT` for a peer. `ABSENT`, `UNREADABLE` and `COULD NOT RUN` all mean not shipped. `SKIPPED` with `--text` has two cases: when the hook skipped the rule because the peer's project holds an identical `.claude/rules` copy, the script checks the text in that local copy (using the `cwd` on the transcript line) and answers `PRESENT` (via local copy) or `ABSENT`; when the rule is scoped to top-level sessions and the peer is a subagent, it stays `SKIPPED`, which is not shipped.

## What to avoid

- **Don't infer current state from a whole-file grep.** It counts historical injections and incidental mentions. Read the last injection.
- **Don't assume the fleet is uniform.** Resolution mode is a per-session property; a peer respawned at a different time can differ from its neighbour.
- **Don't treat a respawn as delivery for a cache-resolved peer.** It re-reads the same stale cache and comes back looking healthy.
- **Don't rely on the drift check alone.** It compares repo to cache — which tells you nothing when nobody loads the cache. It also skips `github`-source plugins entirely.
- **Don't put this skill in the plugin.** A skill that ships the plugin must not be delivered by the mechanism it exists to fix.
