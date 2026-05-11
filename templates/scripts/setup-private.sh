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
CURRENT_DIR=$(pwd)
if [ "$CURRENT_DIR" = "$MAIN_WORKTREE" ]; then
  echo "Already in main worktree, nothing to link."
  exit 0
fi

# Registry (project list, team metadata, IDs)
ln -sf "$MAIN_WORKTREE/registry.yaml" ./registry.yaml

# Retrospectives (auto-generated project-specific content)
ln -sf "$MAIN_WORKTREE/docs/process/retrospective.md" ./docs/process/retrospective.md

echo "Private files linked from $MAIN_WORKTREE"
