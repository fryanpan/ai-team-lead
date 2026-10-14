"""A check with min_interval_hours reuses a recent GREEN and reruns a RED.

The transcript-archive walk is too heavy to run hourly, so it runs daily. The
reuse must never hide a failure: only a pass is carried forward, and a carried
line says how old it is.
"""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "fleet_healthcheck_spaced", os.path.join(ROOT, "scripts", "fleet_healthcheck.py"))
hc = importlib.util.module_from_spec(_spec)
sys.modules["fleet_healthcheck_spaced"] = hc
_spec.loader.exec_module(hc)

SPEC = {"type": "archive_backlog", "name": "transcript archive", "min_interval_hours": 24}


def counter(result):
    calls = []
    def fn(spec):
        calls.append(spec)
        return result
    return fn, calls


def test_green_is_reused_inside_the_interval():
    cache = {}
    fn, calls = counter((True, "transcript archive: 0 unarchived"))
    hc.run_spaced(SPEC, fn, cache, now=1000.0)
    ok, detail = hc.run_spaced(SPEC, fn, cache, now=1000.0 + 3600)
    assert ok and len(calls) == 1
    assert "measured 1.0h ago" in detail


def test_green_reruns_after_the_interval():
    cache = {}
    fn, calls = counter((True, "fine"))
    hc.run_spaced(SPEC, fn, cache, now=0.0)
    hc.run_spaced(SPEC, fn, cache, now=24 * 3600 + 1)
    assert len(calls) == 2


def test_red_is_never_reused():
    cache = {}
    fn, calls = counter((False, "transcript archive: 5 never archived"))
    hc.run_spaced(SPEC, fn, cache, now=0.0)
    ok, _ = hc.run_spaced(SPEC, fn, cache, now=60.0)
    assert not ok and len(calls) == 2


def test_raising_check_is_red_and_not_cached_as_green():
    cache = {}
    def boom(spec):
        raise RuntimeError("x")
    ok, detail = hc.run_spaced(SPEC, boom, cache, now=0.0)
    assert not ok and "check raised" in detail
    assert cache["transcript archive"]["ok"] is False


def test_unspaced_check_always_runs_and_skips_cache():
    cache = {}
    fn, calls = counter((True, "ok"))
    spec = {"type": "swap", "name": "swap"}
    hc.run_spaced(spec, fn, cache)
    hc.run_spaced(spec, fn, cache)
    assert len(calls) == 2 and cache == {}


def test_archive_backlog_is_daily_by_default():
    cache = {}
    fn, calls = counter((True, "fine"))
    spec = {"type": "archive_backlog", "name": "transcript archive"}
    hc.run_spaced(spec, fn, cache, now=0.0)
    hc.run_spaced(spec, fn, cache, now=23 * 3600)
    assert len(calls) == 1
