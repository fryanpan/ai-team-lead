# Fleet writing/editing subagent — plan

Branch: `feature/fleet-writing-subagent` · Author: Fleet Writing Build session · 2026-07-16
Craft co-author/reviewer: the fleet's writing peer. Origin: a team-doc post-mortem on an advisory project (~4h of Bryan's hands-on time to get a proven prototype's team-facing doc presentable).

## Outcome

Any peer can hand "write/edit a doc other people will read" to a subagent that owns doc quality, instead of that quality depending on the calling agent remembering to care mid-task. Ships via `plugin/team-lead-fleet/`, so every peer that enables the plugin inherits it.

## The headline finding: the craft rules are mostly already default behavior

The brief assumed three craft rules (audience-first structure, concision, table self-consistency) needed to be written down. **I ran the RED baseline before writing them, and the premise largely does not hold.** This changes deliverable 3, not deliverables 1 and 2.

### What I ran

Nine fresh agents, same raw spike notes (three benchmarked approaches, several explicit non-measurements), asked to write it up. Three conditions, to isolate whether the *audience cue* was doing the work:

| Condition | Audience cue | Result |
|---|---|---|
| v1 (3 reps) | Prompt named reader + task ("team will read and discuss async, weigh in on whether B is right") | All 3 reader-oriented, light structure |
| v2 (3 reps) | Prompt vague ("write up a doc for this"), but notes' last line mentioned the team | All 3 reader-oriented — and all 3 cited that line as their reason |
| v3 (3 reps) | No audience cue anywhere (line stripped from the notes) | All 3 still reader-oriented, light structure |

My first two conditions were contaminated: v1 pre-satisfied the exact rule it was testing, and v2 leaked the audience through the fixture. v3 is the clean test.

### What the baseline actually produced

Zero of nine reached for the over-formalism failure mode — no ADR blocks, no Option 0/1/2 matrices, no cross-cutting ID schemes. All nine led with the question and recommendation, used one comparison table, and gave open questions their own section. Every doc carried 4–8 honesty markers ("not measured", "extrapolated", "one route, one machine"), including correctly refusing to launder the unfinished approach's "felt smooth on my M1" into a performance result. One rep, unprompted, named its own audience assumption and offered to re-cut.

That is R1 and R3 compliance without the skill.

### Why — the harness already ships most of R1 and R2

Verified against two independent system-prompt archives (Piebald-AI/claude-code-system-prompts v2.1.211, 2026-07-15; asgeirtj/system_prompts_leaks Opus 4.8), cross-checked against this session's own live prompt. Every agent already receives, verbatim:

> **Lead with the outcome.** Your first sentence after finishing should answer "what happened" [...]
> Write it for a teammate who stepped away and is catching up, not for a log file [...]
> Being readable and being concise are different things, and readable matters more [...]
> **Match the response to the question** [...] Use tables only for short enumerable facts.

The agents weren't being clever. They were following their system prompt. Per `skill-authoring.md` ("cut anything the harness already covers"), re-documenting this is waste that drifts out of date.

### What the archive says is genuinely NOT covered

- **Emphasis/bold discipline — zero coverage.** Grepped all 584 prompt files: no agent gets bold guidance by default. And the baseline over-uses it — median ~15 bold phrases per ~750-word doc (one per 50 words), including bolded whole sentences (`**pan off-route and you hit blank tiles.**`), which is exactly the "if a sentence needs bold to find its point, rewrite it shorter" failure.
- **Table *consistency*** — the harness says *when* to reach for a table, never how to build one (units, consistent treatment across cells).
- **Document structure** — all harness writing guidance is scoped to *chat output between tool calls*, not standalone documents. Agents transfer it to docs anyway (that's what the baseline shows), but the transfer is not guaranteed.
- **Vocabulary-up-front** (the writing peer's corollary) — not covered.

### The limitation I cannot close, and why it argues FOR the subagent

`docs/process/learnings.md`: *"For alwaysApply rules, the desk eval can't measure salience under context-window load. Only a real long-context session can."* My baseline is fresh-context. It **cannot** rule out that craft collapses in a long, code-heavy session — which is exactly the originating condition (the doc was an afterthought at the end of a long spike).

So the honest synthesis: **the failure was never that agents don't know how to write. It's that a loaded session doesn't stop to think about the reader.** A subagent fixes that structurally — it gets a fresh, short context whose single task is the doc. That is precisely the condition where I just measured near-perfect writing, nine times out of nine. **The subagent form is doing the real work; the craft text is a thin supplement.**

This vindicates Bryan's choice of subagent-over-rules for a better reason than the brief gave.

## Deliverables

### 1. The subagent — `plugin/team-lead-fleet/agents/writing-editor.md`

Mechanics verified (not guessed) against [plugins-reference](https://code.claude.com/docs/en/plugins-reference.md) and [sub-agents](https://code.claude.com/docs/en/sub-agents.md), plus real examples in `~/.claude/plugins/cache/`:

- Plugins ship subagents from an auto-discovered `agents/*.md` dir. No `plugin.json` declaration needed.
- Invoked as a **namespaced** `subagent_type`: `team-lead-fleet:writing-editor`.
- Frontmatter supports `skills:` — **preloads** skill content at startup. The craft skill binds to the subagent here, rather than relying on it remembering to load it.
- Plugin agents ignore `hooks`/`mcpServers`/`permissionMode` (security).

**The contradiction to neutralize.** The default subagent contract says: *"Do NOT Write report/summary/findings/analysis .md files. Return findings directly as your final assistant message"*, and general-purpose adds *"NEVER proactively create documentation files (*.md)"*. A writing subagent whose deliverable **is** a .md file fights its own base prompt. The definition must override this explicitly — write the file at the path the caller names, return only a short summary + the path.

### 2. The trigger rule — `plugin/team-lead-fleet/rules/delegate-writing.md`

Thin. Routes "doc for others" work to the subagent. Two hard constraints:

- **Budget.** Injected rules are already ~511 lines on this branch / ~661 with the unmerged branch, against a stated ~80-line budget in learnings.md. Note: `alwaysApply:` frontmatter is **vestigial** — `hooks/session-start.sh` concatenates *every* file in `rules/` regardless. New rule target: ≲15 lines.
- **Gate out subagents.** Custom subagents *do* load CLAUDE.md and project rules (verified), so the trigger leaks into the writing subagent and invites recursion.

**Gate design — prose + mechanical, following existing precedent.** `hooks/block-hive-in-subagents.sh` already solves this exact shape and records the lesson in its own comment: *"Rules alone don't hold here: an alwaysApply rule telling subagents not to do this is inherited advice they can rationalize past."* It uses a verified discriminator: **the PreToolUse payload carries `agent_id` only for subagents** (absent in the main session). I'll follow it: prose gate modeled on `claude-hive-peer.md`'s opener, backed by a PreToolUse hook denying recursive `writing-editor` dispatch from inside a subagent. Fail-open, like its precedent.

**Provenance correction, since this doc preaches it:** that precedent is *uncommitted* — `block-hive-in-subagents.sh` is an untracked file in the main worktree, referenced by an uncommitted `hooks.json`. It exists in no commit on any branch. So it has never run for any peer, and a checkout would delete it. It's a sound design I followed, but calling it "shipped precedent" would have been a claim the source doesn't support. It needs committing (see below).

### 3. The craft skill — `plugin/team-lead-fleet/skills/writing-for-readers/SKILL.md`

Scoped **down** to what the evidence supports — the harness's *"readable matters more than concise"* is the premise it builds on, not something to restate. Keep only:

- Emphasis/bold budget (the one axis with zero harness coverage and a measured baseline deficiency).
- Table self-consistency + scale-flagging (harness covers when-to-table, not how).
- Vocabulary-up-front corollary.
- One line on document structure choice (harness guidance is chat-scoped; make the transfer explicit rather than assumed).

The writing peer owns whether this is the right craft. **Open push-back for it:** its triage called emphasis-budget "not standalone, fold into concision as one line." The evidence inverts that — concision is the part the harness already covers well and the baseline already does; emphasis is the only axis with no coverage and consistent over-use. I'll propose emphasis carries more weight than one folded line, and let it rule.

## Reach

Only binds repos that enable `team-lead-fleet`. At least one advisory, team-owned repo in the registry has a **tracked** `.claude/settings.json` — cannot commit the plugin-enable there. Document the untracked `.claude/settings.local.json` override path instead. Worth stating plainly: the repo the originating failure came from is one this cannot reach by default.

## Test strategy

RED→GREEN per `superpowers:writing-skills`, with the honest limits stated.

| Test | Method | Result |
|---|---|---|
| Craft baseline (RED) | 9 reps, 3 contamination conditions, independent judge | **Null on the assumed failure**; 4 real failures found |
| Trigger baseline (RED) | Real top-level session, `claude -p`, no plugin | **RED confirmed** — wrote it itself in 4 turns, no delegation, and fabricated ("a regular rider fills their disk") |
| Trigger (GREEN), 1st attempt | Same + `--plugin-dir` | **FAILED** — see below |
| Trigger (GREEN), after refactor | Same | **PASS 4/4** — dispatches `team-lead-fleet:writing-editor`, 0/4 take the skill shortcut |
| Subagent registration | Ask a real session to list its `subagent_type`s | `team-lead-fleet:writing-editor` present — plumbing confirmed |
| Recursion gate | Force a subagent to dispatch writing-editor | **Hook fires**, inner dispatch denied, nothing written |
| Hook units | 6 synthetic payloads | 6/6 — denies only subagent+writing-editor; fail-open on garbage |
| Budget | `cat rules/*.md \| wc -l` | +11 lines (511 → 522) |

`--plugin-dir` loads a plugin **for one session only** — the live fleet's installed copy is never repointed. No peer was affected by these tests.

### The GREEN failure that mattered

First GREEN attempt, the trigger **did not fire**. The session called `Skill(team-lead-fleet:writing-for-readers)` and wrote the doc itself. The rule lost to the skill — the skill's description was a strong match for the task, and superpowers' `using-superpowers` mandates skill invocation forcefully ("YOU DO NOT HAVE A CHOICE"), while my rule was one mild paragraph in 522 lines of injected rules.

This is the project's own thesis reproducing inside the fix: a *triggering* failure. Two changes, both aimed at the observed rationalization rather than an imagined one:

1. The rule now names the loophole: *"don't write it yourself — and don't load a writing skill to help you write it better."* Killer-item promotion, per learnings.md.
2. The skill's `description` was retargeted so a top-level session doesn't match it. It doesn't need to be independently discoverable — the subagent preloads it via `skills:` frontmatter.

4/4 after. Consistent behavior across reps — per `writing-skills`, convergence is the signal the wording binds.

### Evidence the chain works end-to-end

The GREEN doc fixed the exact axes the baseline failed — including the two nobody predicted. It stated an explicit ask (3/3 fail at baseline), flagged that the numbers scale ("points on a curve, not constants" — 0/9 at baseline), refused to fabricate ("all are gaps in the spike, not risks I'm asserting" — 6/9 at baseline), wrote "Not measured — never finished" rather than "n/a", and **derived the latent scaling finding 0/9 baselines computed** ("8x the distance produced ~20x the download"), marked as *"my inference... not something the spike set out to measure."* The vocabulary primer and the inference-marking are skill-only content and the subagent never called `Skill` — so the `skills:` preload is confirmed working.

## Risks

| Risk | Mitigation |
|---|---|
| Craft skill is largely redundant → pure token cost on every writing task | Scope to the 4 uncovered items; the writing peer rules on content |
| Trigger over-fires (delegates trivial edits, or a doc the agent is mid-conversation about) | Trigger on "doc others will read", not all prose; measure over-fire in GREEN |
| Recursion | Prose gate + PreToolUse hook (precedent-backed) |
| Fresh-context baseline can't see long-context collapse | Stated as a known limit; it argues for the subagent, which is what we're shipping |
| Rule injection already ~8x over budget | Not fixing tonight; flagged for Bryan. New rule stays ≲15 lines |

## Repo-state issue for Bryan (needs a call, not mine to make)

`origin/main` = dbc4907. The main worktree sits on `registry-local-only-folders` @ 5bc1de5 — **6 unmerged commits** carrying `skill-authoring.md`, the `qa-delegate` skill, the email-firewall rules, and the subagent-leak learning. The *installed* fleet plugin runs from ec73ea0 **on that branch**, so the live fleet is ahead of main. This branch is based on main and follows those conventions without inheriting them. If that branch doesn't merge, my rule ships to a main that lacks the `skill-authoring.md` it's written against.

## Ship

PR to `ai-team-lead`. **HOLD for Bryan's morning review — do not auto-merge.** Fleet-wide behavior change.

## Late finding: merging this PR does not ship it

The fleet plugin is installed from a **directory** marketplace (`~/dev/ai-team-lead/plugin`), but the install is a **one-time copy** into `~/.claude/plugins/cache/`, made 2026-07-11 07:31 and never re-synced. Every cached file still carries that mtime.

The README claims "Skills + rules update on the next session start (hook re-reads files)." That is false as installed: `session-start.sh` re-reads `${CLAUDE_PLUGIN_ROOT}/rules/`, and `CLAUDE_PLUGIN_ROOT` **is the frozen cache** — not the source dir. So the hook faithfully re-reads a snapshot.

Measured drift between cache and source right now:

| File | State |
|---|---|
| `hooks/block-hive-in-subagents.sh` | missing from cache — and uncommitted in source, so it exists in no commit either |
| `hooks/hooks.json` | differs (cache has no `PreToolUse`) |
| `rules/claude-hive-peer.md` | differs |
| `rules/email-channel-capability-firewall.md` | differs — a security rule |
| `rules/workflow-conventions.md` | differs |

Three consequences:

1. **Peers have been running 2026-07-11 rules for five days.** Every rule change since — including edits to the email capability firewall — has reached nobody.
2. **The hive-block hook has never run for any peer.** It's untracked, so it's also one `git checkout` from deletion.
3. **This PR ships nothing on merge.** The plugin has to be updated from the directory source before any peer gets `writing-editor`. Merging is necessary and not sufficient — worth knowing before anyone concludes the subagent "doesn't work."

Not fixed here: whether the fix is re-installing on a cadence, a symlink install, or a respawn-time sync step is a fleet-operations decision, not this PR's.
