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
    """Calibration guard, against the measured record rather than a guess.

    Six 5-hour rate-limit episodes are recorded in the transcripts between
    2026-08-29 and 2026-09-01. The rolling window at the first rejection of each
    ranged 442M to 695M. A ceiling at or above the LOWEST of those is green
    through the episode it most needed to predict -- setting it near the median
    (507M) would have missed two of six."""
    assert fbw.WINDOW_CEILING_TOKENS < 442_000_000  # lowest measured rejection
    assert fbw.WINDOW_WATCH_TOKENS < fbw.WINDOW_CEILING_TOKENS


def test_the_watch_line_leaves_room_to_act():
    """A watch line that is nearly the ceiling is a ceiling with extra steps."""
    assert fbw.WINDOW_WATCH_TOKENS <= 0.85 * fbw.WINDOW_CEILING_TOKENS


# --- verdict severity comes from the level, never from the split -------------
#
# Regression tests for 2026-09-01 (evening). The absolute thresholds were added
# that afternoon and checked first, but the two share rules stayed as `elif`
# fallbacks, so they could only run BELOW the watch line. The watch then fired
# BREACH at 189M while the window was FALLING -- 265M lower than when the
# standing decision was recorded ninety minutes earlier.

_ROWS = [("noisy-project", {"tokens": 110}), ("ai-team-lead", {"tokens": 20})]


def _verdict(fleet_tokens, share=0.61):
    """Concentrated by construction: share over the floor, headroom under it."""
    return fbw.verdict_for(fleet_tokens, share, 1.0 - share, 0.40,
                           "noisy-project", _ROWS)


def test_a_small_window_is_ok_however_concentrated_it_is():
    """The actual shape that misfired: 189M, 61% unprotected, 39% headroom."""
    verdict, detail = _verdict(189_000_000)
    assert verdict == "OK", detail
    assert "BREACH" not in detail


def test_a_concentrated_small_window_still_reports_who_is_spending():
    """Downgrading the verdict must not delete the information."""
    _, detail = _verdict(189_000_000)
    assert "61%" in detail
    assert "noisy-project" in detail


def test_the_split_cannot_manufacture_a_breach_at_any_level_below_the_watch_line():
    """A share is scale-free, so it must not decide severity anywhere here."""
    for tokens in (1_000_000, 100_000_000, fbw.WINDOW_WATCH_TOKENS - 1):
        assert _verdict(tokens)[0] == "OK", tokens


def test_concentration_escalates_a_level_that_already_matters():
    """Past the watch line the split is load-bearing again: WATCH -> BREACH."""
    assert _verdict(fbw.WINDOW_WATCH_TOKENS)[0] == "BREACH"
    assert _verdict(fbw.WINDOW_WATCH_TOKENS, share=0.20)[0] == "WATCH"


def test_the_ceiling_breaches_regardless_of_how_evenly_it_is_split():
    """An evenly-shared window past the ceiling is still past the ceiling."""
    verdict, detail = _verdict(fbw.WINDOW_CEILING_TOKENS, share=0.05)
    assert verdict == "BREACH"
    assert "442M" in detail, "must carry the measured exhaustion point"


def test_every_verdict_states_the_absolute_level():
    """The level is the part a reader cannot reconstruct from the split."""
    for tokens in (50_000_000, fbw.WINDOW_WATCH_TOKENS,
                   fbw.WINDOW_CEILING_TOKENS):
        assert "M" in _verdict(tokens)[1]
