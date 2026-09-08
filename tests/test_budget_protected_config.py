"""The protected-projects map moved out of this repo, which is public.

A protected project is one Bryan committed to this week, so the list itself is
the private fact -- naming it in a public commit leaks the week's priorities.
The map now loads from ~/.config/team-lead/protected-projects.json.

The failure this guards is silent under-protection: if the file is missing or
malformed and the loader falls back quietly, a project that should hold a
reserve reads as unprotected, the split verdict looks BETTER than the truth,
and nothing in the report says why. Every fallback path must warn.

Fabricated names only -- never point a monitor's tests at live state.
"""
import importlib.util
import json
import os

_spec = importlib.util.spec_from_file_location(
    "fbw", os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "fleet_budget_watch.py"))
fbw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fbw)


def _load(tmp_path, payload, name="protected.json"):
    p = tmp_path / name
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    fbw.PROTECTED_PATH = str(p)
    return fbw._load_protected()


def test_valid_config_loads_verbatim_and_does_not_warn(tmp_path):
    loaded, warning = _load(tmp_path, {"alpha-project": 0.3, "ai-team-lead": 0.1})
    assert loaded == {"alpha-project": 0.3, "ai-team-lead": 0.1}
    assert warning is None


def test_missing_file_falls_back_and_says_so(tmp_path):
    fbw.PROTECTED_PATH = str(tmp_path / "absent.json")
    loaded, warning = fbw._load_protected()
    assert loaded == {"ai-team-lead": 0.10}
    assert "absent.json" in warning


def test_malformed_json_falls_back_and_says_so(tmp_path):
    loaded, warning = _load(tmp_path, "{not json")
    assert loaded == {"ai-team-lead": 0.10}
    assert "unreadable" in warning


def test_empty_object_falls_back(tmp_path):
    loaded, warning = _load(tmp_path, {})
    assert loaded == {"ai-team-lead": 0.10}
    assert "non-empty" in warning


def test_share_outside_range_falls_back_and_names_the_offender(tmp_path):
    """A share of 5 would reserve 500% of the window; 0 reserves nothing."""
    loaded, warning = _load(tmp_path, {"alpha-project": 5, "beta-project": 0})
    assert loaded == {"ai-team-lead": 0.10}
    assert "alpha-project" in warning and "beta-project" in warning


def test_non_object_payload_falls_back(tmp_path):
    loaded, warning = _load(tmp_path, ["alpha-project"])
    assert loaded == {"ai-team-lead": 0.10}
    assert "non-empty object" in warning


def test_the_fallback_names_only_the_public_coordination_project(tmp_path):
    """The fallback ships in a public file, so it may name only public things.

    This is the assertion that would have failed before the map moved out:
    the reserve for the week's private project used to sit in this source file
    as a literal.
    """
    fbw.PROTECTED_PATH = str(tmp_path / "absent.json")
    loaded, _ = fbw._load_protected()
    assert list(loaded) == ["ai-team-lead"]
