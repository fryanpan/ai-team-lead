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

if [ "$CURRENT_DIR" = "$MAIN_WORKTREE" ]; then
  echo "In main worktree: private-checkout files linked, nothing else to link."
  exit 0
fi

# Registry (project list, team metadata, IDs)
link_private "$MAIN_WORKTREE/registry.yaml" ./registry.yaml

# Retrospectives (auto-generated project-specific content)
link_private "$MAIN_WORKTREE/docs/process/retrospective.md" ./docs/process/retrospective.md

echo "Private files linked from $MAIN_WORKTREE"
