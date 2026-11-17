#!/bin/bash
# Symlink private files from the main worktree into a new worktree.
# Run this after creating a worktree so it has access to gitignored files.
#
# Usage: ./scripts/setup-private.sh

set -e

# Find the main worktree by taking the first entry from `git worktree list`.
# This works whether run from the main worktree or any linked worktree.
MAIN_WORKTREE=$(git worktree list --porcelain | awk '/^worktree/{print $2; exit}')

if [ ! -d "$MAIN_WORKTREE" ]; then
  echo "Error: Could not find main worktree (got: $MAIN_WORKTREE)"
  exit 1
fi

# If we're already in the main worktree, nothing to link.
#
# Compare RESOLVED paths. `pwd` returns the logical path (which keeps whatever
# symlinks you walked through to get here, e.g. ~/dev -> /Volumes/Data/...),
# while `git worktree list` returns the physical one. A plain string compare
# between the two forms says "different" for the SAME directory, the script
# concludes it is in a linked worktree, and then `ln -sf` points each private
# file at itself and destroys it. That happened on 2026-08-04 and cost
# registry.yaml and docs/process/retrospective.md, both gitignored and
# therefore unrecoverable from git.
CURRENT_DIR=$(cd "$(pwd -P)" && pwd -P)
MAIN_WORKTREE=$(cd "$MAIN_WORKTREE" && pwd -P)

# Belt and braces: never link a file onto itself, whatever the path compare said.
link_private() {
  local src="$1" dst="$2"
  if [ "$(cd "$(dirname "$src")" && pwd -P)/$(basename "$src")" = \
       "$(cd "$(dirname "$dst")" && pwd -P)/$(basename "$dst")" ]; then
    echo "Refusing to link $dst onto itself — skipping."
    return 0
  fi
  ln -sf "$src" "$dst"
}

# Files kept in the private companion checkout. Linked into EVERY worktree,
# main included, because main has no copy of its own. A regular file already at
# the destination is replaced only when it is byte-identical to the private
# copy; otherwise it may hold edits that exist nowhere else, so it is left
# alone and reported.
# The checkout's path is machine-local: the `checkout=` line of the overlay conf.
OVERLAY_CONF="${TEAM_LEAD_FLEET_PRIVATE_CONF:-$HOME/.config/team-lead/private-overlay.conf}"
PRIVATE_CHECKOUT="${TEAM_LEAD_PRIVATE_CHECKOUT:-}"
if [ -z "$PRIVATE_CHECKOUT" ] && [ -r "$OVERLAY_CONF" ]; then
  PRIVATE_CHECKOUT="$(sed -n 's/^checkout=//p' "$OVERLAY_CONF" | head -1)"
fi
link_from_private_checkout() {
  local rel="$1" src="$PRIVATE_CHECKOUT/$1" dst="./$1"
  if [ -z "$PRIVATE_CHECKOUT" ]; then
    echo "WARNING: no private checkout configured (checkout= in $OVERLAY_CONF) — $rel is not linked."
    return 0
  fi
  if [ ! -f "$src" ]; then
    echo "WARNING: $src not found — $rel is not linked."
    return 0
  fi
  # On a branch from before the split git still tracks this path. Linking over
  # it would turn a tracked file into a symlink that a later `git add -A` could
  # commit, private path and all.
  if git ls-files --error-unmatch "$dst" >/dev/null 2>&1; then
    return 0
  fi
  if [ -f "$dst" ] && [ ! -L "$dst" ] && ! cmp -s "$src" "$dst"; then
    echo "WARNING: $dst is a regular file that differs from $src — left alone. Merge it into the private copy, then rerun."
    return 0
  fi
  link_private "$src" "$dst"
}
link_from_private_checkout docs/process/fleet-ops.md
link_from_private_checkout docs/process/learnings.md

# This checkout's Claude Code settings (.claude/settings.json: permissions,
# hooks, env). They are local to the machine, so git does not track them.
#
# The main worktree's file is the live one and this script NEVER replaces it.
# Only when it is missing (a fresh clone on a machine that has the private
# checkout) is it restored, as a copy, from the backup kept there.
SETTINGS=".claude/settings.json"
SETTINGS_BACKUP="team-lead/settings.json"
# Git treats an ignored file as free to overwrite: switching the main worktree
# to a commit from before the file was untracked replaces it with that commit's
# copy, and switching back removes it. So each run (the checkout and merge
# hooks call this script) keeps a copy of the live file inside the git
# directory, where no checkout reaches, and a missing file comes back from
# that copy first.
SETTINGS_KEEP="$(git rev-parse --git-common-dir)/team-lead-settings.keep.json"
keep_settings_copy() {
  [ -f "./$SETTINGS" ] && [ ! -L "./$SETTINGS" ] || return 0
  # A tracked file here is an older commit's copy, not the live one.
  git ls-files --error-unmatch "./$SETTINGS" >/dev/null 2>&1 && return 0
  cmp -s "./$SETTINGS" "$SETTINGS_KEEP" 2>/dev/null && return 0
  cp "./$SETTINGS" "$SETTINGS_KEEP" && chmod 600 "$SETTINGS_KEEP"
}
restore_settings_if_missing() {
  if [ -e "./$SETTINGS" ] || [ -L "./$SETTINGS" ]; then
    return 0
  fi
  if [ -f "$SETTINGS_KEEP" ]; then
    mkdir -p ./.claude
    cp -n "$SETTINGS_KEEP" "./$SETTINGS"
    echo "Restored $SETTINGS from the copy kept in the git directory."
  elif [ -n "$PRIVATE_CHECKOUT" ] && [ -f "$PRIVATE_CHECKOUT/$SETTINGS_BACKUP" ]; then
    mkdir -p ./.claude
    cp -n "$PRIVATE_CHECKOUT/$SETTINGS_BACKUP" "./$SETTINGS"
    echo "Restored $SETTINGS from the private checkout's backup copy."
  else
    echo "WARNING: $SETTINGS is missing and there is no backup to restore it from. This checkout has no project settings: write your own (templates/settings/settings.json is a starting point)."
  fi
}
# A linked worktree gets a link to the main worktree's file, the way it gets
# the registry. Left alone when the branch still tracks the path (linking over
# a tracked file would let `git add -A` commit the link), and when a regular
# file is already there, because it may hold edits made in that worktree.
link_settings_from_main() {
  local src="$MAIN_WORKTREE/$SETTINGS" dst="./$SETTINGS"
  if git ls-files --error-unmatch "$dst" >/dev/null 2>&1; then
    return 0
  fi
  if [ ! -f "$src" ]; then
    echo "WARNING: $src not found — $SETTINGS is not linked into this worktree."
    return 0
  fi
  if [ -e "$dst" ] && [ ! -L "$dst" ]; then
    echo "NOTE: $dst is a regular file — left alone, not linked to the main worktree's copy."
    return 0
  fi
  mkdir -p ./.claude
  link_private "$src" "$dst"
}

if [ "$CURRENT_DIR" = "$MAIN_WORKTREE" ]; then
  restore_settings_if_missing
  keep_settings_copy
  echo "In main worktree: private-checkout files linked, nothing else to link."
  exit 0
fi

# Registry (project list, team metadata, IDs)
link_private "$MAIN_WORKTREE/registry.yaml" ./registry.yaml

link_settings_from_main

# Local channel list for the morning-digest skill
if [ -f "$MAIN_WORKTREE/.claude/skills/morning-digest/channels.local.md" ]; then
  link_private "$MAIN_WORKTREE/.claude/skills/morning-digest/channels.local.md" ./.claude/skills/morning-digest/channels.local.md
fi

# Retrospectives (auto-generated project-specific content)
link_private "$MAIN_WORKTREE/docs/process/retrospective.md" ./docs/process/retrospective.md

echo "Private files linked from $MAIN_WORKTREE"
