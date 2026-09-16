#!/usr/bin/env python3
"""Tests for the true-spend monitor.

Two of these guard mistakes that were made for real, in this script, on its
first run against the live API — so they are regression tests, not hypotheticals:

  - `cents_to_dollars` guards THE CENTS TRAP. The cost report's `amount` is in
    the lowest currency unit, and the API docs' own example is "123.45" meaning
    $1.23. Reading it as dollars is 100x wrong in the direction that says a
    runaway bill is fine.
  - `money` guards a MEASURED near-zero rendering as "$0.00" and so becoming
    indistinguishable from nothing measured. The whole point of this script is
    the difference between those two, and the first version erased it.
  - `matches_fleet_workspace` guards exact-name matching. The card asked for a
    workspace called `fleet`; the one that exists has a possessive prefix.
    Exact matching attributed every charge in it to nobody and reported the
    fleet's spend as $0.00 on the line directly below the one printing those
    same charges.

Run: python3 scripts/test_fleet_spend_monitor.py
"""
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

import fleet_spend_monitor as fsm  # noqa: E402


def test_cents_to_dollars():
    # The docs' own example: "123.45" is $1.23, not $123.45.
    assert abs(fsm.cents_to_dollars("123.45") - 1.2345) < 1e-9
    assert fsm.cents_to_dollars("0") == 0.0
    assert abs(fsm.cents_to_dollars("100") - 1.00) < 1e-9
    assert abs(fsm.cents_to_dollars("5500.0") - 55.00) < 1e-9
    print("ok: cents_to_dollars reads the lowest currency unit, not dollars")


def test_cents_to_dollars_is_the_only_conversion():
    """A second division by 100 anywhere is how the trap comes back."""
    src = open(os.path.join(SCRIPTS_DIR, "fleet_spend_monitor.py")).read()
    body_divisions = [
        line for line in src.splitlines()
        if "/ 100" in line and "def cents_to_dollars" not in line
        and not line.lstrip().startswith("#")
    ]
    # The one inside cents_to_dollars itself is expected; nothing else.
    assert len(body_divisions) == 1, f"unexpected /100 outside the converter: {body_divisions}"
    print("ok: /100 happens in exactly one place")


def test_money_keeps_a_measured_near_zero_visible():
    assert fsm.money(0) == "$0.00"          # a true zero
    assert fsm.money(0.0036) == "$0.0036"   # measured, tiny, must NOT read as $0.00
    assert fsm.money(0.004) != "$0.00"
    assert fsm.money(1.5) == "$1.50"
    assert fsm.money(1234.5) == "$1,234.50"
    print("ok: money() distinguishes a measured near-zero from nothing measured")


def test_fleet_workspace_matches_by_substring():
    assert fsm.matches_fleet_workspace("A Person's Fleet", fsm.FLEET_WORKSPACE_MATCH)
    assert fsm.matches_fleet_workspace("fleet", fsm.FLEET_WORKSPACE_MATCH)
    assert fsm.matches_fleet_workspace("FLEET ops", fsm.FLEET_WORKSPACE_MATCH)
    assert not fsm.matches_fleet_workspace("Default", fsm.FLEET_WORKSPACE_MATCH)
    assert not fsm.matches_fleet_workspace(None, fsm.FLEET_WORKSPACE_MATCH)
    print("ok: the fleet workspace is matched by substring, case-insensitively")


def test_could_not_look_is_never_a_zero():
    """Exit 2, not 0 — the three states, with the third loud."""
    saved = fsm.ADMIN_KEY_SERVICE
    fsm.ADMIN_KEY_SERVICE = "no-such-service-exists-for-this-test"
    try:
        rc = fsm.main(["--days", "30"])
    finally:
        fsm.ADMIN_KEY_SERVICE = saved
    assert rc == 2, f"a missing key must exit 2, not {rc}"
    print("ok: could-not-look exits 2, never 0")


if __name__ == "__main__":
    test_cents_to_dollars()
    test_cents_to_dollars_is_the_only_conversion()
    test_money_keeps_a_measured_near_zero_visible()
    test_fleet_workspace_matches_by_substring()
    test_could_not_look_is_never_a_zero()
    print("\nALL TESTS PASSED")
