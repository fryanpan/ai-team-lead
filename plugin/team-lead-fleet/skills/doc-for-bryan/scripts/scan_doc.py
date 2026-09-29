#!/usr/bin/env python3
"""Mechanical scan for a markdown doc going to Bryan for review.

Checks: word cap, raw board ids, bare URLs, and percentages with no stated
denominator on the same line. Fenced code blocks are skipped.

Exit codes are three states, so a scan that never ran cannot read as clean:
  0  PASSED       every check ran and found nothing
  1  HITS         every check ran; the hits are listed
  2  COULD NOT RUN  the file could not be read; nothing was checked
"""
import argparse
import re
import sys

# Board ids: a one-letter kind, a dash, then an opaque 10+ char token.
RAW_ID = re.compile(r"(?<![\w/-])[tdw]-[A-Za-z0-9_-]{10,}\b")
URL = re.compile(r"https?://\S+")
PERCENT = re.compile(r"\d+(?:\.\d+)?\s?%")
# Anything that states what a rate is out of.
DENOMINATOR = re.compile(r"\bof\s+(?:the\s+)?[\d,.]+|\bout of\b|\d\s*/\s*\d|\bn\s*=\s*\d", re.I)


def is_linked(line, start):
    """True when the URL at `start` is a markdown link target or an autolink."""
    return line[max(0, start - 2):start] == "](" or line[max(0, start - 1):start] == "<"


def scan(text, max_words):
    hits = []
    in_fence = False
    words = 0
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        words += len(line.split())
        for m in RAW_ID.finditer(line):
            hits.append(f"line {n}: raw id {m.group(0)} (name the thing and link it)")
        for m in URL.finditer(line):
            if not is_linked(line, m.start()):
                hits.append(f"line {n}: bare URL {m.group(0)} (put it behind link text)")
        if PERCENT.search(line) and not DENOMINATOR.search(line):
            hits.append(f"line {n}: rate with no denominator: {line.strip()[:80]}")
    if words > max_words:
        hits.insert(0, f"length: {words} words, cap {max_words}")
    return words, hits


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("path")
    p.add_argument("--max-words", type=int, default=1500)
    args = p.parse_args(argv)
    try:
        with open(args.path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"scan COULD NOT RUN on {args.path}: {e}. Nothing was checked.")
        return 2
    words, hits = scan(text, args.max_words)
    if not hits:
        print(f"scan PASSED on {args.path}: {words} words (cap {args.max_words}), 0 hits")
        return 0
    print(f"scan HITS on {args.path}: {words} words (cap {args.max_words}), {len(hits)} hits")
    for h in hits:
        print(f"  {h}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
