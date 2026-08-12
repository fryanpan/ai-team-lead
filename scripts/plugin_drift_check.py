#!/usr/bin/env python3
"""Does the fleet actually run the plugins that the repos have?

WHY THIS EXISTS
---------------
A directory-source plugin installs from a local repo, but what sessions load is
a COPY at ~/.claude/plugins/cache/. Editing the repo changes nothing until
someone runs `claude plugin update` — and there is no error, no warning, and no
external symptom when they diverge. An edited rule simply has no effect,
everywhere, silently.

That failure has now happened three times:

  * July 2026 — the fleet ran a frozen snapshot while main sat six weeks behind.
    The fix logged at the time was "add a drift check." Nobody built it.
  * 2026-08-03 — three writing rules were edited and distributed to nobody.
    Noticed by accident while checking something unrelated.
  * 2026-08-10 — live-feedback had been installed at 0.0.1 since May 9 while the
    repo moved 124 commits ahead, 25 of them touching the shipped plugin. The
    installed .mcp.json still named a PATH-resolved binary that the plugin had
    stopped shipping, so every session that resolved from cache failed to start
    the MCP server with a bare `ENOENT` and no other diagnostic.

The third one is why this script is no longer hardcoded to team-lead-fleet. The
checker existed, was green, and ran three times a day — at ONE plugin, while a
different plugin was three months stale. A check that only looks where you
already looked is not a check.

WHAT IT CHECKS, per directory-source plugin
  1. Content — every file in the repo's plugin dir vs the installed copy.
  2. Version — the repo manifest version vs the installed version.
  3. Release lag — commits touching the plugin source since the version last
     changed. This is the one that catches "shipped 25 changes, bumped nothing,"
     which content-hashing alone reports only AFTER someone deploys.

Exit codes: 0 = everything matches, 1 = drift, 2 = can't tell.

    python3 scripts/plugin_drift_check.py [--quiet]
"""
import hashlib
import json
import os
import re
import subprocess
import sys

CACHE_ROOT = os.path.expanduser("~/.claude/plugins/cache")
CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")

# Files that legitimately differ or don't travel with the plugin.
SKIP_NAMES = {".DS_Store"}
# Machine-local config and backups. These are gitignored by construction — a
# `.env` holds the machine's own secrets and MUST NOT be committed — so the
# remedy this checker prescribes ("commit, then update") can never apply to
# them, and flagging them produces a red that no correct action can clear. A
# permanently-red check is one you learn to ignore, which costs the real drift
# signal sitting next to it. Fixing a local .env port must not read as "the
# fleet is running different code."
SKIP_PATTERNS = (".env", ".env.", ".bak-", ".backup")
# Never walk these into a content comparison — they are build/VCS noise that the
# installer does not copy, and including them produces permanent false drift.
SKIP_DIRS = {".git", "node_modules", ".claude-worktrees", "__pycache__"}


def sh(args, cwd=None):
    try:
        r = subprocess.run(args, capture_output=True, text=True, cwd=cwd,
                           timeout=60)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def directory_marketplaces():
    """[(marketplace_name, source_dir)] for Directory-source marketplaces.

    GitHub-source marketplaces are excluded on purpose: their canonical content
    is upstream, not a local working tree, so "repo vs cache" is not a question
    we can answer or act on locally.
    """
    out = []
    text = sh([CLAUDE_BIN, "plugin", "marketplace", "list"])
    name = None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^❯\s+(\S+)", s)
        if m:
            name = m.group(1)
            continue
        m = re.match(r"^Source:\s+Directory\s+\((.+)\)\s*$", s)
        if m and name:
            out.append((name, os.path.realpath(os.path.expanduser(m.group(1)))))
            name = None
    return out


def marketplace_plugins(source_dir):
    """[(plugin_name, abs_plugin_source_dir, manifest_version)]"""
    mpath = os.path.join(source_dir, ".claude-plugin", "marketplace.json")
    if not os.path.isfile(mpath):
        return []
    try:
        data = json.load(open(mpath))
    except Exception:
        return []
    out = []
    for p in data.get("plugins", []):
        pname = p.get("name")
        src = p.get("source") or "."
        if not pname:
            continue
        pdir = os.path.realpath(os.path.join(source_dir, src))
        ver = None
        jpath = os.path.join(pdir, ".claude-plugin", "plugin.json")
        if os.path.isfile(jpath):
            try:
                ver = json.load(open(jpath)).get("version")
            except Exception:
                pass
        out.append((pname, pdir, ver))
    return out


def semver_key(v):
    parts = []
    for p in str(v).split("."):
        m = re.match(r"(\d+)", p)
        parts.append(int(m.group(1)) if m else 0)
    return parts + [0] * (3 - len(parts))


def active_cache_dir(marketplace, plugin):
    """The ACTIVE installed version directory, or None.

    Do NOT pick by mtime. `claude plugin update` leaves the old version in place
    and drops an `.orphaned_at` marker in it — which TOUCHES that directory, so
    the superseded copy is the newest by mtime and an mtime sort selects exactly
    the wrong one. Measured 2026-08-04: right after updating 0.2.0 -> 0.3.0 this
    returned 0.2.0 and the check reported drift against a version no session
    loads.

    Skip orphaned dirs, then take the highest semver.
    """
    root = os.path.join(CACHE_ROOT, marketplace, plugin)
    if not os.path.isdir(root):
        return None, None
    versions = [d for d in os.listdir(root)
                if os.path.isdir(os.path.join(root, d))
                and not os.path.exists(os.path.join(root, d, ".orphaned_at"))]
    if not versions:
        return None, None
    versions.sort(key=semver_key)
    return os.path.join(root, versions[-1]), versions[-1]


def digest(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return "<unreadable>"
    return h.hexdigest()


def tree(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn in SKIP_NAMES:
                continue
            if fn.startswith(SKIP_PATTERNS) or any(p in fn for p in (".bak-", ".backup")):
                continue
            full = os.path.join(dirpath, fn)
            out[os.path.relpath(full, root)] = digest(full)
    return out


def release_lag(plugin_dir):
    """(n_commits, last_bump_subject) since plugin.json's version last changed.

    Catches the failure content-hashing cannot: a repo whose plugin has moved
    many commits while the version string sat still. `claude plugin update` keys
    on the version, so an unbumped change deploys nothing while reporting
    success.
    """
    manifest = os.path.join(plugin_dir, ".claude-plugin", "plugin.json")
    if not os.path.isfile(manifest):
        return None, None
    repo = sh(["git", "rev-parse", "--show-toplevel"], cwd=plugin_dir).strip()
    if not repo:
        return None, None
    rel = os.path.relpath(manifest, repo)
    log = sh(["git", "log", "-1", "--format=%H%x00%s", "--", rel], cwd=repo).strip()
    if not log:
        return None, None
    sha, _, subject = log.partition("\x00")
    rel_plugin = os.path.relpath(plugin_dir, repo)
    count = sh(["git", "rev-list", "--count", f"{sha}..HEAD", "--", rel_plugin],
               cwd=repo).strip()
    try:
        return int(count), subject
    except ValueError:
        return None, subject


def uncommitted(plugin_dir):
    repo = sh(["git", "rev-parse", "--show-toplevel"], cwd=plugin_dir).strip()
    if not repo:
        return []
    rel = os.path.relpath(plugin_dir, repo)
    out = sh(["git", "status", "--porcelain", "--", rel], cwd=repo)
    return [l for l in out.splitlines() if l.strip()]


def fix_instructions(marketplace, plugin):
    return f"""
        Fix — FOUR STEPS, and step 3 is NOT the command you think:
          1. BUMP the version in BOTH the plugin's .claude-plugin/plugin.json
             AND the marketplace's .claude-plugin/marketplace.json (the
             `claude plugin tag` subcommand exists to check they agree)
          2. commit + merge to main
          3. claude plugin update {plugin}@{marketplace}
          4. restart each session (a session reads the cache at startup, and a
             reconnect re-uses the config it already resolved — it cannot pick
             up a new command path)

        Measured 2026-08-04, each of these copies NOTHING:
          - claude plugin marketplace update <name>
              -> '✔ Successfully updated marketplace' and exit 0. It updates the
                 MARKETPLACE, not the plugin. Believing this line is how the
                 fleet ran six weeks behind main in July.
          - claude plugin install <plugin>@<marketplace>   -> 'already installed'
          - starting a fresh session                        -> cache untouched
        Only `claude plugin update` copies the files.

        After updating, the OLD version dir stays behind with an .orphaned_at
        marker. Do not select the cache dir by mtime."""


def check_plugin(marketplace, plugin, plugin_dir, manifest_ver, quiet):
    """Returns 0 ok / 1 drift / 2 cannot check. Prints its own findings."""
    label = f"{plugin}@{marketplace}"

    if not os.path.isdir(plugin_dir):
        print(f"[drift] {label}: CANNOT CHECK — source dir missing at {plugin_dir}")
        return 2

    cache, installed_ver = active_cache_dir(marketplace, plugin)
    if cache is None:
        if not quiet:
            print(f"[drift] {label}: not installed — skipping")
        return 0

    problems = []

    if manifest_ver and installed_ver and manifest_ver != installed_ver:
        problems.append(
            f"VERSION MISMATCH — repo says {manifest_ver}, installed is {installed_ver}")

    repo_files, cache_files = tree(plugin_dir), tree(cache)
    changed = sorted(k for k in repo_files.keys() & cache_files.keys()
                     if repo_files[k] != cache_files[k])
    only_repo = sorted(repo_files.keys() - cache_files.keys())
    only_cache = sorted(cache_files.keys() - repo_files.keys())
    if changed or only_repo or only_cache:
        problems.append("CONTENT DIFFERS from the installed copy")

    lag, bump_subject = release_lag(plugin_dir)
    if lag:
        problems.append(
            f"RELEASE LAG — {lag} commit(s) touched this plugin since the version "
            f"last changed ({bump_subject!r}). `claude plugin update` keys on the "
            f"version, so these deploy NOTHING until it is bumped.")

    if not problems:
        if not quiet:
            print(f"[drift] {label}: OK — {len(repo_files)} files, {installed_ver}")
        return 0

    print(f"[drift] {label}: NOT WHAT THE REPO HAS")
    print(f"        source:    {plugin_dir}")
    print(f"        installed: {cache}")
    for p in problems:
        print(f"        ! {p}")
    for f in changed[:20]:
        print(f"        differs      {f}")
    for f in only_repo[:20]:
        print(f"        not deployed {f}")
    for f in only_cache[:20]:
        print(f"        stale copy   {f}")
    extra = max(0, len(changed) - 20) + max(0, len(only_repo) - 20) + \
        max(0, len(only_cache) - 20)
    if extra:
        print(f"        ... and {extra} more")

    dirty = uncommitted(plugin_dir)
    if dirty:
        print("\n        NOTE: the plugin source has uncommitted changes. Commit BEFORE")
        print("        updating — installing from a dirty working tree is how the fleet")
        print("        ended up six weeks behind main in July.")
        for l in dirty[:10]:
            print(f"          {l}")

    print(fix_instructions(marketplace, plugin))
    return 1


def main():
    quiet = "--quiet" in sys.argv

    markets = directory_marketplaces()
    if not markets:
        print("[drift] CANNOT CHECK: no directory-source marketplaces found "
              f"(is {CLAUDE_BIN} present?)")
        return 2

    worst = 0
    checked = 0
    for mname, mdir in markets:
        plugins = marketplace_plugins(mdir)
        if not plugins:
            print(f"[drift] {mname}: CANNOT CHECK — no readable marketplace.json "
                  f"under {mdir}")
            worst = max(worst, 2)
            continue
        for pname, pdir, ver in plugins:
            rc = check_plugin(mname, pname, pdir, ver, quiet)
            checked += 1
            # Drift (1) outranks can't-tell (2): a known problem beats an unknown.
            worst = 1 if (rc == 1 or worst == 1) else max(worst, rc)

    if worst == 0 and not quiet:
        print(f"[drift] ALL CLEAR — {checked} directory-source plugin(s) checked")
    return worst


if __name__ == "__main__":
    sys.exit(main())
