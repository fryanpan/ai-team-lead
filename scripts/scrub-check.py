#!/usr/bin/env python3
"""Pre-push leak scanner. Used by `.githooks/pre-push`.

Scans content for project-name / PII leaks BEFORE it leaves the local machine.
The principle: once a push lands on GitHub and a PR is opened against it, the
content is public-record forever (PR descriptions and commits can't be removed).
This gate fires at push time so a leak can be caught and fixed before that.

Two sources of patterns:
1. **Registry**: top-level keys under `projects:` in the repo's `registry.yaml`
   (or, if not present, the fleet registry at `~/dev/ai-team-lead/registry.yaml`).
   When a new project is added to the registry, its name is automatically protected.
2. **Denylist**: hand-curated patterns at `~/.config/team-lead/scrub-denylist.txt`.
   One pattern per line. Plain strings match literally (case-insensitive). Prefix
   with `/` for a regex. Lines starting with `#` are comments.
   **Enforced only when the repo being pushed to is public** — see
   `repo_is_public()`. These patterns are infrastructure identifiers that appear
   legitimately all over the fleet's private repos; a public repo is where they
   become permanent. Unknown visibility enforces.

Usage:
  scrub-check.py file [file...]            # scan named files
  scrub-check.py --diff-range A..B          # scan files changed in range
  scrub-check.py --staged                   # scan files in git index
  scrub-check.py --scan-all-tracked         # scan every tracked file (audit)

Exit codes: 0 = clean, 1 = leaks found, 2 = setup error.

Bypass entirely with `SCRUB_SKIP=1` (logged; use sparingly).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import List, Optional, Set, Tuple

FLEET_REGISTRY = os.path.expanduser("~/dev/ai-team-lead/registry.yaml")
DENYLIST_PATH = os.path.expanduser("~/.config/team-lead/scrub-denylist.txt")

# Text file extensions we scan. Everything else is skipped (binaries, lockfiles).
SCAN_EXTS = {
    ".md", ".py", ".sh", ".bash", ".zsh", ".yml", ".yaml", ".json", ".jsonc",
    ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss", ".txt", ".rst",
    ".toml", ".env", ".envrc",
}

# Specific paths to never scan (the scanner's own data files, gitignored docs).
SKIP_PATHS = {
    "docs/process/aggregation-log.md",
    "registry.yaml",
}


def repo_root() -> Optional[str]:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def main_repo_name() -> Optional[str]:
    """Name of the main repo, correct even from inside a linked worktree.

    `--git-common-dir` points at the main repo's `.git` regardless of which
    worktree we're in, so its parent directory is the real repo name.
    """
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    if not common:
        return None
    # ".git" -> resolve relative to cwd; "/path/to/repo/.git" -> /path/to/repo
    git_dir = os.path.abspath(common)
    if os.path.basename(git_dir) == ".git":
        return os.path.basename(os.path.dirname(git_dir))
    # Bare repos: /path/to/repo.git -> repo
    return os.path.basename(git_dir).removesuffix(".git") or None


def find_registry() -> Optional[str]:
    """Local registry.yaml at repo root, else fleet fallback."""
    root = repo_root()
    if root:
        local = os.path.join(root, "registry.yaml")
        if os.path.isfile(local):
            return local
    if os.path.isfile(FLEET_REGISTRY):
        return FLEET_REGISTRY
    return None


def load_project_names(registry_path: Optional[str]) -> Set[str]:
    """Top-level project keys under `projects:` in registry.yaml.

    Skips names that would cause heavy false-positive load:
      - Names without a hyphen AND under 6 chars (e.g. `tasks`, `crm`) — collide
        with common English words. the user can still flag them precisely via the
        hand-curated denylist if he wants stricter matching.
      - The current repo's own name (a repo legitimately self-references in its
        README, CLAUDE.md, plugin metadata, etc).
    """
    names: Set[str] = set()
    public: Set[str] = set()
    if not registry_path:
        return names
    in_projects = False
    current: Optional[str] = None
    with open(registry_path) as f:
        for line in f:
            if re.match(r"^projects:\s*$", line):
                in_projects = True
                continue
            if not in_projects:
                continue
            m = re.match(r"^  ([a-zA-Z][a-zA-Z0-9_-]*):\s*$", line)
            if m:
                current = m.group(1)
                names.add(current)
                continue
            if current and re.match(r"^    public:\s*true\b", line):
                public.add(current)
                continue
            # Hit a non-indented line that isn't blank/comment — projects block ended.
            if line and not line[0].isspace() and not line.lstrip().startswith("#"):
                break

    # Drop names that are too generic to safely match by themselves.
    names = {n for n in names if "-" in n or len(n) >= 6}

    # Drop projects the registry marks `public: true`. Per the fleet's
    # public-content-scrubbing rule, a name that already lives in a public
    # GitHub repo is safe to mention — flagging it protects nothing, and the
    # cost is real: the fleet's own public tooling (channel plugins, the
    # live-feedback plugin) is referenced constantly in docs, learnings, and
    # config, so leaving these in means the gate fires on nearly every push.
    # That is the SCRUB_SKIP-training failure described above, arriving by a
    # different door. Marking a project public is a deliberate operator act in
    # the (gitignored) registry, and it is the same assertion the flip-public
    # flow already requires.
    names -= public

    # Drop the current repo's own name — a repo's own README / CLAUDE.md / plugin
    # metadata legitimately mentions itself; we don't want to flag self-references.
    #
    # Use the MAIN repo's name, not the working tree's. In a linked worktree
    # (`.claude/worktrees/<branch>`), `--show-toplevel` is the worktree path, so
    # basename() is the branch's dir name and the real repo name never gets
    # discarded — every self-reference then trips the gate. A gate that cries wolf
    # in worktrees, where most fleet work happens, trains people into SCRUB_SKIP=1,
    # which is worse than the false positives.
    self_name = main_repo_name()
    if self_name:
        names.discard(self_name)

    return names


def load_denylist() -> List[Tuple[str, bool]]:
    """Return (pattern, is_regex) tuples. Missing file => empty list."""
    out: List[Tuple[str, bool]] = []
    if not os.path.isfile(DENYLIST_PATH):
        return out
    with open(DENYLIST_PATH) as f:
        for raw in f:
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("/"):
                body = s[1:]
                # Documented syntax is /regex/ — strip the CLOSING delimiter too.
                # Leaving it on silently appends a literal "/" to every pattern,
                # so /\btailb53801\b/ only matched when a slash happened to
                # follow. Found 2026-08-24, seeding the first real denylist.
                if body.endswith("/") and len(body) > 1:
                    body = body[:-1]
                out.append((body, True))
            else:
                out.append((s, False))
    return out


def repo_is_public(registry_path: Optional[str]) -> bool:
    """Is the repo we are pushing to itself public?

    The denylist protects infrastructure identifiers — the tailnet name, host
    names, workspace ids. Those appear constantly and legitimately in the
    fleet's PRIVATE repos (daily reviews, learnings, config), where mentioning
    them leaks nothing. In a PUBLIC repo the same string is a permanent leak.
    So the denylist is enforced on public repos and skipped on private ones:
    protection where exposure is forever, silence where it is not. Registry
    project names are unaffected — they are gated by their own `public: true`.

    Resolution order, most reliable first:
      1. The (gitignored) registry's `public: true` on the entry whose key,
         `path` basename, or `repo` basename matches this repo.
      2. `gh repo view --json visibility`, if gh is installed and authorized.

    UNKNOWN MEANS ENFORCE. A gate that fails open is not a gate, and the cost
    of being wrong is asymmetric: a false positive costs one push, a false
    negative is public record forever.
    """
    name = main_repo_name()
    if not name:
        return True

    if registry_path and os.path.isfile(registry_path):
        in_projects = False
        current: Optional[str] = None
        matches_current = False
        is_public = False
        with open(registry_path) as f:
            for line in f:
                if re.match(r"^projects:\s*$", line):
                    in_projects = True
                    continue
                if not in_projects:
                    continue
                m = re.match(r"^  ([a-zA-Z][a-zA-Z0-9_-]*):\s*$", line)
                if m:
                    if matches_current:
                        return is_public
                    current = m.group(1)
                    matches_current = current == name
                    is_public = False
                    continue
                if current:
                    m2 = re.match(r"^    (path|repo):\s*(\S+)", line)
                    if m2 and os.path.basename(m2.group(2).rstrip("/")) == name:
                        matches_current = True
                    if re.match(r"^    public:\s*true\b", line):
                        is_public = True
                if line and not line[0].isspace() and not line.lstrip().startswith("#"):
                    break
        if matches_current:
            return is_public

    try:
        out = subprocess.run(
            ["gh", "repo", "view", "--json", "visibility", "-q", ".visibility"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().upper() == "PUBLIC"
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    return True


def build_patterns(names: Set[str], denylist: List[Tuple[str, bool]]) -> List[Tuple[str, re.Pattern]]:
    """Compile all match patterns. Names use a hyphen-aware word boundary."""
    patterns: List[Tuple[str, re.Pattern]] = []
    for name in sorted(names):
        # (?<![\w-]) and (?![\w-]) keep e.g. `some-proj` from matching inside `super-some-proj-foo`.
        rx = re.compile(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", re.IGNORECASE)
        patterns.append((f"registry-project: {name}", rx))
    for raw, is_regex in denylist:
        try:
            body = raw if is_regex else re.escape(raw)
            patterns.append((f"denylist: {raw}", re.compile(body, re.IGNORECASE)))
        except re.error as e:
            print(f"[scrub-check] bad regex in denylist: {raw!r} ({e})", file=sys.stderr)
    return patterns


def should_scan(path: str) -> bool:
    if path in SKIP_PATHS:
        return False
    base = os.path.basename(path)
    # Allow extensionless files only if their name suggests text (e.g., .githooks/pre-push)
    ext = os.path.splitext(base)[1].lower()
    if ext in SCAN_EXTS:
        return True
    # Files with no extension that live in .githooks/ or scripts/ are typically text
    if not ext and ("/.githooks/" in "/" + path + "/" or path.startswith("scripts/")):
        return True
    return False


def scan_file(path: str, patterns: List[Tuple[str, re.Pattern]]) -> List[Tuple[int, str, str]]:
    """Return [(line_no, label, line_text)] of matches."""
    findings: List[Tuple[int, str, str]] = []
    try:
        with open(path, "rb") as f:
            data = f.read()
        text = data.decode("utf-8", errors="replace")
    except (OSError, IOError):
        return findings
    for line_no, line in enumerate(text.split("\n"), 1):
        # Skip lines that are intentional examples documenting the gate itself.
        if "scrub-allow" in line:
            continue
        for label, rx in patterns:
            if rx.search(line):
                findings.append((line_no, label, line))
                break  # one finding per line is enough
    return findings


def files_in_range(range_spec: str) -> List[str]:
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", range_spec],
            capture_output=True, text=True, check=True,
        ).stdout
        return [f for f in out.strip().split("\n") if f]
    except subprocess.CalledProcessError:
        return []


def files_staged() -> List[str]:
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, check=True,
        ).stdout
        return [f for f in out.strip().split("\n") if f]
    except subprocess.CalledProcessError:
        return []


def all_tracked_files() -> List[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            capture_output=True, text=True, check=True,
        ).stdout
        return [f for f in out.strip().split("\n") if f]
    except subprocess.CalledProcessError:
        return []


def main() -> int:
    if os.environ.get("SCRUB_SKIP") == "1":
        print("[scrub-check] SCRUB_SKIP=1 set — bypassing scan.", file=sys.stderr)
        return 0

    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        return 0

    if "--diff-range" in args:
        idx = args.index("--diff-range")
        if idx + 1 >= len(args):
            print("[scrub-check] --diff-range needs an argument", file=sys.stderr)
            return 2
        files = files_in_range(args[idx + 1])
    elif "--staged" in args:
        files = files_staged()
    elif "--scan-all-tracked" in args:
        files = all_tracked_files()
    else:
        files = args

    # Filter: keep only files we'd scan and that exist on disk.
    files = [f for f in files if should_scan(f) and os.path.isfile(f)]

    if not files:
        return 0

    registry = find_registry()
    project_names = load_project_names(registry)
    if repo_is_public(registry):
        denylist = load_denylist()
    else:
        denylist = []
    patterns = build_patterns(project_names, denylist)

    if not patterns:
        print(
            "[scrub-check] no patterns configured (no registry.yaml, no denylist) — skipping.",
            file=sys.stderr,
        )
        return 0

    total = 0
    files_with_findings = set()
    for f in files:
        for line_no, label, line in scan_file(f, patterns):
            if total == 0:
                print(f"[scrub-check] leaks detected:", file=sys.stderr)
            files_with_findings.add(f)
            snippet = line.strip()
            if len(snippet) > 100:
                snippet = snippet[:97] + "..."
            print(f"  {f}:{line_no}  ({label})", file=sys.stderr)
            print(f"    > {snippet}", file=sys.stderr)
            total += 1

    if total:
        print(
            f"\n[scrub-check] {total} leak(s) across {len(files_with_findings)} file(s). Push blocked.",
            file=sys.stderr,
        )
        print(
            "[scrub-check] Fix: replace with a generic placeholder, anonymize, or move content to a gitignored path.",
            file=sys.stderr,
        )
        print(
            "[scrub-check] Override (sparingly): SCRUB_SKIP=1 git push ...",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
