"""Tests for scripts/probe_injection.py.

Run: uv run --with pytest python -m pytest scripts/test_probe_injection.py

Fixture transcripts are built in a temp dir; the real marker formats come
from plugin/team-lead-fleet/hooks/session-start.sh.
"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "probe_injection.py"

HEADER = "<team-lead-fleet-rules>\n\nThe following rules apply.\n"


def block(rules, skipped=(), unreadable=(), kind="rule"):
    out = HEADER
    if skipped:
        out += "\nAlready in your context verbatim as project instructions, so not repeated here: " + " ".join(skipped) + "\n"
    for name, body in rules:
        out += f"\n\n<!-- team-lead-fleet {kind}: {name} -->\n"
        out += "\n**RULE FILE UNREADABLE — tell the user.**\n" if name in unreadable else f"\n{body}\n"
    return out + "\n\n</team-lead-fleet-rules>"


def line(content, event="SessionStart", typ="hook_additional_context"):
    return json.dumps({"attachment": {"type": typ, "hookEvent": event, "content": content}})


def persisted(path, preview):
    return f"<persisted-output>\nOutput too large (101KB). Full output saved to: {path}\n\nPreview (first 2KB):\n{preview}\n</persisted-output>"


def probe(tmp, lines, *args):
    t = tmp / "t.jsonl"
    t.write_text("\n".join(lines) + "\n")
    r = subprocess.run([sys.executable, str(SCRIPT), str(t), *args], capture_output=True, text=True)
    assert "Traceback" not in r.stderr, r.stderr
    return r.returncode, r.stdout.strip()


def test_inline_present(tmp_path):
    inj = block([("a.md", "alpha text")])
    code, out = probe(tmp_path, [line(["other", inj])], "--rule", "a.md")
    assert code == 0 and out.startswith("PRESENT (inline)") and "rule: a.md" in out


def test_inline_absent(tmp_path):
    code, out = probe(tmp_path, [line([block([("a.md", "x")])])], "--rule", "b.md")
    assert code == 1 and out.startswith("ABSENT")


def test_persisted_fleet_second_in_content(tmp_path):
    saved = tmp_path / "saved.txt"
    saved.write_text(block([("a.md", "alpha")]))
    items = ["<EXTREMELY_IMPORTANT>mentions <team-lead-fleet-rules> in passing</EXTREMELY_IMPORTANT>",
             persisted(saved, HEADER)]
    code, out = probe(tmp_path, [line(items)], "--rule", "a.md")
    assert code == 0 and f"saved file {saved}" in out


def test_latest_injection_wins(tmp_path):
    old = line([block([("a.md", "x")])])
    new = line([block([("b.md", "y")])])
    assert probe(tmp_path, [old, new], "--rule", "a.md")[0] == 1
    assert probe(tmp_path, [old, new], "--rule", "b.md")[0] == 0


def test_private_marker(tmp_path):
    code, out = probe(tmp_path, [line([block([("p.md", "secret")], kind="private rule")])], "--rule", "p.md")
    assert code == 0 and "private rule: p.md" in out


def test_skipped_header(tmp_path):
    inj = block([("a.md", "x")], skipped=["claude-hive-peer.md", "c.md"])
    code, out = probe(tmp_path, [line([inj])], "--rule", "c.md")
    assert code == 1 and out.startswith("SKIPPED")


def test_bare_name_in_body_is_not_a_marker(tmp_path):
    inj = block([("a.md", "see c.md for details")])
    assert probe(tmp_path, [line([inj])], "--rule", "c.md")[1].startswith("ABSENT")


def test_unreadable(tmp_path):
    inj = block([("a.md", "")], unreadable=["a.md"])
    code, out = probe(tmp_path, [line([inj])], "--rule", "a.md")
    assert code == 1 and out.startswith("UNREADABLE")


def test_text_checks_edited_rule(tmp_path):
    inj = block([("a.md", "old wording"), ("b.md", "NEW wording")])
    t = [line([inj])]
    assert probe(tmp_path, t, "--rule", "a.md", "--text", "NEW wording")[0] == 1  # scoped to a.md
    assert probe(tmp_path, t, "--rule", "b.md", "--text", "NEW wording")[0] == 0
    assert probe(tmp_path, t, "--text", "NEW wording")[0] == 0
    assert probe(tmp_path, t, "--text", "nowhere")[0] == 1


def test_no_fleet_output(tmp_path):
    lines = [line(["unrelated"]), line([block([("a.md", "x")])], event="Other"),
             line([block([("a.md", "x")])], typ="other")]
    code, out = probe(tmp_path, lines, "--rule", "a.md")
    assert code == 2 and out.startswith("COULD NOT RUN")


def test_missing_saved_file(tmp_path):
    item = persisted(tmp_path / "gone.txt", HEADER)
    code, out = probe(tmp_path, [line([item])], "--rule", "a.md")
    assert code == 2 and out.startswith("COULD NOT RUN")


def test_missing_transcript(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "nope.jsonl"), "--rule", "a.md"],
                       capture_output=True, text=True)
    assert r.returncode == 2 and r.stdout.startswith("COULD NOT RUN")
