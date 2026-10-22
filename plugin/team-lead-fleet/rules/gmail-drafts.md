---
alwaysApply: true
---

# Gmail Drafts

## The Gmail connector breaks every link in a draft (measured 2026-10-22)

`create_draft` on the claude.ai Gmail connector rewrites each link into a Google redirect,
`https://www.google.com/url?q=<url>&source=gmail&ust=<time>&sa=E`, when it saves the draft.
The recipient sees that redirect instead of your link.

- **No input format avoids it.** It was tested three ways: a bare URL in `body`, an `<a href>` in
  `htmlBody`, and a URL with no scheme (`cal.com/x`). All three came back wrapped when read with
  `messageFormat: RAW`. The scheme-less one also became `http://`.
- **The connector causes it, not Gmail.** Drafts the user writes in Gmail web keep clean links.
- **Nothing tells you.** The tool returns success. The user finds out only when he opens the draft,
  and that has happened across several peers.

## Until a clean draft path ships

- **An email with no links:** create the draft as usual.
- **An email with a link:** do not create it through the connector. Put the finished text, with
  clean links, in the review item or doc where he reviews it. He pastes it into Gmail.
- **After any `create_draft`, read it back** with `get_draft` and search the body for
  `google.com/url`. If it is there, delete the draft and use the step above. Do not hand over a
  draft you have not read back.
- **Do not try to repair a wrapped draft with `update_draft`.** It wraps again, and it also
  detaches the draft from its thread.

Delete this section when the fleet has a draft path that keeps links clean, and say which one.
