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
if [ "$CURRENT_DIR" = "$MAIN_WORKTREE" ]; then
  echo "Already in main worktree, nothing to link."
  exit 0
fi

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

# Registry (project list, team metadata, IDs)
link_private "$MAIN_WORKTREE/registry.yaml" ./registry.yaml

# Retrospectives (auto-generated project-specific content)
link_private "$MAIN_WORKTREE/docs/process/retrospective.md" ./docs/process/retrospective.md

echo "Private files linked from $MAIN_WORKTREE"
