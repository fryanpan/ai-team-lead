"""Free boot-disk space under 20 GB turns the guard critical (red).

Set by the owner on 2026-09-30, when free space had fallen from ~30 GB to 16 GB
with nothing alerting.
"""
import importlib.util
import os

import pytest

_spec = importlib.util.spec_from_file_location(
    "guard", os.path.join(os.path.dirname(__file__), "..", "scripts",
                          "fleet_guard.py"))
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


@pytest.mark.parametrize("free_gb,expected", [
    (30.0, "ok"),
    (22.0, "warn"),
    (19.9, "critical"),
    (16.0, "critical"),
])
def test_boot_disk_band(free_gb, expected):
    assert guard.band(free_gb, *guard.BOOT_FREE_GB,
                      higher_is_worse=False) == expected
