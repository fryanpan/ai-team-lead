"""Swap level is not a signal; swap activity is.

The old check went RED above a flat 8.0GB of swap in use. Measured over 1,887
fleet-guard samples (2026-09-01..03), the MEDIAN swap in use was 8.0GB at 11
sessions and 10.5GB at 12 -- the fleet's normal size. So the ceiling sat on the
median and the check was becoming furniture, which is exactly what the four
standing reds before it were. macOS also never returns the allocation, so the
number ratchets up and stays there long after the pressure is gone.

Fabricated counters only -- never point a monitor's tests at live state.
"""
import importlib.util
import os

import pytest

_spec = importlib.util.spec_from_file_location(
    "fhc", os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "fleet_healthcheck.py"))
fhc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fhc)

PAGE = 16384
SPEC = {"name": "swap", "max_swapout_mb_s": 5.0, "sample_seconds": 10}


def _counters(monkeypatch, first, second, used_mb=9000.0):
    """Two vm_stat samples, plus a swap level that must NOT drive the verdict."""
    monkeypatch.setattr(fhc, "_sysctl",
                        lambda k: f"vm.swapusage: total = 10240.00M  used = {used_mb}M  free = 0.00M")
    seq = iter([first, second])
    monkeypatch.setattr(fhc, "_vm_counters", lambda: next(seq))
    monkeypatch.setattr(fhc.time, "sleep", lambda _s: None)


def _sample(swapins, swapouts):
    return {"Swapins": swapins, "Swapouts": swapouts, "page": PAGE}


def _pages_for(mb_per_s, seconds=10):
    return int(mb_per_s * 1e6 * seconds / PAGE)


def test_a_full_swap_file_with_no_activity_is_green(monkeypatch):
    """The exact 2026-09-03 case: 8.7GB in use, nothing moving."""
    _counters(monkeypatch, _sample(1000, 500), _sample(1000, 500), used_mb=8908.8)
    ok, msg = fhc.check_swap(SPEC)
    assert ok
    assert "8.7GB in use" in msg


def test_sustained_swapout_is_red_even_at_a_low_level(monkeypatch):
    """Pressure is the signal, and it can start from an empty swap file."""
    _counters(monkeypatch, _sample(0, 0), _sample(0, _pages_for(20.0)), used_mb=512.0)
    ok, msg = fhc.check_swap(SPEC)
    assert not ok
    assert "20.0MB/s" in msg


def test_reading_pages_back_in_is_not_pressure(monkeypatch):
    """Swapins mean the machine is recovering, not drowning."""
    _counters(monkeypatch, _sample(0, 0), _sample(_pages_for(50.0), 0))
    ok, msg = fhc.check_swap(SPEC)
    assert ok
    assert "50.0MB/s in" in msg


def test_the_ceiling_is_a_rate_not_a_level(monkeypatch):
    _counters(monkeypatch, _sample(0, 0), _sample(0, _pages_for(4.9)))
    assert fhc.check_swap(SPEC)[0]
    _counters(monkeypatch, _sample(0, 0), _sample(0, _pages_for(5.1)))
    assert not fhc.check_swap(SPEC)[0]


def test_the_level_is_still_reported_as_context(monkeypatch):
    """Dropping it entirely would lose the number Bryan has been watching."""
    _counters(monkeypatch, _sample(0, 0), _sample(0, 0), used_mb=12288.0)
    ok, msg = fhc.check_swap(SPEC)
    assert ok and "12.0GB in use" in msg


def test_an_unreadable_vm_stat_is_probe_failed_not_green(monkeypatch):
    monkeypatch.setattr(fhc, "_sysctl",
                        lambda k: "vm.swapusage: total = 1M  used = 1.00M  free = 0M")
    monkeypatch.setattr(fhc, "_vm_counters", lambda: None)
    monkeypatch.setattr(fhc.time, "sleep", lambda _s: None)
    ok, msg = fhc.check_swap(SPEC)
    assert not ok and "PROBE-FAILED" in msg


def test_a_broken_sysctl_is_probe_failed(monkeypatch):
    monkeypatch.setattr(fhc, "_sysctl", lambda k: "nonsense")
    ok, msg = fhc.check_swap(SPEC)
    assert not ok and "PROBE-FAILED" in msg
