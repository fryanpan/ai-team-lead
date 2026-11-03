#!/bin/bash
# Open a link in the user's existing Chrome tab for the same board, instead of a new tab.
# Prefers the active tab of a window already on that board, then any tab on it.
# Opens a new tab (in a normal, non-incognito window) when no tab matches or the
# link is not a board link. Prints REUSED-TAB or NEW-TAB.
# Usage: open-in-board-tab.sh <url>
set -euo pipefail
url="${1:-}"
[ -n "$url" ] || { echo "usage: open-in-board-tab.sh <url>" >&2; exit 2; }
board=$(printf '%s' "$url" | sed -nE 's#.*(/workspaces/w-[^/?#]+).*#\1#p')
osascript - "$url" "$board" <<'APPLESCRIPT'
on boardMatch(u, board)
  if board is "" then return false
  if u does not contain board then return false
  set AppleScript's text item delimiters to board
  set tailStr to text item 2 of u
  set AppleScript's text item delimiters to ""
  if tailStr is "" then return true
  return (character 1 of tailStr is in {"/", "?", "#"})
end boardMatch

on run argv
  set target to item 1 of argv
  set board to item 2 of argv
  tell application "Google Chrome"
    set normalWins to {}
    repeat with w in windows
      if mode of w is not "incognito" then set end of normalWins to w
    end repeat
    -- 1. an active tab already on this board
    repeat with w in normalWins
      if my boardMatch(URL of active tab of w, board) then
        set URL of active tab of w to target
        set index of w to 1
        activate
        return "REUSED-TAB"
      end if
    end repeat
    -- 2. any tab on this board
    repeat with w in normalWins
      set i to 0
      repeat with t in tabs of w
        set i to i + 1
        if my boardMatch(URL of t, board) then
          set URL of t to target
          set active tab index of w to i
          set index of w to 1
          activate
          return "REUSED-TAB"
        end if
      end repeat
    end repeat
    -- 3. a new tab in a normal window
    if (count of normalWins) is 0 then
      make new window
      set URL of active tab of window 1 to target
    else
      set w to item 1 of normalWins
      make new tab at end of tabs of w with properties {URL:target}
      set index of w to 1
    end if
    activate
    return "NEW-TAB"
  end tell
end run
APPLESCRIPT
