"""Tests for scripts/probe_injection.py.

Run: uv run --with pytest python -m pytest scripts/test_probe_injection.py

Injection fixtures come from running the real plugin hook
(plugin/team-lead-fleet/hooks/session-start.sh) against a small rules
directory, so marker formats, the dedup header, the scoped-omission line and
the UNREADABLE text are the hook's own. Hand-built fixtures are used only for
transcript-shape cases. HOME is always a temp dir.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOK_DIR = REPO / "plugin/team-lead-fleet/hooks"
SCRIPT = Path(__file__).resolve().parent / "probe_injection.py"
FM = "---\nalwaysApply: true\n{extra}---\n\n"


def make_plugin(tmp, rules):
    """A copy of the real hook next to a controlled rules dir. rules: name -> full file text."""
    root = tmp / "plugin"
    shutil.copytree(HOOK_DIR, root / "hooks")
    (root / "rules").mkdir()
    for name, text in rules.items():
        (root / "rules" / name).write_text(text)
    return root


def run_hook(tmp, root, payload, overlay=None):
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    if overlay is not None:
        env["TEAM_LEAD_FLEET_PRIVATE_RULES"] = str(overlay)
    out = subprocess.run(["/bin/bash", str(root / "hooks/session-start.sh")],
                         input=json.dumps(payload), capture_output=True, text=True, env=env, check=True)
    return json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]


def line(content, event="SessionStart", typ="hook_additional_context", cwd=None):
    rec = {"attachment": {"type": typ, "hookEvent": event, "content": content}}
    if cwd:
        rec["cwd"] = cwd
    return json.dumps(rec)


def persisted(path, preview):
    return (f"<persisted-output>\nOutput too large (101KB). Full output saved to: {path}\n\n"
            f"Preview (first 2KB):\n{preview}\n</persisted-output>")


def probe(tmp, lines, *args, raw=None):
    t = tmp / "t.jsonl"
    t.write_bytes(raw if raw is not None else ("\n".join(lines) + "\n").encode())
    r = subprocess.run([sys.executable, str(SCRIPT), str(t), *args], capture_output=True, text=True)
    assert "Traceback" not in r.stderr, r.stderr
    return r.returncode, r.stdout.strip()


def fleet(tmp, rules, payload=None, overlay=None):
    root = make_plugin(tmp, rules)
    return run_hook(tmp, root, payload or {"source": "startup", "cwd": str(tmp / "proj")}, overlay)


def rule(body, extra=""):
    return FM.format(extra=extra) + body + "\n"


# --- hook-generated fixtures -------------------------------------------------

def test_inline_present_and_absent(tmp_path):
    ctx = fleet(tmp_path, {"a.md": rule("alpha text")})
    ls = [line(["other", ctx])]
    code, out = probe(tmp_path, ls, "--rule", "a.md")
    assert code == 0 and out.startswith("PRESENT (inline)") and "rule: a.md" in out
    assert probe(tmp_path, ls, "--rule", "b.md")[0] == 1


def test_persisted_with_fleet_item_second(tmp_path):
    ctx = fleet(tmp_path, {"a.md": rule("alpha")})
    saved = tmp_path / "saved.txt"
    saved.write_text(ctx)
    items = ["<EXTREMELY_IMPORTANT>cites <team-lead-fleet-rules> in passing</EXTREMELY_IMPORTANT>",
             persisted(saved, ctx[:200])]
    code, out = probe(tmp_path, [line(items)], "--rule", "a.md")
    assert code == 0 and f"saved file {saved}" in out


def test_persisted_item_that_only_mentions_the_tag_is_not_fleet_output(tmp_path):
    item = persisted(tmp_path / "x.txt", "some tool output citing <team-lead-fleet-rules> later")
    code, out = probe(tmp_path, [line([item])], "--rule", "a.md")
    assert code == 2 and out.startswith("COULD NOT RUN")


def test_private_marker(tmp_path):
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    (overlay / "p.md").write_text(rule("secret"))
    ctx = fleet(tmp_path, {"a.md": rule("alpha")}, overlay=overlay)
    code, out = probe(tmp_path, [line([ctx])], "--rule", "p.md")
    assert code == 0 and "private rule: p.md" in out


def test_unreadable_uses_the_hooks_own_text(tmp_path):
    ctx = fleet(tmp_path, {"a.md": rule("alpha"), "empty.md": ""})
    code, out = probe(tmp_path, [line([ctx])], "--rule", "empty.md")
    assert code == 1 and out.startswith("UNREADABLE")
    assert probe(tmp_path, [line([ctx])], "--rule", "a.md")[0] == 0


def dup_setup(tmp_path, local_text=None):
    text = rule("dup body v2")
    proj = tmp_path / "proj"
    (proj / ".claude/rules").mkdir(parents=True)
    (proj / ".claude/rules/dup.md").write_text(text if local_text is None else local_text)
    # An identical project copy makes the hook skip dup.md; keep a second rule so the block is not empty.
    ctx = fleet(tmp_path, {"dup.md": text, "a.md": rule("alpha")})
    return ctx, str(proj)


def test_dedup_skip_reports_skipped(tmp_path):
    ctx, cwd = dup_setup(tmp_path)
    assert "Already in your context verbatim" in ctx
    code, out = probe(tmp_path, [line([ctx], cwd=cwd)], "--rule", "dup.md")
    assert code == 1 and out.startswith("SKIPPED")


def test_dedup_skip_with_text_checks_the_local_copy(tmp_path):
    ctx, cwd = dup_setup(tmp_path)
    ls = [line([ctx], cwd=cwd)]
    code, out = probe(tmp_path, ls, "--rule", "dup.md", "--text", "dup body v2")
    assert code == 0 and out.startswith("PRESENT") and "local copy" in out
    code, out = probe(tmp_path, ls, "--rule", "dup.md", "--text", "not there")
    assert code == 1 and out.startswith("ABSENT")


def test_dedup_skip_with_text_and_no_cwd_could_not_run(tmp_path):
    ctx, _ = dup_setup(tmp_path)
    code, out = probe(tmp_path, [line([ctx])], "--rule", "dup.md", "--text", "dup body v2")
    assert code == 2 and out.startswith("COULD NOT RUN")


def test_scoped_omission_is_skipped(tmp_path):
    ctx = fleet(tmp_path, {"main.md": rule("main only", "appliesTo: main\n"), "a.md": rule("alpha")},
                payload={"source": "startup", "agent_type": "subagent", "cwd": str(tmp_path / "proj")})
    assert "omitted for this agent" in ctx
    code, out = probe(tmp_path, [line([ctx])], "--rule", "main.md", "--text", "main only")
    assert code == 1 and out.startswith("SKIPPED")


def test_text_checks_edited_rule_and_is_scoped_to_the_rule(tmp_path):
    ctx = fleet(tmp_path, {"a.md": rule("old wording"), "b.md": rule("NEW wording")})
    ls = [line([ctx])]
    assert probe(tmp_path, ls, "--rule", "a.md", "--text", "NEW wording")[0] == 1
    assert probe(tmp_path, ls, "--rule", "b.md", "--text", "NEW wording")[0] == 0
    assert probe(tmp_path, ls, "--text", "NEW wording")[0] == 0
    assert probe(tmp_path, ls, "--text", "nowhere")[0] == 1


def test_text_without_rule_ignores_header_lines(tmp_path):
    ctx, cwd = dup_setup(tmp_path)
    ls = [line([ctx], cwd=cwd)]
    # The header lists dup.md by name but carries none of its body.
    assert probe(tmp_path, ls, "--text", "Already in your context")[0] == 1
    assert probe(tmp_path, ls, "--text", "dup body v2")[0] == 1
    assert probe(tmp_path, ls, "--text", "alpha")[0] == 0


def test_bare_name_in_body_is_not_a_marker(tmp_path):
    ctx = fleet(tmp_path, {"a.md": rule("see c.md for details")})
    assert probe(tmp_path, [line([ctx])], "--rule", "c.md")[1].startswith("ABSENT")


# --- transcript-shape cases (hand-built) -------------------------------------

BLOCK = "<team-lead-fleet-rules>\n\n\n<!-- team-lead-fleet rule: {n} -->\n\nbody\n\n</team-lead-fleet-rules>"


def test_latest_injection_wins(tmp_path):
    old, new = line([BLOCK.format(n="a.md")]), line([BLOCK.format(n="b.md")])
    assert probe(tmp_path, [old, new], "--rule", "a.md")[0] == 1
    assert probe(tmp_path, [old, new], "--rule", "b.md")[0] == 0


def test_no_fleet_output(tmp_path):
    lines = [line(["unrelated"]), line([BLOCK.format(n="a.md")], event="Other"),
             line([BLOCK.format(n="a.md")], typ="other")]
    code, out = probe(tmp_path, lines, "--rule", "a.md")
    assert code == 2 and out.startswith("COULD NOT RUN")


def test_missing_saved_file(tmp_path):
    item = persisted(tmp_path / "gone.txt", BLOCK.format(n="a.md"))
    code, out = probe(tmp_path, [line([item])], "--rule", "a.md")
    assert code == 2 and out.startswith("COULD NOT RUN")


def test_non_utf8_saved_file(tmp_path):
    saved = tmp_path / "bin.txt"
    saved.write_bytes(b"\xff\xfe\x00bad")
    item = persisted(saved, BLOCK.format(n="a.md"))
    code, out = probe(tmp_path, [line([item])], "--rule", "a.md")
    assert code == 2 and out.startswith("COULD NOT RUN")


def test_null_attachment_and_bad_lines_are_skipped(tmp_path):
    raw = (b'{"attachment": null}\nnot json\n\xff\xfe broken bytes hook_additional_context\n[1]\n'
           + line([BLOCK.format(n="a.md")]).encode() + b"\n")
    code, out = probe(tmp_path, [], "--rule", "a.md", raw=raw)
    assert code == 0 and out.startswith("PRESENT")


def test_unexpected_content_shape_is_could_not_run_not_traceback(tmp_path):
    code, out = probe(tmp_path, [line(None), line({"x": 1}), line([None, 5])], "--rule", "a.md")
    assert code == 2 and out.startswith("COULD NOT RUN")


def test_missing_transcript(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "nope.jsonl"), "--rule", "a.md"],
                       capture_output=True, text=True)
    assert r.returncode == 2 and r.stdout.startswith("COULD NOT RUN")
