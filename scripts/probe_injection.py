#!/usr/bin/env python3
"""Report whether a session's latest fleet-rules injection holds a rule or text.

Usage: probe_injection.py TRANSCRIPT.jsonl [--rule NAME.md] [--text STRING]

Reads the LAST SessionStart `hook_additional_context` attachment that carries
the fleet hook's output (a `<team-lead-fleet-rules>` block, inline or saved to
a file behind a `<persisted-output>` item). Earlier injections and sessions
merely talking about one are ignored.

One line out: PRESENT / ABSENT / SKIPPED / UNREADABLE / COULD NOT RUN, plus
the source. Exit 0 present, 1 absent/skipped/unreadable, 2 could not run.
"""
import argparse
import json
import re
import sys

BLOCK = "<team-lead-fleet-rules>"
SAVED = re.compile(r"Full output saved to: (\S+)")
SKIP_HDR = re.compile(r"^Already in your context verbatim.*?not repeated here:(.*)$", re.M)
MARKER = re.compile(r"^<!-- team-lead-fleet (rule|private rule): (\S+) -->$", re.M)
UNREADABLE = "RULE FILE UNREADABLE"


class CouldNotRun(Exception):
    pass


def fleet_item(content):
    """Return the item holding the fleet hook output, wherever it sits."""
    items = content if isinstance(content, list) else [content]
    for item in items:
        if not isinstance(item, str):
            continue
        if item.lstrip().startswith(BLOCK):
            return item
        if item.lstrip().startswith("<persisted-output>") and BLOCK in item:
            return item
    return None


def load_injection(path):
    """Return (text, source) of the latest fleet injection."""
    try:
        f = open(path, encoding="utf-8")
    except OSError as e:
        raise CouldNotRun(f"cannot read transcript: {e}")
    found = None
    with f:
        for line in f:
            if "hook_additional_context" not in line:
                continue
            try:
                att = json.loads(line).get("attachment", {})
            except (ValueError, AttributeError):
                continue
            if att.get("type") != "hook_additional_context" or att.get("hookEvent") != "SessionStart":
                continue
            item = fleet_item(att.get("content"))
            if item is not None:
                found = item
    if found is None:
        raise CouldNotRun("no SessionStart injection with fleet hook output in this transcript")
    if found.lstrip().startswith("<persisted-output>"):
        m = SAVED.search(found)
        if not m:
            raise CouldNotRun("persisted output has no 'Full output saved to' path")
        saved = m.group(1)
        try:
            with open(saved, encoding="utf-8") as sf:
                return sf.read(), saved
        except OSError as e:
            raise CouldNotRun(f"saved injection unreadable: {e}")
    return found, "inline"


def sections(text):
    """Map marker -> (kind, section text). Section runs to the next marker or the closing tag."""
    marks = list(MARKER.finditer(text))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((m.group(1), m.group(2), text[m.end():end]))
    return out, (marks[0].start() if marks else len(text))


def check(text, rule, needle):
    """Return (verdict, detail)."""
    secs, header_end = sections(text)
    scope = text
    detail = ""
    if rule:
        hits = [s for s in secs if s[1] == rule]
        if not hits:
            m = SKIP_HDR.search(text[:header_end])
            if m and rule in m.group(1).split():
                return "SKIPPED", f"{rule} listed as already in context, not injected"
            return "ABSENT", f"no marker for {rule}"
        kind, _, body = hits[-1]
        detail = f"{kind}: {rule}"
        if body.lstrip().lstrip("*").startswith(UNREADABLE):
            return "UNREADABLE", detail
        scope = body
    if needle is not None:
        if needle in scope:
            return "PRESENT", (detail + " + text" if detail else "text")
        return "ABSENT", (detail + ", " if detail else "") + "text not found"
    return "PRESENT", detail


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("transcript")
    ap.add_argument("--rule", help="rule file name, e.g. communication.md")
    ap.add_argument("--text", help="exact string that must appear (in the rule, if --rule is given)")
    args = ap.parse_args(argv)
    if args.rule is None and args.text is None:
        ap.error("give --rule, --text or both")
    try:
        text, source = load_injection(args.transcript)
    except CouldNotRun as e:
        print(f"COULD NOT RUN: {e}")
        return 2
    verdict, detail = check(text, args.rule, args.text)
    src = "inline" if source == "inline" else f"saved file {source}"
    print(f"{verdict} ({src}){': ' + detail if detail else ''}")
    return 0 if verdict == "PRESENT" else 1


if __name__ == "__main__":
    sys.exit(main())
