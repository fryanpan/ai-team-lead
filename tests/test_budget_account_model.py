"""The watcher must say WHICH POOL it measured and WHICH MODEL spent it.

2026-09-03: Bryan had to read three /usage meters to Team Lead by hand because
the watcher knew neither. It reported raw tokens with no account label, so a
reading taken across an account switch looked like a continuation of the same
series; and it summed every model together, so `fryanpan@gmail.com` reached
100% on its Fable sub-meter without anything noticing.

Neither gap is closed by reading a meter -- the script cannot, meters live
behind `/usage` in a session pane and pane-scraping is forbidden. They are
closed by labelling what IS measurable and by making a hand-entered meter
reading age visibly.
"""
import importlib.util
import os
from datetime import datetime, timedelta, timezone

import pytest

_spec = importlib.util.spec_from_file_location(
    "fbw", os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "fleet_budget_watch.py"))
fbw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fbw)

NOW = datetime(2026, 9, 3, 12, 30, tzinfo=timezone.utc)


# --- model attribution -------------------------------------------------------

def test_burn_is_attributed_to_the_model_that_spent_it(tmp_path):
    t = tmp_path / "s.jsonl"
    t.write_text(
        '{"timestamp":"2026-09-03T12:00:00Z","requestId":"a",'
        '"message":{"model":"claude-fable-5","usage":{"input_tokens":10,"output_tokens":1}}}\n'
        '{"timestamp":"2026-09-03T12:01:00Z","requestId":"b",'
        '"message":{"model":"claude-opus-4-8","usage":{"input_tokens":5,"output_tokens":0}}}\n')
    tok, turns, by_model = fbw.window_burn(str(t), NOW - timedelta(hours=5))
    assert tok == 16 and turns == 2
    assert by_model == {"Fable": 11, "Opus 4.8": 5}


def test_an_unlabelled_model_is_kept_verbatim_not_dropped(tmp_path):
    t = tmp_path / "s.jsonl"
    t.write_text(
        '{"timestamp":"2026-09-03T12:00:00Z","requestId":"a",'
        '"message":{"model":"some-future-model","usage":{"input_tokens":7}}}\n')
    tok, _, by_model = fbw.window_burn(str(t), NOW - timedelta(hours=5))
    assert tok == 7
    assert by_model == {"some-future-model": 7}, "silently dropping is the bug"


def test_the_requestid_dedupe_still_holds_per_model(tmp_path):
    """One billed request repeated per content block must count once."""
    t = tmp_path / "s.jsonl"
    line = ('{"timestamp":"2026-09-03T12:00:00Z","requestId":"a",'
            '"message":{"model":"claude-fable-5","usage":{"input_tokens":10}}}\n')
    t.write_text(line * 3)
    tok, turns, by_model = fbw.window_burn(str(t), NOW - timedelta(hours=5))
    assert (tok, turns, by_model) == (10, 1, {"Fable": 10})


# --- account labelling -------------------------------------------------------

def test_a_reading_on_a_new_account_is_not_a_continuation():
    """Comparing burn across a /login is comparing two different pools."""
    assert fbw.account_changed("a@x.com", "b@x.com")


def test_the_same_account_is_a_continuation():
    assert not fbw.account_changed("a@x.com", "a@x.com")


def test_no_previous_account_is_not_a_change():
    """First run ever -- nothing to have switched away from."""
    assert not fbw.account_changed(None, "a@x.com")


def test_an_unreadable_account_is_not_reported_as_a_switch():
    """auth status can fail; that is missing data, not a pool change."""
    assert not fbw.account_changed("a@x.com", None)


# --- meter ledger staleness --------------------------------------------------

def test_a_meter_reading_reports_its_own_age():
    read_at = (NOW - timedelta(hours=3)).isoformat()
    assert fbw.meter_age_hours({"read_at": read_at}, NOW) == pytest.approx(3.0)


def test_a_pool_with_no_reading_has_no_age():
    assert fbw.meter_age_hours({}, NOW) is None


def test_pools_are_ordered_by_which_resets_next():
    ledger = {
        "team@x": {"resets": "2026-09-07T07:00:00-07:00"},
        "active@x": {"resets": "2026-09-04T03:59:00-07:00"},
        "personal@x": {"resets": "2026-09-03T17:30:00-07:00"},
    }
    assert fbw.pools_by_next_reset(ledger) == ["personal@x", "active@x", "team@x"]


def test_a_pool_with_no_reset_time_sorts_last_rather_than_crashing():
    ledger = {"known": {"resets": "2026-09-04T03:59:00-07:00"}, "unknown": {}}
    assert fbw.pools_by_next_reset(ledger) == ["known", "unknown"]
