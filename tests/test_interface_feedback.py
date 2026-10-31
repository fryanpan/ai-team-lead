"""Daily interface-feedback ping: record service use, ask once per day after a gap.

Runs the hook script as a subprocess the way Claude Code does (JSON on stdin,
JSON on stdout) with a fake clock and a temp state dir.
"""
import datetime
import json
import os
import subprocess
import sys

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "plugin", "team-lead-fleet",
                      "hooks", "interface_feedback.py")
NOON = datetime.datetime(2026, 10, 30, 12, 0).timestamp()   # local noon
HOUR = 3600


def run(tmp, mode, payload, now, agent="Tester", *extra):
    env = dict(os.environ, INTERFACE_FEEDBACK_DIR=str(tmp),
               INTERFACE_FEEDBACK_NOW=str(now))
    env.pop("CW_AGENT_NAME", None)
    env.pop("FEEDBACK_AGENT_NAME", None)
    if agent:
        env["CW_AGENT_NAME"] = agent
    r = subprocess.run([sys.executable, SCRIPT, mode, *extra], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def use(tmp, tool, now, sid="s1", **kw):
    run(tmp, "record", {"session_id": sid, "tool_name": tool, "tool_input": kw.get("tool_input", {}),
                        **({"agent_id": kw["agent_id"]} if "agent_id" in kw else {})}, now)


def prompt(tmp, now, sid="s1", **kw):
    return run(tmp, "ask", {"session_id": sid}, now, **kw)


def test_used_today_with_hour_gap_fires_once(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON)
    out = prompt(tmp_path, NOON + HOUR + 60)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "notion" in ctx and "interface_feedback.py" in ctx and "--agent \"Tester\"" in ctx


def test_under_an_hour_does_not_fire(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON)
    assert prompt(tmp_path, NOON + HOUR - 60) is None


def test_second_ask_same_day_does_not_fire(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON)
    assert prompt(tmp_path, NOON + 2 * HOUR) is not None
    assert prompt(tmp_path, NOON + 2 * HOUR + 600) is None  # notion already asked; hive used 10m ago


def test_service_not_used_today_does_not_fire(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON - 86400)
    assert prompt(tmp_path, NOON + 2 * HOUR) is None


def test_multiple_due_services_batch_into_one_ask(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON)
    use(tmp_path, "mcp__plugin_claude-workspaces_claude-workspaces__get_doc", NOON + 60)
    use(tmp_path, "Skill", NOON + 120, tool_input={"skill": "team-lead-fleet:retro"})
    out = prompt(tmp_path, NOON + 3 * HOUR)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "claude-workspaces" in ctx and "notion" in ctx and "team-lead-fleet" in ctx
    assert prompt(tmp_path, NOON + 4 * HOUR) is None


def test_service_name_normalisation():
    sys.path.insert(0, os.path.dirname(SCRIPT))
    import interface_feedback as f
    assert f.service_from_tool("mcp__plugin_claude-workspaces_claude-workspaces__x") == "claude-workspaces"
    assert f.service_from_tool("mcp__plugin_a_b__x") == "a/b"
    assert f.service_from_tool("mcp__claude-hive__send_message") == "claude-hive"
    assert f.service_from_tool("Skill", {"skill": "plain"}) is None
    assert f.service_from_tool("Bash") is None


def test_subagents_and_non_fleet_sessions_skipped(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON, agent_id="sub1")
    assert prompt(tmp_path, NOON + 2 * HOUR) is None
    run(tmp_path, "record", {"session_id": "s2", "tool_name": "mcp__notion__search"}, NOON, agent="")
    assert prompt(tmp_path, NOON + 2 * HOUR, sid="s2", agent="") is None


def test_add_and_read_roundtrip_and_write_failure(tmp_path):
    env = dict(os.environ, INTERFACE_FEEDBACK_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, SCRIPT, "add", "--service", "notion", "--well", "w",
                        "--issue", "i", "--improve", "m", "--agent", "A"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0
    r = subprocess.run([sys.executable, SCRIPT, "read", "--service", "notion", "--since", "2000-01-01"],
                       capture_output=True, text=True, env=env)
    assert "1 entries" in r.stdout and "improve: m" in r.stdout
    blocker = tmp_path / "file"
    blocker.write_text("x")
    env["INTERFACE_FEEDBACK_DIR"] = str(blocker / "sub")
    r = subprocess.run([sys.executable, SCRIPT, "add", "--service", "n", "--well", "w",
                        "--issue", "i", "--improve", "m"], capture_output=True, text=True, env=env)
    assert r.returncode == 1 and "COULD NOT WRITE" in r.stderr


def test_record_hook_asks_for_session_woken_without_typed_prompt(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON)
    out = run(tmp_path, "record", {"session_id": "s1", "tool_name": "mcp__claude-hive__send_message"},
              NOON + 2 * HOUR)
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "notion" in out["hookSpecificOutput"]["additionalContext"]
    assert prompt(tmp_path, NOON + 2 * HOUR + 600) is None  # notion already asked; hive used 10m ago
