---
name: ship-push-only
description: For advisory / team-owned repos where humans own PR review and merge. Implement → test → code review → push feature branch. Stop. Do NOT open a PR; do NOT merge; do NOT deploy. Notify the reviewer and hand off.
---

# Ship Push-Only

For repos where the team owns the PR/merge/deploy flow and Claude's role ends at "branch is pushed and clean." Common in advisory engagements where the project has many users in production and a team-owned review process. Pushing commits is fine; opening PRs and merging is not.

## Pipeline

### 1. Code Review (parallel background agents)

Same as `ship-auto` step 1 — Claude review + codex review in parallel. Fix BLOCKING findings. This catches issues before the human reviewer even sees the branch, which makes their review cheaper.

### 2. Definition of Done

Same as `ship-auto` step 2.

### 3. Push the branch

```bash
git push -u origin <branch>
```

That's it. **Do NOT** run `gh pr create`. **Do NOT** auto-open a PR.

If the repo's own rules say only a human commits or pushes, skip this step: stop at uncommitted changes in a worktree and hand off as below.

### 4. Hand off for review (set by Bryan, 2026-10-27)

A review item that lists facts about the change is not a handoff. He answered one with: *"I expect a diff review of the changes, plus a markdown mdx doc with the PR draft in a format that follows team conventions. Plus I expect in the PR draft screenshots or videos that show before and after."*

- **A diff review on the board**, grouped by intent (`create_diff_review` with `groups`).
- **A PR draft as an `.mdx` doc on the board, in that team's PR format.** Learn the format from the repo's PR template and its recent merged PRs. If the team requires PR text in the human's own words, the draft is notes for him to rewrite, and says so at the top.
- **Before and after screenshots or video in the draft for any visible change**, captured from the build where the bug shows.
- **One review item** that links the diff review and the draft and asks for the push or the PR.
- Also state test status, the local code-review findings he should confirm or dismiss, and the branch or worktree.

**If dev and production behave differently, fix why** so dev shows the bug too, rather than only patching the symptom.

Then stop. The PR opening, review cycle, merge, and deploy are the human reviewer's call.

## What NOT to do

- Don't `gh pr create` — even with "draft" or "ready-for-review" status. The team's process owns this.
- Don't message the team directly. Hand off to the user; he relays.
- Don't run any deploy command, even ones the repo defines. Production deploys are the team's owned action.
- Don't pre-emptively address review feedback that hasn't been written yet. Wait for actual comments.

## D-class

Same gates as `ship-auto` apply for any operations that *do* run (e.g., the local code review, branch push to a non-default branch). Pushes to feature branches are fine; pushes to `main` / `master` are D-class.

## When team practice changes

If the team adopts a more automated flow later, the project's `CLAUDE.md` switches to `Ship skill: ship-auto` (or `ship-guarded`) and this skill is no longer invoked for that repo.
