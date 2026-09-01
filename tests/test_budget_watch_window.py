"""The 5h watcher must see subagents, and must judge the window absolutely.

Two defects found 2026-09-01, both in the instrument built to catch the
2026-08-31 account exhaustions:

  1. It globbed `<project>/*.jsonl`, so it read main-agent sessions only. The
     finding that motivated the whole script was that subagent fan-out is ~77%
     of fleet burn. It shipped blind to exactly that.
  2. Its verdict was a SHARE. A share is scale-free -- the same line prints at
     4% of the pool and at 96% -- so it stayed green through both events.

Every case runs against fabricated transcripts. Never point a monitor's tests
at live state; it fires real alerts someone has to chase.
"""
import datetime
import importlib.util
import json
import os

import pytest

_spec = importlib.util.spec_from_file_location(
    "fbw", os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "fleet_budget_watch.py"))
fbw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fbw)


def _write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        for i, (when, tok) in enumerate(rows):
            fh.write(json.dumps({
                "timestamp": when.isoformat().replace("+00:00", "Z"),
                "requestId": f"{path}-{i}",
                "message": {"usage": {"input_tokens": tok}},
            }) + "\n")


def test_subagent_transcripts_are_counted(tmp_path):
    """The whole point of the instrument. A nested transcript must be found."""
    proj = tmp_path / "a-project"
    _write(str(proj / "main.jsonl"), [])
    (proj / "sess" / "subagents").mkdir(parents=True)
    (proj / "sess" / "subagents" / "agent-1.jsonl").write_text("")
    found = fbw.transcripts_under(str(proj))
    assert any(p.endswith("agent-1.jsonl") for p in found)
    assert any(p.endswith("main.jsonl") for p in found)


def test_a_deeply_nested_transcript_is_still_found(tmp_path):
    """Nesting depth is an implementation detail of the harness, not a contract."""
    proj = tmp_path / "p"
    deep = proj / "s" / "subagents" / "nested" / "deeper"
    deep.mkdir(parents=True)
    (deep / "agent-x.jsonl").write_text("")
    assert any(p.endswith("agent-x.jsonl")
               for p in fbw.transcripts_under(str(proj)))


def test_a_project_with_no_transcripts_returns_empty_not_an_error(tmp_path):
    proj = tmp_path / "empty"
    proj.mkdir()
    assert fbw.transcripts_under(str(proj)) == []


def test_window_burn_dedupes_on_request_id(tmp_path):
    """A transcript writes one record per content block, all repeating the same
    usage object. Summing records counts each billed request many times."""
    now = datetime.datetime.now(datetime.timezone.utc)
    p = tmp_path / "t.jsonl"
    with open(p, "w") as fh:
        for _ in range(4):
            fh.write(json.dumps({
                "timestamp": now.isoformat().replace("+00:00", "Z"),
                "requestId": "same-request",
                "message": {"usage": {"input_tokens": 100}},
            }) + "\n")
    tok, turns = fbw.window_burn(str(p), now - datetime.timedelta(hours=5))
    assert (tok, turns) == (100, 1)


def test_window_burn_excludes_turns_before_the_cutoff(tmp_path):
    now = datetime.datetime.now(datetime.timezone.utc)
    p = tmp_path / "t.jsonl"
    _write(str(p), [(now - datetime.timedelta(hours=9), 500),
                    (now - datetime.timedelta(hours=1), 7)])
    tok, turns = fbw.window_burn(str(p), now - datetime.timedelta(hours=5))
    assert (tok, turns) == (7, 1)


def test_the_ceiling_is_below_the_lowest_observed_exhaustion(tmp_path):
    """Calibration guard. The observed burn-throughs sat at 760M and 800M in a
    5h window; a ceiling at or above that fires only once it is too late."""
    assert fbw.WINDOW_CEILING_TOKENS < 750_000_000
    assert fbw.WINDOW_WATCH_TOKENS < fbw.WINDOW_CEILING_TOKENS


def test_the_watch_line_leaves_room_to_act():
    """A watch line that is nearly the ceiling is a ceiling with extra steps."""
    assert fbw.WINDOW_WATCH_TOKENS <= 0.85 * fbw.WINDOW_CEILING_TOKENS
