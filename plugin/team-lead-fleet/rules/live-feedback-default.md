---
alwaysApply: true
---

# Workspaces as the Default Review Surface

The plugin's skills carry all the mechanics and the user's standing preferences — `claude-workspaces:working-in-a-workspace`, `:editing-review-docs`, `:diff-review`, `:embedding-widget`. This rule is only what has to fire *before* you would think to invoke one.

## Bind it, don't send a path

When you want the user to review a markdown doc, a dev server or an interactive preview, **put it on the workspace** rather than sending a file path or a bare URL.

- **Applies to:** anything you want his voice, structure or content pass on — posts, plans, audits, retros, design and decision docs — plus any dev-server URL or mockup, and anything where you want comment-level input.
- **Skip for:** one-line acks, code review (the PR diff is canonical), your own notes.
- **Once a doc is bound, never Write/Edit the `.md`.** The plugin flushes the live doc to disk about a second after every change and silently clobbers filesystem edits.
- **If this session has a `workspaceId`, or someone said "the board is your task list", read `claude-workspaces:working-in-a-workspace` before doing anything else.** It is the contract, and nothing else will tell you to open it.

## A workspace URL is not a durable address

**The review URL embeds a workspace id that changes when the workspace is recreated.** Every link written against the old one dies silently — no error, no redirect, dead for you as well as the reader.

- **In a durable doc** — committed, exported, or sent to someone — cite relative repo paths or GitHub URLs.
- **In live chat** — a message, a thread reply, a hand-off — the URL is correct and is what he wants, because he's clicking it now.

## Match BOTH channel-source spellings — transitional, delete when the fleet is fully renamed

**Anything matching on the channel source must accept `source="live-feedback"` AND `source="claude-workspaces"`.** A session emits the new string only once restarted onto the new bundle, so respawned and un-respawned peers coexist. A matcher keyed to one spelling goes silently deaf to half the fleet, indistinguishable from nobody having commented. **Match on the presence of `doc_id` / `thread_id` instead** where you can; those did not change.

Same for anything else keyed to the old name: tool prefix `mcp__plugin_claude-workspaces_claude-workspaces__*`, skills `claude-workspaces:*`, install key `claude-workspaces@claude-workspaces`. Env vars gain `CW_*`; old `FEEDBACK_*` / `LF_*` spellings are permanently dual-read.
