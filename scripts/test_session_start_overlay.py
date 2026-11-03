"""Tests for the private-overlay half of the fleet plugin's SessionStart hook.

Run: uv run --with pytest python -m pytest scripts/test_session_start_overlay.py

HOME is always a temp dir, so the hook's payload log under
~/Library/Logs is never written by a test.
"""
import json
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "plugin/team-lead-fleet/hooks/session-start.sh"
PUBLIC_MARK = "<!-- team-lead-fleet rule: communication.md -->"


def run_hook(home, overlay_dir=None, payload=None, drop_home=False):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    if overlay_dir is not None:
        env["TEAM_LEAD_FLEET_PRIVATE_RULES"] = str(overlay_dir)
    if drop_home:
        env.pop("HOME")
    out = subprocess.run(
        ["/bin/bash", str(HOOK)],
        input=json.dumps(payload or {"source": "startup", "cwd": "/nonexistent"}),
        capture_output=True, text=True, env=env, check=True,
    )
    return json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]


def write_rule(d, name, body, frontmatter="alwaysApply: true\nnote: FM-SENTINEL\n"):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(f"---\n{frontmatter}---\n\n{body}\n")


def write_conf(home, rules_dir):
    conf = home / ".config/team-lead/private-overlay.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(f"# test\nrules_dir={rules_dir}\nmarketplace=m\n")


def test_overlay_rules_are_injected_and_named(tmp_path):
    overlay = tmp_path / "overlay"
    write_rule(overlay, "alpha.md", "OVERLAY-ALPHA-TEXT")
    write_rule(overlay, "beta.md", "OVERLAY-BETA-TEXT")
    ctx = run_hook(tmp_path, overlay)
    assert "OVERLAY-ALPHA-TEXT" in ctx and "OVERLAY-BETA-TEXT" in ctx
    assert "Private overlay rules loaded: alpha.md beta.md" in ctx
    assert "FM-SENTINEL" not in ctx  # frontmatter stripped
    assert PUBLIC_MARK in ctx


def test_overlay_rules_are_not_deduplicated_against_project_copies(tmp_path):
    # No `gate: never` here: the overlay must skip dedup on its own account.
    overlay = tmp_path / "overlay"
    write_rule(overlay, "alpha.md", "OVERLAY-ALPHA-TEXT")
    project = tmp_path / "proj"
    (project / ".claude/rules").mkdir(parents=True)
    (project / ".claude/rules/alpha.md").write_text((overlay / "alpha.md").read_text())
    ctx = run_hook(tmp_path, overlay, payload={"source": "startup", "cwd": str(project)})
    assert "OVERLAY-ALPHA-TEXT" in ctx


def test_rule_in_both_sets_is_injected_once_from_overlay(tmp_path):
    overlay = tmp_path / "overlay"
    write_rule(overlay, "communication.md", "OVERLAY-COMMUNICATION-TEXT")
    ctx = run_hook(tmp_path, overlay)
    assert "OVERLAY-COMMUNICATION-TEXT" in ctx
    assert PUBLIC_MARK not in ctx
    assert ctx.count("communication.md -->") == 1
    assert "In both sets, injected once from the overlay: communication.md" in ctx


def test_conf_file_names_the_overlay_directory(tmp_path):
    overlay = tmp_path / "overlay"
    write_rule(overlay, "alpha.md", "OVERLAY-ALPHA-TEXT")
    write_conf(tmp_path, overlay)
    ctx = run_hook(tmp_path)  # no env override
    assert "OVERLAY-ALPHA-TEXT" in ctx
    assert "Private overlay rules loaded: alpha.md" in ctx


def test_missing_overlay_is_loud_when_conf_expects_it(tmp_path):
    write_conf(tmp_path, tmp_path / "does-not-exist")
    ctx = run_hook(tmp_path)
    assert "**Private overlay rules: NOT LOADED.**" in ctx
    assert PUBLIC_MARK in ctx


def test_missing_overlay_is_silent_on_a_plain_install(tmp_path):
    ctx = run_hook(tmp_path, tmp_path / "does-not-exist")
    assert "Private overlay" not in ctx
    assert PUBLIC_MARK in ctx


def test_empty_overlay_is_reported_loudly(tmp_path):
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    ctx = run_hook(tmp_path, overlay)
    assert "**Private overlay rules: NONE FOUND.**" in ctx


def test_hook_survives_without_home(tmp_path):
    # No HOME and no override: must not trip `set -u`, must still inject.
    ctx = run_hook(tmp_path, drop_home=True)
    assert PUBLIC_MARK in ctx
