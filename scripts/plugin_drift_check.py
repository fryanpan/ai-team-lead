#!/usr/bin/env python3
"""Does the fleet actually run the plugin that's in this repo?

WHY THIS EXISTS
---------------
The `team-lead-fleet` plugin installs from a local *directory* source, but what
peers load is a COPY at ~/.claude/plugins/cache/. Editing the repo changes
nothing for the fleet until someone runs a marketplace update — and there is no
error, no warning, and no external symptom when they diverge. An edited rule
simply has no effect, everywhere, silently.

That failure has now happened twice. In July the fleet ran a frozen snapshot
while main sat six weeks behind, and the fix logged at the time was "add a drift
check." Nobody built it. On 2026-08-03 three writing rules were edited and
distributed to nobody; the drift was noticed by accident while checking
something unrelated.

So: check it on a schedule instead of by luck. The token-watch runs this 3x/day.

Exit codes: 0 = fleet matches repo, 1 = drift, 2 = can't tell.

    python3 scripts/plugin_drift_check.py [--quiet]
"""
import hashlib
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_PLUGIN = os.path.join(REPO_ROOT, "plugin", "team-lead-fleet")
CACHE_ROOT = os.path.expanduser(
    "~/.claude/plugins/cache/team-lead-fleet/team-lead-fleet")

# Files that legitimately differ or don't travel with the plugin.
SKIP_NAMES = {".DS_Store"}


def cache_dir():
    """The ACTIVE installed version directory, or None.

    Do NOT pick by mtime. `claude plugin update` leaves the old version in place
    and drops an `.orphaned_at` marker in it — which TOUCHES that directory, so
    the superseded copy is the newest by mtime and an mtime sort selects exactly
    the wrong one. Measured 2026-08-04: right after updating 0.2.0 -> 0.3.0 this
    function returned 0.2.0 and the check reported drift against a version no
    session loads.

    Skip orphaned dirs, then take the highest semver.
    """
    if not os.path.isdir(CACHE_ROOT):
        return None
    versions = []
    for d in os.listdir(CACHE_ROOT):
        full = os.path.join(CACHE_ROOT, d)
        if not os.path.isdir(full):
            continue
        if os.path.exists(os.path.join(full, ".orphaned_at")):
            continue
        versions.append(d)
    if not versions:
        return None

    def key(v):
        parts = []
        for p in v.split("."):
            m = re.match(r"(\d+)", p)
            parts.append(int(m.group(1)) if m else 0)
        return parts + [0] * (3 - len(parts))

    versions.sort(key=key)
    return os.path.join(CACHE_ROOT, versions[-1])


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def tree(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            if fn in SKIP_NAMES:
                continue
            full = os.path.join(dirpath, fn)
            out[os.path.relpath(full, root)] = digest(full)
    return out


def uncommitted_plugin_changes():
    r = subprocess.run(["git", "status", "--porcelain", "plugin/"],
                       capture_output=True, text=True, cwd=REPO_ROOT)
    return [l for l in r.stdout.splitlines() if l.strip()]


def main():
    quiet = "--quiet" in sys.argv

    if not os.path.isdir(REPO_PLUGIN):
        print(f"[drift] CANNOT CHECK: repo plugin dir missing at {REPO_PLUGIN}")
        return 2
    cache = cache_dir()
    if cache is None:
        print(f"[drift] CANNOT CHECK: no installed plugin under {CACHE_ROOT}")
        return 2

    repo_files, cache_files = tree(REPO_PLUGIN), tree(cache)

    changed = sorted(k for k in repo_files.keys() & cache_files.keys()
                     if repo_files[k] != cache_files[k])
    only_repo = sorted(repo_files.keys() - cache_files.keys())
    only_cache = sorted(cache_files.keys() - repo_files.keys())

    if not (changed or only_repo or only_cache):
        if not quiet:
            print(f"[drift] OK — fleet runs what the repo has "
                  f"({len(repo_files)} files, {os.path.basename(cache)})")
        return 0

    print(f"[drift] FLEET IS RUNNING DIFFERENT CONTENT than {REPO_PLUGIN}")
    print(f"        installed: {cache}")
    for f in changed:
        print(f"        differs      {f}")
    for f in only_repo:
        print(f"        not deployed {f}")
    for f in only_cache:
        print(f"        stale copy   {f}")

    dirty = uncommitted_plugin_changes()
    if dirty:
        print("\n        NOTE: plugin/ has uncommitted changes. Commit BEFORE updating —")
        print("        installing from a dirty working tree is how the fleet ended up")
        print("        six weeks behind main in July.")
        for l in dirty:
            print(f"          {l}")

    print("\n        Fix — FOUR STEPS, and step 3 is NOT the command you think:")
    print("          1. BUMP the version in BOTH plugin/team-lead-fleet/.claude-plugin/")
    print("             plugin.json AND plugin/.claude-plugin/marketplace.json (the")
    print("             `claude plugin tag` subcommand exists to check they agree)")
    print("          2. commit + merge to main")
    print("          3. claude plugin update team-lead-fleet@team-lead-fleet")
    print("          4. restart each peer (a session reads the cache at startup)")
    print("\n        Measured 2026-08-04, each of these copies NOTHING:")
    print("          - claude plugin marketplace update team-lead-fleet")
    print("              -> '✔ Successfully updated marketplace' and exit 0. It updates")
    print("                 the MARKETPLACE, not the plugin. Believing this line is how")
    print("                 the fleet ran six weeks behind main in July.")
    print("          - claude plugin install team-lead-fleet@team-lead-fleet")
    print("              -> '✔ already installed'")
    print("          - starting a fresh session")
    print("              -> cache untouched")
    print("        Only `claude plugin update` copies the files. It reports a version")
    print("        delta ('updated from 0.2.0 to 0.3.0'), so the bump in step 1 is")
    print("        very likely required for it to act — that part is inferred from the")
    print("        message, not measured.")
    print("\n        After updating, the OLD version dir stays behind with an")
    print("        .orphaned_at marker. Do not select the cache dir by mtime.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
