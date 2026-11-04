---
alwaysApply: true
---

# Open a page in a browser without asking

**Bryan, 2026-11-03, on a research thread:** "never ask me that question again -- if you need to open something in Chrome, just do it. Make that a fleet wide rule"

## The rule

When a fetch cannot read a page you need (it renders only with JavaScript, it redirects to an app shell, or it returns an empty body), open it in a browser yourself and read it. Do not offer to do it and do not ask first.

1. Use Claude in Chrome (`mcp__claude-in-chrome__*`) when it is connected. Work in a new tab and close it when you are done.
2. If the extension is not connected, render the page headless: `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --virtual-time-budget=15000 --dump-dom <url>`, then strip the tags. Say in your report which of the two you used.
3. Only if both fail, say what failed and give the answer you have. Do not end with an offer to try again.

## What this does not change

This rule is about reading. Opening a page does not authorize anything you do on it. Signing in, submitting a form, accepting terms, posting, buying and downloading files still follow the session's permission rules. Reading a page that needs Bryan's sign-in is also not covered: say it needs his sign-in and move on.

## Why

"I can open it in Chrome if you want" hands Bryan a yes/no question whose answer is always yes. He has to read it, reply, and wait another round for the content he asked for in the first place.
