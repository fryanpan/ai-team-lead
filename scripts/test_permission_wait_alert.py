"""Tests for plugin/team-lead-fleet/hooks/permission-wait-alert.py.

Run: uv run --with pytest python -m pytest scripts/test_permission_wait_alert.py
"""
import importlib.util
import json
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "plugin/team-lead-fleet/hooks/permission-wait-alert.py"


@pytest.fixture
def hook(tmp_path, monkeypatch):
    lead = tmp_path / "ai-team-lead"
    (lead / ".claude/worktrees/x").mkdir(parents=True)
    (tmp_path / "ai-team-lead-private").mkdir()
    (tmp_path / "other").mkdir()
    monkeypatch.setenv("TEAM_LEAD_DIR", str(lead))
    monkeypatch.delenv("CW_AGENT_NAME", raising=False)
    spec = importlib.util.spec_from_file_location("pwa", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.LOG = str(tmp_path / "logs/permission-waits.log")
    mod.banners = []
    mod.banner = lambda title, text: mod.banners.append(title)
    mod.sent = []
    mod.tmp = tmp_path
    mod.lead = lead
    return mod


def fake_post(mod, peers, send_reply=None, raise_on=None):
    def post(path, payload):
        if raise_on == path:
            raise OSError("connection refused")
        if path == "/list-peers":
            return peers
        mod.sent.append(payload)
        return send_reply if send_reply is not None else {"ok": True}
    mod.post = post


def event(cwd, message="Claude needs your permission to use Write"):
    return json.dumps({"cwd": str(cwd), "message": message, "session_id": "s1"})


def logged(mod):
    return [json.loads(l) for l in Path(mod.LOG).read_text().splitlines()]


def test_peer_wait_is_sent_to_the_team_lead_only(hook):
    peers = [
        {"stable_id": "worktree", "cwd": str(hook.lead / ".claude/worktrees/x")},
        {"stable_id": "private", "cwd": str(hook.tmp / "ai-team-lead-private")},
        {"stable_id": "lead", "cwd": str(hook.lead)},
    ]
    fake_post(hook, peers)
    rec = hook.main(event(hook.tmp / "other"))
    assert rec["delivery"] == "sent"
    assert [p["to_stable_id"] for p in hook.sent] == ["lead"]
    assert "do not reply" in hook.sent[0]["text"]
    assert hook.banners == []
    assert logged(hook)[0]["delivery"] == "sent"


def test_ok_false_reply_counts_as_failed_and_banners(hook):
    fake_post(hook, [{"stable_id": "lead", "cwd": str(hook.lead)}],
              send_reply={"ok": False, "error": "unknown peer"})
    rec = hook.main(event(hook.tmp / "other"))
    assert rec["delivery"] == "failed"
    assert len(hook.banners) == 1


def test_no_team_lead_peer_banners(hook):
    fake_post(hook, [{"stable_id": "wt", "cwd": str(hook.lead / ".claude/worktrees/x")}])
    rec = hook.main(event(hook.tmp / "other"))
    assert rec["delivery"] == "no-peer"
    assert hook.sent == []
    assert len(hook.banners) == 1


def test_broker_down_is_failed(hook):
    fake_post(hook, [], raise_on="/list-peers")
    assert hook.main(event(hook.tmp / "other"))["delivery"] == "failed"


def test_team_lead_itself_banners_without_hive(hook):
    fake_post(hook, [], raise_on="/list-peers")
    rec = hook.main(event(hook.lead))
    assert rec["delivery"] == "self"
    assert len(hook.banners) == 1


def test_team_lead_worktree_is_a_peer_not_self(hook):
    fake_post(hook, [{"stable_id": "lead", "cwd": str(hook.lead)}])
    assert hook.main(event(hook.lead / ".claude/worktrees/x"))["delivery"] == "sent"


def test_bad_input_still_logs_error(hook):
    rec = hook.main("not json")
    assert rec["delivery"] == "error"
    assert logged(hook)[0]["delivery"] == "error"


def test_non_string_message_is_coerced(hook):
    fake_post(hook, [{"stable_id": "lead", "cwd": str(hook.lead)}])
    raw = json.dumps({"cwd": str(hook.tmp / "other"), "message": 42})
    assert hook.main(raw)["message"] == "42"


def test_team_lead_worktree_also_banners(hook):
    fake_post(hook, [{"stable_id": "lead", "cwd": str(hook.lead)}])
    hook.main(event(hook.lead / ".claude/worktrees/x"))
    assert len(hook.banners) == 1


def test_bad_input_banners(hook):
    hook.main("not json")
    assert len(hook.banners) == 1


def test_second_wait_in_window_is_deduped(hook):
    fake_post(hook, [{"stable_id": "lead", "cwd": str(hook.lead)}])
    assert hook.main(event(hook.tmp / "other"))["delivery"] == "sent"
    assert hook.main(event(hook.tmp / "other"))["delivery"] == "deduped"
    assert len(hook.sent) == 1
    assert hook.banners == []


def test_prefers_unsuffixed_team_lead_id(hook):
    peers = [{"stable_id": "lead-ab12", "cwd": str(hook.lead), "last_seen": "2026-11-06T20:00:00Z"},
             {"stable_id": "lead", "cwd": str(hook.lead), "last_seen": "2026-11-06T19:00:00Z"}]
    assert hook.team_lead_stable_id(peers) == "lead"


def test_log_is_capped(hook):
    hook.LOG_MAX_BYTES = 2000
    Path(hook.LOG).parent.mkdir(parents=True, exist_ok=True)
    Path(hook.LOG).write_text(('{"x": 1}\n') * 1000)
    hook.main("not json")
    assert Path(hook.LOG).stat().st_size <= 2000
    assert logged(hook)[-1]["delivery"] == "error"
