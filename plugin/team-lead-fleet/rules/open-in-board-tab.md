---
alwaysApply: true
appliesTo: main
---

# Open a link in the tab Bryan already has (2026-11-03)

Bryan, by voice: *"when you are opening docs in the future, can you please remember to open docs in the same tab and make this a workspace fleet rule"*.

- **To put a doc, mockup or diff review in front of him on the Mac, load it in his open Chrome tab for that board.** Never `open <url>`: it adds a new tab every time, and he has to hunt for the right one.
- **Use the plugin's script for every link you open in his browser.** It reuses his active tab on that board, then any tab on it. It opens a new tab only when none matches or the link is not a board link:
  ```bash
  s=$(find ~/.claude/plugins/cache/team-lead-fleet -name open-in-board-tab.sh 2>/dev/null | sort -V | tail -1)
  [ -x "$s" ] && bash "$s" "<url>" || echo "open-in-board-tab.sh not installed: send the link instead"
  ```
- **It prints REUSED-TAB or NEW-TAB.** Anything else means it did not open the link; say so.
- **Every extra tab costs machine memory.** One Chrome renderer per tab, shared by every session on the Mac.
