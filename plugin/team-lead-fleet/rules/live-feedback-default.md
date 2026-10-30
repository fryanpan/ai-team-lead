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
- **Every new doc an agent writes for a person is `.mdx`, bound or not**: plans, designs, reports, briefs, status and daily docs. Plain markdown is valid mdx, so nothing else changes, and an interactive element can be added later. Set by Bryan, 2026-10-28: *"Fleetwide can we have all agents make mdx docs by default going forward instead of md."*
  - **Keep `.md` for files a tool finds by name or type:** README, CLAUDE, AGENTS, SKILL and CHANGELOG files, rule files, and docs a site generator or linter expects as `.md`.
  - **Do not rename an existing `.md` doc.** Renaming a bound doc breaks its binding.
  - **Escape a literal `<` or `{` in mdx prose, or put it in code.** The parser otherwise reads it as markup.
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

## An approval to ship is one button that lists what it does (2026-10-29)

Bryan, on a PR draft that asked him to approve the push and the messages in prose: *"In the future,
just give me a button here to ask you to push the PR and send messages, and so on.  And give me a
list of things you're planning to do with the button."*

- **When shipping needs his approval, file one decision item** on the section of the doc that
  describes the shipping. This covers a push, opening or editing a PR, and sending a message.
  Work that ships without asking under `workflow-conventions.md` still does.
- **The item lists, numbered, every action its approve button triggers**, and says what it will not do.
- **One item for the whole set.** Not one item per action, and not a question in the doc body.
- **Never ask him to run a command** to do any of it.

## A pending PR or reply draft keeps a live test link at the top (2026-10-29)

Bryan, on a PR draft: *"Give me a link to test in the doc."*, then *"always give me a link to test
while the PR draft or comment response is still pending until it's done and posted"*.

- **Every pending PR draft or comment-response draft has a live test link at the top of its doc**,
  next to the diff links.
- **Keep the link working until the PR is pushed and the reply is posted.** The server behind it
  stays up until then, and comes down after.
- **If machine memory forces the server down early, say so in the doc** and tell him.

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

## A mockup's design notes sit in a margin outside the frame (2026-10-30)

Bryan, on a mockup whose notes were written inside the mocked screen: *"I thought we had a
convention of attaching comments with a line and having the comments float in the margin to the
right of the frame."*

- **Render the design in a frame that can be resized**, the way a design tool shows an artboard.
- **Put every design note in a right-hand margin outside the frame**, level with a numbered marker
  on the element it is about and joined to it by a line.
- **Never write design notes inside the mocked UI.** Text inside the frame reads as part of the
  product.

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
