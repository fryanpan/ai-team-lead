# Learnings

Technical discoveries that should persist across sessions.

## Plan Mode
- Skip plan mode for simple deliverables (writing a doc, creating a ticket, small edits). Plan mode adds ~6 min overhead and is only worth it for multi-file implementation work.

## Skill Editing
- When inserting a step into a numbered skill list, renumber all subsequent steps in a single edit rather than one-at-a-time — cascading individual edits are error-prone and slow.

## Notion MCP
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
- Subagents dispatched via the `Agent` tool do NOT inherit the parent's custom MCP servers. Use standard tools (e.g. `gh pr checks` via `Bash`) for CI status inside subagents.
- Keep total `alwaysApply: true` rule content in a repo under ~80 lines — it multiplies across every turn.

## Peer Session Administration
- Peers can be killed and respawned without user intervention. The team-lead has `/shutdown-session` for clean shutdowns and `/respawn-sessions` for bulk respawn from `registry.yaml`.
- Orphaned MCP server processes (bun running `claude-hive-mcp/server.ts`, reparented to PID 1) can accumulate when subagents exit without reaping children. Periodic sweep: `ps aux | grep claude-hive-mcp/server.ts | grep -v grep | awk '{print $2}' | xargs kill`.

## Plugin Subagents
- Plugins ship subagents from an **auto-discovered `agents/*.md` dir** — no `plugin.json` declaration. The `subagent_type` is **namespaced**: `team-lead-fleet:writing-editor`, not `writing-editor`. Plugin agents ignore `hooks`/`mcpServers`/`permissionMode` frontmatter (security).
- The `skills:` frontmatter field **preloads** skill content into the subagent at startup. This is how you make a skill unforgettable: bind it to the agent rather than trusting the agent to load it. Verified — the subagent never calls `Skill` and still applies the content.
- **The default subagent contract forbids what a writing subagent exists to do.** Every subagent gets "Do NOT Write report/summary/findings/analysis .md files. Return findings directly as your final assistant message," and general-purpose adds "NEVER proactively create documentation files (*.md)." A subagent whose deliverable *is* a file must override this explicitly or it fights its own base prompt.
- **Test plugin changes with `claude -p --plugin-dir <path>`** — loads a plugin for one session only, so you can RED/GREEN a fleet-wide change without repointing the live fleet's installed copy. `--output-format stream-json --verbose` shows the actual tool calls, which is the only way to know whether a trigger fired vs. the model doing the right thing by another route.

## Rules vs Skills (triggering)
- **A rule telling agents to delegate loses to a skill whose description matches the task.** Measured: with both present, the session called `Skill(writing-for-readers)` and wrote the doc itself instead of dispatching the subagent. superpowers' `using-superpowers` mandates skill invocation forcefully; a mild paragraph inside ~500 lines of injected rules doesn't compete. Fixes that worked: name the loophole in the rule ("don't load a writing skill to help you write it better") **and** retarget the skill's `description` so a top-level session doesn't match it. 0/4 → 4/4.
- Corollary: **a skill that a subagent preloads doesn't need a discoverable description.** Writing one invites the parent to grab it — which spends exactly the context delegation was meant to save.
- The `alwaysApply:` frontmatter on `plugin/team-lead-fleet/rules/*.md` is **vestigial**. `hooks/session-start.sh` concatenates *every* file in `rules/` into SessionStart context regardless of it. Injected total is ~660 lines — ~8x the ~80-line budget above.

## Evaluating Whether Guidance Is Needed
- **Run the no-guidance control before writing the guidance.** Nine agents given raw spike notes and no writing guidance produced reader-first docs with light structure 9/9 — the assumed "over-formalism" failure (ADR blocks, option matrices) never appeared. The Claude Code system prompt already ships "Lead with the outcome" / "write for a teammate who stepped away" / "tables only for short enumerable facts" to every agent. Check the [system-prompt archive](https://github.com/Piebald-AI/claude-code-system-prompts) before documenting craft — you'll mostly be restating the harness.
- **Watch for contaminated fixtures.** My first test named the reader in the prompt (pre-satisfying the rule under test); my second leaked it via the fixture's last line, and all 3 agents cited that line by name. Both looked like passes. Strip the cue and re-run.
- **Agents mimic their source's hedges rather than reasoning about evidence.** Measured: 9/9 correctly hedged the two claims the notes flagged as uncertain, while 6/9 invented *new* unhedged facts ("the branch is green", "most rides aren't 40km") — some load-bearing in an argument. Careful-looking output is not epistemic discipline; it's pattern-matching on the source's caveats and stopping where they stop. Related: 9/9 did zero re-derivation — excellent transcriptions, zero analyses.
- A skeptical **independent judge subagent**, told explicitly that a false "this is fine" costs more than a false "this is flawed", caught all of the above. Eyeballing the docs did not — they read as excellent.

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
