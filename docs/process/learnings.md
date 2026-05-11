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
