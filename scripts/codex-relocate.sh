#!/bin/bash
# Codex's Homebrew cask binary hangs at process start from its Caskroom path --
# parked in the dynamic loader, zero CPU, no output, no error. The same bytes run
# instantly from elsewhere, so this mirrors the cask payload out of the Caskroom
# and points PATH at the copy.
#
# The whole payload is mirrored, not just the binary: codex spawns
# codex-code-mode-host from beside its own executable, and reads codex-path/ and
# codex-resources/ as siblings of bin/. Copying the binary alone gets you a
# working `codex --version` and a broken `codex exec`.
#
# Re-run after `brew upgrade codex`. Until you do, the new version sits in the
# Caskroom shadowed by this copy and the fleet silently keeps running the old one.
set -euo pipefail

CASKROOM=/opt/homebrew/Caskroom/codex
DEST="$HOME/.local/codex"
BIN="$HOME/.local/bin"

[ -d "$CASKROOM" ] || { echo "no codex cask at $CASKROOM" >&2; exit 1; }

version=$(ls -1 "$CASKROOM" | grep -E '^[0-9]+\.' | sort -V | tail -1)
[ -n "$version" ] || { echo "no versioned payload in $CASKROOM" >&2; exit 1; }

if [ -e "$DEST/$version" ]; then
    echo "already mirrored: $version"
else
    mkdir -p "$DEST"
    cp -R "$CASKROOM/$version" "$DEST/$version.partial"
    mv "$DEST/$version.partial" "$DEST/$version"
    echo "mirrored $version"
fi

mkdir -p "$BIN"
for exe in codex codex-code-mode-host; do
    [ -f "$DEST/$version/bin/$exe" ] || continue
    ln -sfn "$DEST/$version/bin/$exe" "$BIN/$exe"
done

# Prove it, rather than trusting the copy. A hang here means the relocation did
# not help and the cause is not what this script assumes -- do not paper over it.
got=$("$BIN/codex" --version </dev/null 2>&1) || { echo "FAILED to run after relocate" >&2; exit 1; }
echo "$got"
case "$got" in
    *"$version"*) echo "OK: PATH codex is $version" ;;
    *) echo "MISMATCH: expected $version, got '$got'" >&2; exit 1 ;;
esac
