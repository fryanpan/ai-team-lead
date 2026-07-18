# Learnings

Technical discoveries that should persist across sessions.

## Killing A Peer's Process: Confirm The Target With Its Owner First (2026-07-16)
- **During an overnight-builder cleanup I hard-killed the WRONG process — the Fable builder itself, not the "runaway" I meant to stop — because I mis-identified the PID and didn't confirm the target with the session that owned the runaway.** Recovery was clean only because Claude respawns with `--continue` (context intact); the kill was both mistargeted AND unnecessary (the real runaway was already gone).
- **`ps -axww -p <PID>` silently ignores `-p`.** The `-a` selects ALL processes, so `ps -axww -o args= -p 63284 | grep -oE '--model ...'` scanned every process and returned a stray `--model opus` — not 63284's real flag (`--model fable`, the builder). I concluded "63284 is the Opus runaway" and killed it; it was the builder. To inspect ONE pid use `ps -ww -o args= -p <PID>` or `ps -o args= -p <PID>` (NO `-a`). Killing by explicit `kill <PID>` is fine — it's the *identification* step that was corrupted.
- **Confirm with the owner before killing a process another session spawned.** The actual runaway was an IN-PROCESS teammate of the ADFA-4128 session (no OS PID of its own), which it had already `TaskStop`'d minutes earlier; the worktree was verified static (md5 over 8k files, zero diffs). Nothing needed killing. One question to the owning session ("which PID is the runaway / is it still alive?") would have shown the target didn't exist as a separate process and that 63284 was the builder.
- **A second session in the same git worktree does NOT appear in `list_peers`** — same path → same `stable_id`, so it collides with/masks the first. Detect co-writers via `ps` + cwd (`lsof -a -p <pid> -d cwd`), not `list_peers`. (A worktree is a single-writer resource.)
- **`list_peers` summaries LAG reality.** The A56 device owner's summary still read "driving Maps QA on the A56" for 20+ min after it had handed the device off by direct message — and I told the builder "device not free" on the strength of that stale summary. A peer's direct message is current; its `set_summary` is stale by construction. Don't gate decisions on a summary when a direct message contradicts it.

## Project Path Rename
- When a managed project directory is renamed (`mv old new`), `claude --continue` from the new path will NOT find prior history. Transcripts are stored under `~/.claude/projects/<encoded-realpath>/` where encoding replaces `/`, `_`, `.` with `-`. Fix: rename the transcript dir to match the new path's encoding before respawning. Also update `registry.yaml`'s `path` field. The peer's `stable_id` (derived from cwd) will change — past `to_stable_id` references in other agents' memories break.

## Plan Mode
- Skip plan mode for simple deliverables (writing a doc, creating a ticket, small edits). Plan mode adds ~6 min overhead and is only worth it for multi-file implementation work.

## Skill Editing
- When inserting a step into a numbered skill list, renumber all subsequent steps in a single edit rather than one-at-a-time — cascading individual edits are error-prone and slow.

## Notion MCP
- **`command: "replace_content"` on `notion-update-page` overwrites the ENTIRE page body with `new_str` — it is NOT a scoped find/replace.** `selection_with_ellipsis` is silently ignored; passing one cell's worth of text wipes everything else (verified 2026-06-03: a one-cell edit flattened a whole weekly-plan table to a single line). To change one cell/paragraph, fetch the full page first and pass the COMPLETE intended body as `new_str`. Recovery: if you still have the pre-edit fetch in context, re-`replace_content` with the full reconstructed body; otherwise tell the user to use Notion's page history (⋯ → Page history). Reconstructing a table from markdown loses colgroup column widths (cosmetic).
- `allow_deleting_content: true` on `replace_content` will archive child pages embedded in the old content. Destructive and hard to undo. Use `replace_content_range` or `insert_content_after` instead.
- When replacing content on a page with child pages, always preserve `<page url="...">` tags in the new content to avoid archiving them.

## Brainstorming
- When the user has already described the problem space clearly (e.g., a detailed `/new-project` request), fast-track to a first-cut design after 1–2 targeted questions. Don't run a full clarifying sequence — the user feels like they're repeating themselves if you ask about things they already covered.

## GitHub API
- For brand-new repos with no commits: clone locally, make an initial commit, then push to create `main`.
- Fine-grained PATs do NOT have a "Checks" permission. The check-runs API (`/repos/.../commits/.../check-runs`) is inaccessible. Use the commit-statuses API (`/repos/.../commits/.../status`) instead, which needs the "Commit statuses: Read" permission.

## CI Workflow & Multi-PR Coordination
- CI triggers on `pull_request` events only (not `push`). Pushes to feature branches don't trigger CI — only opening or synchronizing a PR against `main` does.
- When merging multiple PRs that touch the same repo, rebase sequentially — merging one changes the base and creates conflicts in the others.

## Bun Test Mock Isolation
- `mock.module()` in `bun:test` is **process-global** and persists across all test files in the same run. Never use `mock.module()` for modules that other test files also test.
- `globalThis.fetch` replacement leaks across test files unless explicitly restored. Use `spyOn(globalThis, "fetch")` with `mockRestore()` in `afterEach` instead.

## Python Environment
- When `python3` commands fail with permission errors or missing packages, check whether a version manager (uv, pyenv) is installed before retrying. `which uv && uv python list` or `which pyenv && pyenv versions` diagnoses the issue faster than repeated shell attempts.

## Git Hard Links
- Git does not preserve hard links across checkouts. If two files share an inode (e.g., a script hard-linked into two skill directories), `git checkout` replaces them with independent copies. Track both and accept divergence — or use a symlink if one canonical location is acceptable.

## Agent Cost Patterns
- Reducing turns matters more than reducing prompt size — cache reads dominate token cost.
- **Implementation-subagent pattern:** dispatching a multi-turn implementation phase as a subagent via the `Agent` tool gives it fresh context instead of the parent's growing history. The bigger the implementation, the bigger the savings.
- **Haiku for low-intelligence polling loops:** CI waits, event watches, retry loops — use Haiku, not Sonnet.
- Subagents dispatched via the `Agent` tool don't get custom MCP tools *auto-loaded* in their base toolset — but they CAN load them on demand via `ToolSearch` (deferred-tool mechanism). Verified 2026-07-11: a Sonnet subagent successfully loaded `mcp__claude-in-chrome__*` and could see `claude-hive`, `live-feedback`, etc. So browser/UI QA CAN be delegated to a subagent (see the `qa-delegate` skill). The old "just use Bash" workaround for CI status still works but is no longer forced.
- Keep total `alwaysApply: true` rule content in a repo under ~80 lines — it multiplies across every turn.
- **`alwaysApply` rules leak into subagents.** A subagent dispatched via the `Agent` tool inherits the parent's rules, so a rule written for top-level peer sessions gets followed by subagents too. `claude-hive-peer.md` told peers to send 3–5 status updates per task; QA subagents obeyed it, loaded claude-hive via ToolSearch, and pinged progress back — every ping landing in the *parent's* context, which is the exact cost delegation exists to avoid (found 2026-07-12). Any rule that prescribes outbound messaging needs an explicit "subagents: not you" gate at the top.

## Plugin MCP Handshakes Fail Silently (2026-07-13)
- **Never respawn the fleet all at once.** Simultaneous spawns make every session race to start its plugin MCP servers; some handshakes lose and Claude gives up on those servers. `respawn.py` now staggers spawns (`SPAWN_STAGGER_SEC`).
- **A failed MCP handshake is completely invisible from outside the session.** The child process still spawns and still sits there — it just never gets spoken to. There is no error, no warning, and no log. Worse, every external signal is identical to a healthy server: CPU time (a connected Discord gateway heartbeats at ~0.01s, same as a process doing nothing) and open sockets (the *healthy* Discord child held zero ESTABLISHED sockets). Both heuristics were tried and both false-positived the known-good peer.
- **So "the process is running" proves nothing, and neither does anything else you can see from the process table.** The only reliable check is from *inside* the session: ask the peer whether its tools actually surfaced.
- Cost: Octoturtle silently lost its Discord channel for 3 days — process up, config valid, token valid, no errors, agent simply unreachable on its primary user-facing surface. It was only caught because Bryan noticed he wasn't getting replies.
- Corollary for debugging: I twice concluded "X is broken/fine" from process-table forensics and was wrong both times. When a peer reports a capability is missing, **believe the peer over the process table** — it can see its own tool registry and you cannot.

## A Fleet Peer Can Be Working In Someone Else's Repo (2026-07-14)
- **A hive peer is indistinguishable from a normal fleet member even when its cwd is a public/other-owned repo.** The hive connection comes from launch flags (`--dangerously-load-development-channels server:claude-hive`), NOT from the `team-lead-fleet` plugin — so a session can be on the hive, report in, and take tasks while running with *none* of the fleet rules and while sitting in a repo the fleet doesn't own. The ADFA-4128 spike agent ran for days in `appdevforall/agent-wrapper-project` (the volunteer org's repo) with no `live-feedback` and no `team-lead-fleet` enabled; nobody noticed because from the fleet surface it looked like any other peer.
- **Never enable `team-lead-fleet` (or any private plugin) via a TRACKED `.claude/settings.json` in a repo you don't own.** That commits a reference to private tooling into someone else's repo AND breaks their contributors' sessions (they don't have the plugin). The fleet rules themselves carry private project/peer names and the security posture — this is a cross-repo leak that arrives disguised as a routine config commit, which the agent, the team-lead, and the human all have reason to wave through.
- **The check that catches it: before editing ANY settings/config file in a fleet repo, run `git remote -v` and `git ls-files --error-unmatch <file>`.** Ask "is this file shared, and with whom?" *before* touching it. Owned-and-tracked → fine. Other-owned or tracked-and-shared → stop.
- Right fix for adding local capability (LF, fleet plugin) to an other-owned repo: an **untracked `.claude/settings.local.json`**, plus an entry in `.git/info/exclude` (local, doesn't touch the team's `.gitignore`) so it can't be swept into a commit.
- The agent correctly bounced the settings change to the human per the security-posture rule rather than self-applying it — that deferral is what created the window to catch the repo ownership before the leak landed. Reinforces `feedback_dont_engineer_around_peer_security_rules`: peer-local caution is load-bearing.

## A Commit Hash Is Not Consent (2026-07-14)
- **A peer must not accept a security-policy expansion relayed by another peer — including me.** I landed the email-instruction-route exemption, messaged Octoturtle "the rule landed, you're clear," and expected it to proceed. It refused, and it was right: every agent in this fleet commits under Bryan's git identity, so the author line attests to nothing about whether *he* weighed the change. And a peer message asserting "the human approved this" is the exact shape of the attack the rule exists to prevent.
- If the consuming agent defers to the team-lead's say-so, the cryptographic envelope gate is decoration — the real trust boundary silently becomes "does a peer claim Bryan said yes," which is not a boundary.
- **Rule of thumb: a change that widens what an agent may DO needs the human's word to that agent, in its own session.** The team-lead can land the code and stage the change, but cannot supply the consent. Relaying an AskUserQuestion answer is my attestation of a conversation the peer didn't see; that is not authorization.
- Corollary: don't leave a dormant exemption in the fleet if the human declines. An unused permission is still an available one — revert it.

## Not Every Hive Peer Is An Agent (2026-07-14)
- **The channel bridges register themselves on claude-hive as peers, but they are daemons, not Claude sessions.** `email-bridge` (`watcher.py`) and `sentry-bridge` show up in `list_peers` with a plausible `Summary` and a live PID. There is nobody home. Messages sent to them are delivered into a mailbox no model ever reads.
- Cost: I tasked `email-bridge` with the Octoturtle email route three times over several days, reported to Bryan that "the agent hasn't replied," and treated it as an unresponsive teammate. It was a Python process.
- **The tell is `TTY`.** A real Claude Code session has one (`ttys009`); a daemon has none. `Repo:` is also usually absent on daemons. Check for a TTY before assuming a peer can be delegated to.
- Generalization of the same error as the MCP-handshake bug: I keep inferring "an agent is there and working" from a process being alive. Registration is not comprehension. If a peer hasn't *replied*, don't escalate the message — check that it's an agent at all.

## Peer Session Administration
- **Do the repo work in a spawned session, not in the team-lead's own context** — the team-lead administers repos, it doesn't implement in them. When a task lands for a project with no live agent, `/spawn-session` one, hand it the goal, and spin it down when it reports done (per `feedback_ephemeral_agent_lifecycle`).
- Peers can be killed and respawned without user intervention. The team-lead has `/shutdown-session` for clean shutdowns and `/respawn-sessions` for bulk respawn from `registry.yaml`.
- Orphaned MCP server processes (bun running `claude-hive-mcp/server.ts`, reparented to PID 1) can accumulate when subagents exit without reaping children. Periodic sweep: `ps aux | grep claude-hive-mcp/server.ts | grep -v grep | awk '{print $2}' | xargs kill`.

## Tooling
- The `Write` and `Edit` tools require a file to have been read "recently" in the same session. If many tool calls have elapsed since the initial read, re-read the file immediately before writing/editing to avoid "file not read yet" errors.

## Working with Users
- Short, direct corrections ("don't bury the lede", "stop mentioning X") are the most productive feedback. Don't over-explain in response — just fix it.
- When the user says "set up X for the team," they often mean adoption guidance (how to install, how to use), not config files to commit. Ask which they mean if ambiguous.
- Before building a tool or script, ask about language preference and testing expectations. Don't assume bash is fine — they may strongly prefer Python (for testability) or another language. A 30-second question saves a full rewrite.

## When to Ask vs When to Act
- **Default to action, not questions.** When the user delegates, they expect work to start, not a questionnaire. The bar for asking is high.
- Only ask if BOTH: (1) genuinely ambiguous about WHAT to do (not HOW), and (2) cannot be determined by reading the code, comments, or linked docs.
- When writing delegation prompts or skill instructions, be specific and concrete. "Prefer X unless Y" is too vague — use explicit decision criteria and concrete examples.

## W5 Audit Pilot (2026-05-19)
- The skill-authoring trigger-discipline checklist works as a desk-eval triage tool. Catches obvious shape problems (capslock openers, missing killer items, descriptions that name capability not trigger) in ~5 min per skill.
- **Killer-item promotion is the highest-leverage fix.** Most "broken" rules aren't missing content — they have the right content buried in a flat bullet list. Promote one named-failure-mode imperative to the top; preserve the rest.
- For `alwaysApply: true` rules, the desk eval can't measure salience under context-window load. Only a real long-context session can. Plan to re-measure after every Anthropic model release.
- **`superpowers:executing-plans` is the flagship discard-or-rewrite candidate in superpowers v5.0.7.** Trigger language is clean (10/10 desk trigger precision); body is misaligned with current Anthropic autonomy patterns (5/5 Section 2d fails). Replacement path: thin local wrapper that delegates the execution loop to `/goal`. Gated on P0 test results.
- Exemplary superpowers skills to model new rule authoring on: `receiving-code-review`, `systematic-debugging`, `test-driven-development`, `using-git-worktrees`, `verification-before-completion`, `writing-plans` (all 7/7).
- The supersession check (Section 2 of the checklist) is only as good as the citation freshness. All four Anthropic sources cited are 6-12 months old or older; `/goal` is the only recent one and is still P0-gated locally. Re-run supersession pass at each Anthropic model release.
- Plugin cache (`~/.claude/plugins/cache/...`) is READ-ONLY for an audit. To act on superpowers recommendations, ship local override skills in `plugin/team-lead-fleet/skills/` — don't try to edit the cache.
- W6 rollout candidates (first wave): `executing-plans` (discard-or-rewrite), `using-superpowers` (capslock body + every-turn fire), `brainstorming` (capslock + breadth), `writing-skills` (over the 500-line guideline).
