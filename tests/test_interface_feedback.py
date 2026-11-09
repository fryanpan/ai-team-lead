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
    assert "notion" in ctx and "interface-feedback" in ctx


def test_under_an_hour_does_not_fire(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON)
    assert prompt(tmp_path, NOON + HOUR - 60) is None


def test_second_ask_same_day_does_not_fire(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON)
    assert prompt(tmp_path, NOON + 2 * HOUR) is not None
    assert prompt(tmp_path, NOON + 2 * HOUR + 600) is None  # notion already asked; hive used 10m ago


def test_service_asked_yesterday_and_not_used_since_does_not_fire(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON - 86400)
    assert prompt(tmp_path, NOON - 86400 + 2 * HOUR) is not None
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


# --- fix round: behaviours the first review found missing ---------------------

MIN = 60


def test_steady_use_is_asked_after_an_hour_of_use(tmp_path):
    # A service called every 10 minutes never leaves an hour's gap; it must still
    # be asked about once it has been in use for an hour.
    for i in range(7):
        out = run(tmp_path, "record", {"session_id": "s1", "tool_name": "mcp__notion__search"},
                  NOON + i * 10 * MIN)
    assert out is not None and "notion" in out["hookSpecificOutput"]["additionalContext"]


def test_late_night_use_is_asked_about_next_morning(tmp_path):
    late = datetime.datetime(2026, 10, 30, 23, 30).timestamp()
    morning = datetime.datetime(2026, 10, 31, 8, 0).timestamp()
    use(tmp_path, "mcp__notion__search", late)
    out = prompt(tmp_path, morning)
    assert out is not None and "notion" in out["hookSpecificOutput"]["additionalContext"]


def test_no_repeat_ask_after_clear_or_respawn(tmp_path):
    # A /clear or respawn gives a new session id; the same agent must not be
    # asked again about the same service on the same day.
    use(tmp_path, "mcp__notion__search", NOON, sid="s1")
    assert prompt(tmp_path, NOON + 2 * HOUR, sid="s1") is not None
    use(tmp_path, "mcp__notion__search", NOON + 2 * HOUR + MIN, sid="s2")
    assert prompt(tmp_path, NOON + 4 * HOUR, sid="s2") is None


def test_ask_needs_no_shell_command(tmp_path):
    # No Bash call: avoids shell expansion of free text and permission dialogs
    # on peers with narrow allow lists.
    use(tmp_path, "mcp__notion__search", NOON)
    ctx = prompt(tmp_path, NOON + 2 * HOUR)["hookSpecificOutput"]["additionalContext"]
    assert "python3" not in ctx and "--well" not in ctx
    assert "interface-feedback" in ctx


def _transcript(tmp, text):
    p = tmp / "t.jsonl"
    p.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": text}]}}) + "\n")
    return str(p)


BLOCK = ("Done with the task.\n\n```interface-feedback\n"
         "notion | search was fast | fetch by URL failed | accept URLs | `$(rm -rf x)`\n"
         "claude-hive | fine | none | none\n```\n")


def test_collect_records_block_from_transcript_once(tmp_path):
    tp = _transcript(tmp_path, BLOCK)
    for _ in range(2):  # the Stop hook can fire again on the same transcript
        run(tmp_path, "collect", {"session_id": "s1", "transcript_path": tp}, NOON)
    rows = [json.loads(l) for l in (tmp_path / "notion.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["agent"] == "Tester" and rows[0]["issue"] == "fetch by URL failed"
    assert "$(rm -rf x)" in rows[0]["improve"]  # stored literally, never run
    assert (tmp_path / "claude-hive.jsonl").exists()


def _status(tmp, now):
    env = dict(os.environ, INTERFACE_FEEDBACK_DIR=str(tmp), INTERFACE_FEEDBACK_NOW=str(now),
               CW_AGENT_NAME="Tester")
    r = subprocess.run([sys.executable, SCRIPT, "status"], capture_output=True, text=True, env=env)
    return r.stdout


def test_status_reports_three_states(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON)
    assert "nothing due" in _status(tmp_path, NOON + MIN)
    prompt(tmp_path, NOON + 2 * HOUR)
    assert "asked" in _status(tmp_path, NOON + 2 * HOUR)
    # Break the state store: the hook must stay silent to the session, but the
    # failure must be visible to anyone who checks.
    import shutil
    shutil.rmtree(tmp_path / "state")
    (tmp_path / "state").write_text("not a dir")
    assert run(tmp_path, "record", {"session_id": "s1", "tool_name": "mcp__notion__x"},
               NOON + 3 * HOUR) is None
    assert "could not run" in _status(tmp_path, NOON + 3 * HOUR)
