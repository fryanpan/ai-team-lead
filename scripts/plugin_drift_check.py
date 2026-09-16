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

  * 2026-09-15 — the fleet plugin's marketplace moved from a local directory to
    GitHub, and this checker dropped it from coverage without a word. It had
    excluded GitHub sources on purpose, on the reasoning that their canonical
    content is upstream. But the repo is checked out locally, so the comparison
    was answerable all along, and a plugin that stops being mentioned reads
    exactly like a plugin with no drift.

The third one is why this script is no longer hardcoded to team-lead-fleet. The
checker existed, was green, and ran three times a day — at ONE plugin, while a
different plugin was three months stale. A check that only looks where you
already looked is not a check. The fourth is the same lesson from the other
side: silence is not a pass.

WHAT IT CHECKS, per plugin

For a directory-source marketplace the canonical content is the working tree.
For a GitHub-source one it is origin/main in the local clone, because that is
what `claude plugin update` will fetch — comparing the working tree there would
report every edit in progress as drift and miss a commit that was never pushed.
A GitHub marketplace with no local clone is reported as not checked, and does
not fail the run: an upstream marketplace nobody here edits would otherwise be
red on every run, and a line that is always red stops being read.
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
import tempfile

CACHE_ROOT = os.path.expanduser("~/.claude/plugins/cache")
CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")

# Files that legitimately differ or don't travel with the plugin.
SKIP_NAMES = {".DS_Store", ".orphaned_at"}
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
SKIP_DIRS = {".git", "node_modules", ".claude-worktrees", "__pycache__",
             ".in_use", ".venv", "venv", "dist", "build"}


def sh(args, cwd=None):
    try:
        r = subprocess.run(args, capture_output=True, text=True, cwd=cwd,
                           timeout=60)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


# Where clones live. Each entry is expanded and realpath'd, so "~/dev" already
# covers the physical path it resolves to on a machine whose home is a firmlink
# — do not add that resolved path as a second entry. This repo is public and the
# pre-push leak gate rejects an absolute home path written out in full.
DEV_ROOTS = (os.environ.get("FLEET_DEV_ROOT") or "~/dev",)


def local_checkout_for(repo_slug):
    """A local clone whose origin is repo_slug, or None.

    A GitHub-source marketplace's canonical content is a branch on GitHub, but
    on this machine the same repo is usually checked out under ~/dev. When it
    is, the check is answerable: compare the cache against that checkout's
    origin/main. When it is not, say so rather than staying silent.
    """
    want = repo_slug.lower().removesuffix(".git")
    for root in DEV_ROOTS:
        root = os.path.realpath(os.path.expanduser(root))
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if not os.path.isdir(os.path.join(d, ".git")):
                continue
            url = sh(["git", "remote", "get-url", "origin"], cwd=d).strip().lower()
            if not url:
                continue
            url = url.removesuffix(".git")
            if url.endswith(":" + want) or url.endswith("/" + want):
                return os.path.realpath(d)
    return None


def marketplaces():
    """[(name, source_dir_or_None, kind, detail)] for every configured marketplace.

    kind is "directory" (canonical content is the working tree) or "github"
    (canonical content is origin/main in the local clone, when there is one).
    GitHub sources used to be dropped here entirely, which meant moving a
    marketplace to GitHub silently removed it from drift coverage — the plugin
    stopped being reported at all, which reads exactly like "no drift".
    """
    out = []
    text = sh([CLAUDE_BIN, "plugin", "marketplace", "list"])
    name = None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^\u276f\s+(\S+)", s)
        if m:
            name = m.group(1)
            continue
        m = re.match(r"^Source:\s+Directory\s+\((.+)\)\s*$", s)
        if m and name:
            out.append((name, os.path.realpath(os.path.expanduser(m.group(1))),
                        "directory", m.group(1)))
            name = None
            continue
        m = re.match(r"^Source:\s+GitHub\s+\((.+)\)\s*$", s)
        if m and name:
            slug = m.group(1).strip()
            out.append((name, local_checkout_for(slug), "github", slug))
            name = None
    return out


def marketplace_plugins(source_dir):
    """[(plugin_name, abs_plugin_source_dir, manifest_version)]"""
    mpath = os.path.join(source_dir, ".claude-plugin", "marketplace.json")
    if not os.path.isfile(mpath):
        # A repo can BE a single plugin: .claude-plugin/plugin.json at the root
        # and no marketplace manifest. Returning [] there reported "no readable
        # marketplace.json", which is a cannot-check for a repo that is in fact
        # perfectly checkable.
        jpath = os.path.join(source_dir, ".claude-plugin", "plugin.json")
        if os.path.isfile(jpath):
            try:
                man = json.load(open(jpath))
            except Exception:
                return []
            if man.get("name"):
                return [(man["name"], os.path.realpath(source_dir),
                         man.get("version"))]
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


def release_lag(plugin_dir, ref="HEAD"):
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
    log = sh(["git", "log", "-1", "--format=%H%x00%s", ref, "--", rel], cwd=repo).strip()
    if not log:
        return None, None
    sha, _, subject = log.partition("\x00")
    rel_plugin = os.path.relpath(plugin_dir, repo)
    count = sh(["git", "rev-list", "--count", f"{sha}..{ref}", "--", rel_plugin],
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


def ref_tree(plugin_dir, ref):
    """tree() of plugin_dir as it exists at `ref`, or None if that can't be read.

    For a GitHub-source marketplace the fleet loads what is on the branch, not
    what is in the working tree, so the working tree is the wrong thing to
    compare against — it would report drift for every edit in progress and miss
    an edit that was committed but never pushed.
    """
    repo = sh(["git", "rev-parse", "--show-toplevel"], cwd=plugin_dir).strip()
    if not repo:
        return None, None
    rel = os.path.relpath(plugin_dir, repo)
    tmp = tempfile.mkdtemp(prefix="drift-")
    try:
        proc = subprocess.run(["git", "archive", ref, rel], cwd=repo,
                              capture_output=True)
        if proc.returncode != 0 or not proc.stdout:
            return None, None
        tar = subprocess.run(["tar", "-x", "-C", tmp], input=proc.stdout,
                             capture_output=True)
        if tar.returncode != 0:
            return None, None
        root = os.path.join(tmp, rel)
        if not os.path.isdir(root):
            return None, None
        return tree(root), root
    finally:
        pass


def ref_manifest_version(plugin_dir, ref):
    """The plugin version as of `ref`, or None if it cannot be read.

    The working tree's manifest is NOT what ships. A clone can sit many commits
    behind its own origin/main — checked out at an old release, or just never
    pulled — and reading the version from disk then reports the checkout's age
    as though it were the deployed state. Read it from the ref being compared.
    """
    try:
        root = sh(["git", "rev-parse", "--show-toplevel"], cwd=plugin_dir).strip()
        rel = os.path.relpath(
            os.path.join(plugin_dir, ".claude-plugin", "plugin.json"), root)
        return json.loads(sh(["git", "show", f"{ref}:{rel}"], cwd=plugin_dir)).get("version")
    except Exception:
        return None


def check_plugin(marketplace, plugin, plugin_dir, manifest_ver, quiet, ref=None):
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

    repo_files = None
    if ref:
        repo_files, _ = ref_tree(plugin_dir, ref)
        if repo_files is None:
            print(f"[drift] {label}: CANNOT CHECK — {plugin_dir} has no readable "
                  f"{ref}. Fetch the repo, then re-run.")
            return 2
        manifest_ver = ref_manifest_version(plugin_dir, ref) or manifest_ver

    if manifest_ver and installed_ver and manifest_ver != installed_ver:
        if semver_key(installed_ver) > semver_key(manifest_ver):
            print(f"[drift] {label}: CANNOT CHECK — the installed copy is "
                  f"{installed_ver} but {plugin_dir}'s {ref or 'HEAD'} is only "
                  f"{manifest_ver}. The local clone is behind what is deployed, so "
                  f"a diff here would report the clone's staleness as drift. "
                  f"Fetch the repo, then re-run.")
            return 2
        problems.append(
            f"VERSION MISMATCH — repo says {manifest_ver}, installed is {installed_ver}")

    cache_files = tree(cache)
    if repo_files is None:
        repo_files = tree(plugin_dir)
    changed = sorted(k for k in repo_files.keys() & cache_files.keys()
                     if repo_files[k] != cache_files[k])
    only_repo = sorted(repo_files.keys() - cache_files.keys())
    only_cache = sorted(cache_files.keys() - repo_files.keys())
    if changed or only_repo or only_cache:
        problems.append("CONTENT DIFFERS from the installed copy")

    lag, bump_subject = release_lag(plugin_dir, ref or "HEAD")
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
    print(f"        source:    {plugin_dir}" + (f" @ {ref}" if ref else ""))
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

    markets = marketplaces()
    if not markets:
        print("[drift] CANNOT CHECK: no marketplaces found "
              f"(is {CLAUDE_BIN} present?)")
        return 2

    worst = 0
    checked = 0
    for mname, mdir, kind, detail in markets:
        if mdir is None:
            # No local clone means no local copy of what the cache SHOULD hold.
            # For an upstream marketplace nobody here edits that is the normal
            # state, so say it plainly and do not fail the run — a line that is
            # red on every run stops being read.
            if not quiet:
                print(f"[drift] {mname}: not checked — GitHub marketplace {detail} "
                      f"has no local clone, so there is nothing to diff the cache "
                      f"against. Clone it under ~/dev to bring it into coverage.")
            continue
        plugins = marketplace_plugins(mdir)
        if not plugins:
            print(f"[drift] {mname}: CANNOT CHECK — no readable marketplace.json "
                  f"under {mdir}")
            worst = max(worst, 2)
            continue
        ref = "origin/main" if kind == "github" else None
        for pname, pdir, ver in plugins:
            rc = check_plugin(mname, pname, pdir, ver, quiet, ref=ref)
            checked += 1
            # Drift (1) outranks can't-tell (2): a known problem beats an unknown.
            worst = 1 if (rc == 1 or worst == 1) else max(worst, rc)

    if worst == 0 and not quiet:
        print(f"[drift] ALL CLEAR — {checked} plugin(s) checked")
    return worst


if __name__ == "__main__":
    sys.exit(main())
