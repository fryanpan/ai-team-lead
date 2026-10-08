#!/usr/bin/env python3
"""Find Claude model ids in use across every registered project.

UPDATE `CURRENT` BELOW WHEN A NEW MODEL SHIPS. Any id not in that set is
flagged, so a stale set reports current models as old and an old set hides
real drift.

Usage:
  model_currency_check.py [--registry PATH]

Reads each `path:` from the registry (default: registry.yaml at the repo
root, or $REGISTRY), expands `~`, skips missing paths, and greps code and
config files for model ids. Docs, tests, eval/fixture/data dirs and build
output are skipped.

Exit codes (the outcome is also printed on the last line):
  0  every id found is in CURRENT
  1  at least one id is not in CURRENT
  2  could not run (registry missing or unreadable, or no project path found)
"""
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

CURRENT = {
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
}

EXTS = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".yaml", ".yml", ".toml", ".sh"}
SKIP_DIRS = {
    "node_modules", ".git", "dist", "build", ".next", "worktrees", "venv", ".venv",
    "venvs", "env", "__pycache__", "coverage", "docs", "eval", "evals", "data",
    "fixtures", "benchmark", "benchmarks", "research", ".scratch",
}
MODEL_RE = re.compile(
    r"claude-(?:(?:opus|sonnet|haiku|fable|mythos)-[0-9][-0-9a-z]*|3[-0-9a-z]*)"
)
TEST_FILE_RE = re.compile(r"(^test_.*|.*_test\.py|.*\.(test|spec)\.[jt]sx?|^conftest\.py)$")
MAX_BYTES = 2_000_000


def registry_paths(reg: Path):
    paths = []
    for line in reg.read_text().splitlines():
        m = re.match(r"\s+path:\s*(\S+)", line)
        if m:
            paths.append(m.group(1).strip("'\""))
    return paths


def is_test(path: Path, root: Path) -> bool:
    if TEST_FILE_RE.match(path.name):
        return True
    return any(p in ("test", "tests", "__tests__") for p in path.relative_to(root).parts[:-1])


def scan(root: Path):
    found = defaultdict(list)  # id -> [file:line]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix not in EXTS or is_test(p, root):
                continue
            try:
                if p.stat().st_size > MAX_BYTES:
                    continue
                text = p.read_text(errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                for mid in MODEL_RE.findall(line):
                    found[mid].append(f"{p.relative_to(root)}:{i}")
    return found


def main() -> int:
    args = sys.argv[1:]
    reg = os.environ.get("REGISTRY") or str(Path(__file__).resolve().parent.parent / "registry.yaml")
    if args[:1] == ["--registry"] and len(args) > 1:
        reg = args[1]
    reg = Path(reg).expanduser()
    if not reg.is_file():
        print(f"model-currency: registry not found at {reg}")
        print("RESULT: COULD NOT RUN (exit 2)")
        return 2
    try:
        paths = registry_paths(reg)
    except OSError as e:
        print(f"model-currency: cannot read registry: {e}")
        print("RESULT: COULD NOT RUN (exit 2)")
        return 2
    roots = {Path(raw).expanduser() for raw in paths if Path(raw).expanduser().is_dir()}
    if not roots:
        print("model-currency: no existing project path in the registry")
        print("RESULT: COULD NOT RUN (exit 2)")
        return 2

    flagged = 0
    for root in sorted(roots):
        found = scan(root)
        if not found:
            continue
        print(f"\n{root}")
        for mid in sorted(found):
            hits = found[mid]
            ok = mid in CURRENT
            flagged += not ok
            print(f"  {mid}  x{len(hits)}{'' if ok else '  <-- NOT CURRENT'}")
            print(f"      {', '.join(hits[:3])}")
    print(f"\nscanned {len(roots)} project(s); {flagged} non-current id(s) flagged")
    if flagged:
        print("RESULT: FLAGGED (exit 1)")
        return 1
    print("RESULT: CLEAN (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
