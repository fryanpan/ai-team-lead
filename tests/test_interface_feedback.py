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
               INTERFACE_FEEDBACK_NOW=str(now), INTERFACE_FEEDBACK_RETRY="0")
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
    assert prompt(tmp_path, NOON + 2 * HOUR + 600) is None  # notion already asked today


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


def _call(expr, env=None):
    code = ("import importlib.util,sys;s=importlib.util.spec_from_file_location('f',%r);"
            "f=importlib.util.module_from_spec(s);s.loader.exec_module(f);print(repr(%s))"
            % (SCRIPT, expr))
    out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True,
                         env=dict(os.environ, **(env or {})))
    assert out.returncode == 0, out.stderr
    return eval(out.stdout)


def test_service_name_normalisation():
    assert _call('f.service_from_tool("mcp__plugin_claude-workspaces_claude-workspaces__x")') == "claude-workspaces"
    assert _call('f.service_from_tool("mcp__plugin_a_b__x")') == "a/b"
    assert _call('f.service_from_tool("mcp__claude-hive__send_message")') == "claude-hive"
    assert _call('f.service_from_tool("Skill", {"skill": "plain"})') is None
    assert _call('f.service_from_tool("Bash")') is None


def test_subagent_use_counts_for_parent_and_non_fleet_sessions_skipped(tmp_path):
    out = run(tmp_path, "record", {"session_id": "s1", "tool_name": "mcp__notion__search",
                                   "agent_id": "sub1"}, NOON)
    assert out is None
    assert run(tmp_path, "record", {"session_id": "s1", "tool_name": "mcp__notion__search",
                                    "agent_id": "sub1"}, NOON + 2 * HOUR) is None  # never asks a subagent
    out = prompt(tmp_path, NOON + 2 * HOUR)
    assert out and "notion" in out["hookSpecificOutput"]["additionalContext"]
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
    assert prompt(tmp_path, NOON + 2 * HOUR + 600) is None  # notion already asked today


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


def _transcript(tmp, text, at=NOON + 30):
    p = tmp / "t.jsonl"
    p.write_text(json.dumps({"type": "assistant", "timestamp": datetime.datetime.fromtimestamp(
        at).astimezone().isoformat(), "message": {"content": [
        {"type": "text", "text": text}]}}) + "\n")
    return str(p)


BLOCK = ("Done with the task.\n\n```interface-feedback\n"
         "notion | search was fast | fetch by URL failed | accept URLs | `$(rm -rf x)`\n"
         "claude-hive | fine | none | none\n```\n")


def _open_ask(tmp, now=NOON):
    use(tmp, "mcp__notion__search", now - 2 * HOUR)
    use(tmp, "mcp__claude-hive__send_message", now - 2 * HOUR)
    assert prompt(tmp, now) is not None


def test_collect_records_block_from_transcript_once(tmp_path):
    _open_ask(tmp_path)
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


def test_collect_ignores_blocks_from_earlier_days(tmp_path):
    _open_ask(tmp_path)
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "assistant", "timestamp": "2026-10-29T18:00:00Z",
                             "message": {"content": [{"type": "text", "text": BLOCK}]}}) + "\n")
    run(tmp_path, "collect", {"session_id": "s1", "transcript_path": str(p)}, NOON)
    assert not (tmp_path / "notion.jsonl").exists()


def _status_rc(tmp, now, agent="Tester"):
    env = dict(os.environ, INTERFACE_FEEDBACK_DIR=str(tmp), INTERFACE_FEEDBACK_NOW=str(now))
    env.pop("CW_AGENT_NAME", None)
    env.pop("FEEDBACK_AGENT_NAME", None)
    if agent:
        env["CW_AGENT_NAME"] = agent
    r = subprocess.run([sys.executable, SCRIPT, "status"], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout


def test_use_after_todays_ask_is_asked_about_next_day(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON - 3 * HOUR)
    assert prompt(tmp_path, NOON) is not None          # asked today
    use(tmp_path, "mcp__notion__search", NOON + 2 * HOUR)  # more use the same day
    assert prompt(tmp_path, NOON + 3 * HOUR) is None   # not twice in one day
    out = prompt(tmp_path, NOON + 86400)               # not used next day, still asked
    assert out and "notion" in out["hookSpecificOutput"]["additionalContext"]


def test_status_recovers_after_a_transient_error(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON)
    (tmp_path / "errors").mkdir()
    with open(tmp_path / "errors" / "Tester.log", "a") as f:
        f.write("%r 2026-10-30T12:01 record: OSError: disk hiccup\n" % (NOON + 60))
    assert _status_rc(tmp_path, NOON + 120)[0] == 1
    use(tmp_path, "mcp__notion__search", NOON + 2 * 60)   # a later run succeeds
    rc, out = _status_rc(tmp_path, NOON + 3 * 60)
    assert rc == 0 and "could not run" not in out


def test_status_never_run_is_not_nothing_due(tmp_path):
    rc, out = _status_rc(tmp_path, NOON)
    assert rc != 0 and "nothing due" not in out
    rc, out = _status_rc(tmp_path, NOON, agent="")
    assert rc != 0 and "nothing due" not in out


def test_status_tells_answered_from_unanswered(tmp_path):
    _open_ask(tmp_path)
    out = _status_rc(tmp_path, NOON + 60)[1]
    assert "unanswered" in out and "notion" in out
    run(tmp_path, "collect", {"session_id": "s1", "transcript_path": _transcript(tmp_path, BLOCK)},
        NOON + 120)
    out = _status_rc(tmp_path, NOON + 180)[1]
    assert "unanswered" not in out and "notion" in out.split("answered today:")[1]


def test_status_ignores_errors_of_agents_with_overlapping_names(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON, )
    (tmp_path / "errors").mkdir()
    for other in ("Team_Tester", "Tester2"):
        with open(tmp_path / "errors" / (other + ".log"), "a") as f:
            f.write("%r 2026-10-30T12:01 record: OSError: other agent\n" % (NOON + 60))
    rc, out = _status_rc(tmp_path, NOON + 120)
    assert rc == 0 and "could not run" not in out


def test_collect_skips_transcript_when_no_ask_is_open(tmp_path):
    tp = _transcript(tmp_path, BLOCK)
    run(tmp_path, "collect", {"session_id": "s1", "transcript_path": tp}, NOON)
    assert not (tmp_path / "notion.jsonl").exists()


def _service(tool, cfg):
    code = ("import importlib.util,sys;s=importlib.util.spec_from_file_location('f',%r);"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "print(m.service_from_tool(%r))" % (SCRIPT, tool))
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(cfg))
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=env).stdout.strip()


def test_plugin_name_with_underscore_uses_installed_name(tmp_path):
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {"my_tools@mkt": []}}))
    assert _service("mcp__plugin_my_tools_srv__x", tmp_path) == "my_tools/srv"
    assert _service("mcp__plugin_my_tools_my_tools__x", tmp_path) == "my_tools"
    empty = tmp_path / "none"
    empty.mkdir()
    assert _service("mcp__plugin_a_b__x", empty) == "a/b"


def _ts(epoch):
    return datetime.datetime.fromtimestamp(epoch).astimezone().isoformat()


def _transcript_at(tmp, *entries):
    p = tmp / "tt.jsonl"
    p.write_text("".join(json.dumps({"type": "assistant", "timestamp": _ts(t), "message": {
        "content": [{"type": "text", "text": x}]}}) + "\n" for t, x in entries))
    return {"session_id": "s1", "transcript_path": str(p)}


def test_failed_write_keeps_the_ask_open_and_status_shows_it(tmp_path):
    _open_ask(tmp_path)
    (tmp_path / "notion.jsonl").mkdir()          # the write will fail
    tp = _transcript_at(tmp_path, (NOON + 60, BLOCK))
    run(tmp_path, "collect", tp, NOON + 120)
    rc, out = _status_rc(tmp_path, NOON + 120)
    assert rc == 1 and "could not run" in out
    (tmp_path / "notion.jsonl").rmdir()          # writable again
    run(tmp_path, "collect", tp, NOON + 180)
    assert len((tmp_path / "notion.jsonl").read_text().splitlines()) == 1


def test_answer_after_midnight_is_stored(tmp_path):
    late = datetime.datetime(2026, 10, 30, 23, 50).timestamp()
    _open_ask(tmp_path, late)
    run(tmp_path, "collect", _transcript_at(tmp_path, (late + 15 * MIN, BLOCK)), late + 16 * MIN)
    assert (tmp_path / "notion.jsonl").exists()


def test_no_second_ask_an_hour_after_a_late_ask(tmp_path):
    use(tmp_path, "mcp__notion__search", datetime.datetime(2026, 10, 30, 22, 0).timestamp())
    t = datetime.datetime(2026, 10, 30, 23, 30).timestamp()
    assert run(tmp_path, "record", {"session_id": "s1", "tool_name": "mcp__notion__x"}, t)
    assert prompt(tmp_path, t + HOUR + MIN) is None


def test_earlier_unanswered_ask_survives_a_later_ask(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON)
    assert prompt(tmp_path, NOON + HOUR + MIN) is not None       # notion asked
    use(tmp_path, "mcp__claude-hive__send_message", NOON + 30 * MIN)
    assert prompt(tmp_path, NOON + 2 * HOUR) is not None          # hive asked
    hive_only = "Ok.\n```interface-feedback\nclaude-hive | fine | none | none\n```\n"
    run(tmp_path, "collect", _transcript_at(tmp_path, (NOON + 2 * HOUR + MIN, hive_only)),
        NOON + 2 * HOUR + 2 * MIN)
    assert "unanswered: notion" in _status_rc(tmp_path, NOON + 2 * HOUR + 3 * MIN)[1]
    run(tmp_path, "collect", _transcript_at(tmp_path, (NOON + 2 * HOUR + 4 * MIN, BLOCK)),
        NOON + 2 * HOUR + 5 * MIN)
    assert (tmp_path / "notion.jsonl").exists()


def test_duplicate_and_unasked_blocks_store_one_row_per_asked_service(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON - 2 * HOUR)
    assert prompt(tmp_path, NOON) is not None
    extra = "```interface-feedback\nnobody-asked | a | b | c\n```\n"
    run(tmp_path, "collect", _transcript_at(tmp_path, (NOON + MIN, BLOCK), (NOON + 2 * MIN, BLOCK + extra)),
        NOON + 3 * MIN)
    assert len((tmp_path / "notion.jsonl").read_text().splitlines()) == 1
    assert not (tmp_path / "nobody-asked.jsonl").exists()


def test_file_names_do_not_collide():
    assert _call('f.safe("p/s")') != _call('f.safe("p_s")')
    assert _call('f.safe("Team Lead")') != _call('f.safe("Team_Lead")')


def test_unparseable_error_line_does_not_stick_as_could_not_run(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON)
    (tmp_path / "errors").mkdir()
    (tmp_path / "errors" / "Tester.log").write_text("continuation of a multi-line message\n")
    use(tmp_path, "mcp__notion__search", NOON + 20 * MIN)
    rc, out = _status_rc(tmp_path, NOON + 21 * MIN)
    assert rc == 0 and "could not run" not in out


def test_logged_error_with_newline_stays_one_line(tmp_path):
    _call('f.log_error("record", "first\\nsecond")', {"INTERFACE_FEEDBACK_DIR": str(tmp_path),
                                                       "CW_AGENT_NAME": "Tester"})
    assert len((tmp_path / "errors" / "Tester.log").read_text().splitlines()) == 1


def test_block_without_timestamp_is_not_stored(tmp_path):
    _open_ask(tmp_path)
    p = tmp_path / "nt.jsonl"
    p.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": BLOCK}]}}) + "\n")
    run(tmp_path, "collect", {"session_id": "s1", "transcript_path": str(p)}, NOON + MIN)
    assert not (tmp_path / "notion.jsonl").exists()


def test_prompt_with_nothing_due_does_not_rewrite_state(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON)
    assert prompt(tmp_path, NOON + MIN) is None     # first prompt stamps ask health
    st = tmp_path / "state" / "Tester.json"
    before = st.stat().st_mtime_ns
    assert prompt(tmp_path, NOON + 2 * MIN) is None
    assert st.stat().st_mtime_ns == before


def test_ask_text_says_to_write_the_block_even_when_ending_without_text(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON)
    ctx = prompt(tmp_path, NOON + 2 * HOUR)["hookSpecificOutput"]["additionalContext"]
    assert "even if" in ctx


def test_hooks_json_skips_unnamed_sessions_before_python(tmp_path):
    hooks = json.load(open(os.path.join(os.path.dirname(SCRIPT), "hooks.json")))["hooks"]
    cmd = hooks["PostToolUse"][-1]["hooks"][0]["command"]
    root = os.path.dirname(os.path.dirname(SCRIPT))
    base = dict(os.environ, CLAUDE_PLUGIN_ROOT=root, INTERFACE_FEEDBACK_DIR=str(tmp_path))
    base.pop("CW_AGENT_NAME", None)
    base.pop("FEEDBACK_AGENT_NAME", None)
    payload = json.dumps({"tool_name": "mcp__notion__x"})
    r = subprocess.run(["sh", "-c", cmd], input=payload, text=True, env=base)
    assert r.returncode == 0 and not (tmp_path / "state").exists()
    r = subprocess.run(["sh", "-c", cmd], input=payload, text=True, env=dict(base, CW_AGENT_NAME="X"))
    assert r.returncode == 0 and (tmp_path / "state" / "X.json").exists()


def test_record_success_does_not_hide_a_failing_collect(tmp_path):
    _open_ask(tmp_path)
    (tmp_path / "errors").mkdir()
    with open(tmp_path / "errors" / "Tester.log", "a") as f:
        f.write("%r 2026-10-30T12:01 collect: OSError: cannot read transcript\n" % (NOON + 60))
    use(tmp_path, "mcp__notion__search", NOON + 2 * MIN)
    rc, out = _status_rc(tmp_path, NOON + 3 * MIN)
    assert rc == 1 and "could not run" in out


def test_common_block_variants_are_parsed(tmp_path):
    _open_ask(tmp_path)
    text = ("```interface-feedback\n- `Notion`|fast|none|accept URLs\n"
            "* claude-hive | fine | none | none\n```\n")
    run(tmp_path, "collect", {"session_id": "s1", "transcript_path": _transcript(tmp_path, text)},
        NOON + MIN)
    row = json.loads((tmp_path / "notion.jsonl").read_text())
    assert row["well"] == "fast" and row["improve"] == "accept URLs"
    assert (tmp_path / "claude-hive.jsonl").exists()


def test_directory_scoped_skill_is_not_a_plugin():
    assert _call('f.service_from_tool("Skill", {"skill": "apps/web:deploy"})') is None
    assert _call('f.service_from_tool("Skill", {"skill": "superpowers:brainstorming"})') == "superpowers"



def test_service_back_after_a_week_is_asked_once_that_day(tmp_path):
    use(tmp_path, "mcp__notion__search", NOON)
    later = NOON + 8 * 86400
    assert run(tmp_path, "record", {"session_id": "s1", "tool_name": "mcp__notion__x"}, later)
    use(tmp_path, "mcp__notion__search", later + MIN)
    assert prompt(tmp_path, later + 2 * HOUR) is None


def test_status_checks_every_mode_not_just_the_newest_error(tmp_path):
    _open_ask(tmp_path)
    (tmp_path / "errors").mkdir()
    with open(tmp_path / "errors" / "Tester.log", "a") as f:
        f.write("%r x collect: OSError: broken\n" % (NOON + 60))
        f.write("%r x record: OSError: blip\n" % (NOON + 90))
    use(tmp_path, "mcp__notion__search", NOON + 2 * MIN)   # record recovers
    rc, out = _status_rc(tmp_path, NOON + 3 * MIN)
    assert rc == 1 and "collect" in out


def test_failed_tool_calls_count_as_use(tmp_path):
    hooks = json.load(open(os.path.join(os.path.dirname(SCRIPT), "hooks.json")))["hooks"]
    cmd = hooks["PostToolUseFailure"][-1]["hooks"][0]["command"]
    assert "record PostToolUseFailure" in cmd
    run(tmp_path, "record", {"session_id": "s1", "tool_name": "mcp__notion__x"}, NOON, "Tester",
        "PostToolUseFailure")
    out = run(tmp_path, "record", {"session_id": "s1", "tool_name": "mcp__notion__x"},
              NOON + 2 * HOUR, "Tester", "PostToolUseFailure")
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUseFailure"


def test_hooks_pin_python_path():
    hooks = json.load(open(os.path.join(os.path.dirname(SCRIPT), "hooks.json")))["hooks"]
    for ev in ("PostToolUse", "PostToolUseFailure", "UserPromptSubmit", "Stop"):
        for h in hooks[ev]:
            for c in h["hooks"]:
                if "interface_feedback.py" in c["command"]:
                    assert "/usr/bin/python3 " in c["command"]
