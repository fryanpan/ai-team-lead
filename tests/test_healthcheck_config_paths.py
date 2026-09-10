"""The generated config must point at the state files that are actually written.

A path spelled out as a literal in the installer while the writer resolves its
own directory is a silent divergence: the check keeps reading a file nobody
updates any more, and every value it reports is from whenever the writer moved.
That happened to `guard-state.json` when fleet_guard.py migrated to /opt/fleet.

Only the staleness bound turned it into a RED. Had the abandoned copy been
touched by anything, the check would have gone on reporting both monitor loops
up off a frozen file -- which is the state this whole check exists to catch.
"""

import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def installer():
    return load("install_healthcheck",
                os.path.join(ROOT, "scripts", "install_healthcheck.py"))


@pytest.fixture(scope="module")
def guard():
    return load("fleet_guard", os.path.join(ROOT, "scripts", "fleet_guard.py"))


def spec_named(installer, name):
    for s in installer.BASE_CHECKS:
        if s.get("name") == name:
            return s
    raise AssertionError(f"no check named {name!r}")


def test_monitor_loops_reads_the_file_the_guard_writes(installer, guard):
    """The one that broke: guard moved roots, config kept the old literal."""
    configured = os.path.expanduser(spec_named(installer, "monitor loops")["path"])
    assert configured == guard.STATE, (
        f"monitor loops check reads {configured}, guard writes {guard.STATE}"
    )


def test_monitor_loops_path_is_not_a_literal_home_path(installer):
    """A resolved path survives a deploy-root move; a spelled-out one does not."""
    raw = spec_named(installer, "monitor loops")["path"]
    assert not raw.startswith("~"), (
        "path is spelled out rather than resolved from the deploy root"
    )


def test_monitor_loops_keeps_a_staleness_bound(installer):
    """Without it an abandoned file reports every loop up, indefinitely."""
    assert spec_named(installer, "monitor loops").get("max_age_minutes")
