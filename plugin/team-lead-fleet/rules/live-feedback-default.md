---
alwaysApply: true
---

# Workspaces as the Default Review Surface

The plugin's skills carry all the mechanics and the user's standing preferences — `claude-workspaces:working-in-a-workspace`, `:editing-review-docs`, `:diff-review`, `:embedding-widget`, `:project-docs-layout`. This rule is only what has to fire *before* you would think to invoke one.

## Bind it, don't send a path

When you want the user to review a markdown doc, a dev server or an interactive preview, **put it on the workspace** rather than sending a file path or a bare URL.

- **Applies to:** anything you want his voice, structure or content pass on — posts, plans, audits, retros, design and decision docs — plus any dev-server URL or mockup, and anything where you want comment-level input.
- **Skip for:** one-line acks, code review (the PR diff is canonical), your own notes.
- **Once a doc is bound, never Write/Edit the `.md`.** The plugin flushes the live doc to disk about a second after every change and silently clobbers filesystem edits.
- **Bind every doc you work on with him as `.mdx`, from the start**, even when no chart is planned. A bound file cannot be renamed, so a `.md` doc that later needs a chart moves to a new doc and loses its threads. Set by Bryan, 2026-10-21: *"let's please always use mdx files for all docs that we're working on"*.
- **Draw a chart as a live chart block in the doc, never as an embedded PNG or SVG.** A regenerated image can stay stale behind a cache, and an agent cannot edit an image block. Same day: *"Stop using images."*
- **If this session has a `workspaceId`, or someone said "the board is your task list", read `claude-workspaces:working-in-a-workspace` before your first piece of work.** It is the contract, and nothing else will tell you to open it. It now holds the board rules this file used to carry: filing asks, replying on threads, evidence before done, task ids on dispatched work, and dated deferrals.

## An ask that starts on a doc thread is filed on that thread (2026-10-19)

Bryan, on a summary thread where an offer went out as plain text and its follow-up was filed on a
task: *"why didn't you raise a review item?"*, then *"Note doesn't work. Also why didn't you attach.
Review item to this thread"*.

- **If your answer to a thread comment needs his decision, make the reply the review item:**
  `post_reply` with `review`. Not an item on a task linked from the thread, and not an offer in plain
  text such as "say if you want it".
- **Every link in a thread reply or a review item opens on the board.** A repo path does not. Bind
  the file first and link the doc's URL.

## A dev server you put in front of him live-reloads

Set by Bryan, 2026-09-22: *"Can you please have the dev server page live reload, so i don't need
to refresh?"*, then *"And do that for all local dev servers"*. It applies to every peer, not to the
one project that prompted it.

- **If you bind a dev-server URL for review, the page reloads itself when the build changes.**
  Asking him to hit refresh to see whether you fixed something is the failure; he is reviewing on a
  phone as often as not.
- **The shape is small and needs no dependency.** Serve the build output, watch the source dirs,
  rebuild on change, inject a short `EventSource` snippet into every HTML response, and broadcast a
  reload when the build finishes.
- **This is a property of the review surface, not of one stack.** A framework that already does it
  satisfies the rule; a static server that does not is the thing to fix before you send the link.

## A link he tests runs the whole stack from the change (2026-10-28)

Bryan, after a phone test link where every photo was broken because storage and the worker were not running
from the change: *"when you're working on flows that require API and emulator, please spin up the emulator
stack so I can test working flows. Please remember that"*.

- **If the change touches the API, storage or a background worker, run all of it from the change's code**:
  emulators, API, workers, and stubs for outside services.
- **Before you send the link, open it at the address his device will use** and complete one upload or write
  through the flow.

## A doc review is one summary plus a comment on each line it is about (2026-10-26)

Bryan, after a review of his /about page arrived as one long reply on the thread that asked for it:
*"please comment in doc whenever you're doing a review, instead of replying in a giant comment up
here. It's kind of not helpful"*, then *"Please make sure this is done fleet wide -- any time you do
a doc review in a doc like this, post one summary review, but then multiple detailed comments in the
places that you're commenting about"*.

- **Reply on the asking thread with a short summary only:** the verdict and the one or two points
  that matter most, a few sentences at most.
- **Put every specific point in its own inline thread** (`create_thread` with `find`) on the words
  it is about, so he can read, answer and resolve each one where it applies.
- **Offer wording changes as suggestions** (`find_and_replace` with `suggest: true`), not as
  quoted text in a comment.
