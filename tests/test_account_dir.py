"""An account folder shares settings, plugins and transcripts, never the login.

Two failure modes this guards: a spare folder that links or copies the main
login's account record (two accounts would then report as one), and a refresh
that overwrites the spare's own login record with the main one's.
"""
import importlib.util
import json
import os
import stat

import pytest

_spec = importlib.util.spec_from_file_location(
    "account_dir", os.path.join(os.path.dirname(__file__), "..", "scripts", "account_dir.py"))
account_dir = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(account_dir)


MAIN_CFG = {
    "oauthAccount": {"emailAddress": "main@example.com"},
    "userID": "u-main",
    "cachedGrowthBookFeatures": {"x": 1},
    "mcpServers": {"hive": {"command": "hive"}},
    "hasCompletedOnboarding": True,
    "projects": {"/p": {"hasTrustDialogAccepted": True, "mcpServers": {"d": {}},
                        "lastCost": 3.2, "lastSessionId": "s1"}},
}


@pytest.fixture
def main_dir(tmp_path):
    src = tmp_path / "main"
    src.mkdir()
    for d in ("projects", "plugins", "skills", "sessions", "cache", "backups"):
        (src / d).mkdir()
    for f in ("settings.json", "history.jsonl", "notes.md", ".credentials.json"):
        (src / f).write_text("{}")
    (src / "mystery-new-dir").mkdir()
    cfg = tmp_path / "main.claude.json"
    cfg.write_text(json.dumps(MAIN_CFG))
    return src, cfg


def run(folder, src, cfg, *extra):
    return account_dir.main([str(folder), "--source", str(src),
                             "--source-config", str(cfg), *extra])


def test_links_shared_entries_and_nothing_per_account(tmp_path, main_dir):
    src, cfg = main_dir
    folder = tmp_path / "spare"
    assert run(folder, src, cfg, "--execute") == 0
    for name in ("projects", "plugins", "skills", "settings.json", "history.jsonl", "notes.md"):
        assert os.readlink(folder / name) == str(src / name)
    for name in ("sessions", "cache", "backups", ".credentials.json", "mystery-new-dir"):
        assert not os.path.lexists(folder / name)


def test_seeded_config_carries_no_account_keys(tmp_path, main_dir):
    src, cfg = main_dir
    folder = tmp_path / "spare"
    run(folder, src, cfg, "--execute")
    seeded = json.loads((folder / ".claude.json").read_text())
    assert "oauthAccount" not in seeded
    assert "userID" not in seeded
    assert "cachedGrowthBookFeatures" not in seeded
    assert seeded["mcpServers"] == MAIN_CFG["mcpServers"]
    assert seeded["projects"] == {"/p": {"hasTrustDialogAccepted": True, "mcpServers": {"d": {}}}}
    assert stat.S_IMODE(os.stat(folder / ".claude.json").st_mode) == 0o600


def test_dry_run_changes_nothing(tmp_path, main_dir):
    src, cfg = main_dir
    folder = tmp_path / "spare"
    run(folder, src, cfg)
    assert not folder.exists()


def test_rerun_is_idempotent_and_keeps_the_login(tmp_path, main_dir):
    src, cfg = main_dir
    folder = tmp_path / "spare"
    run(folder, src, cfg, "--execute")
    spare_cfg = json.loads((folder / ".claude.json").read_text())
    spare_cfg["oauthAccount"] = {"emailAddress": "spare@example.com"}
    (folder / ".claude.json").write_text(json.dumps(spare_cfg))
    assert run(folder, src, cfg, "--execute") == 0
    assert json.loads((folder / ".claude.json").read_text())["oauthAccount"]["emailAddress"] == "spare@example.com"


def test_sync_refreshes_mcp_but_not_the_login(tmp_path, main_dir):
    src, cfg = main_dir
    folder = tmp_path / "spare"
    run(folder, src, cfg, "--execute")
    spare_cfg = json.loads((folder / ".claude.json").read_text())
    spare_cfg["oauthAccount"] = {"emailAddress": "spare@example.com"}
    spare_cfg["projects"]["/p"]["lastCost"] = 9.9
    (folder / ".claude.json").write_text(json.dumps(spare_cfg))
    updated = dict(MAIN_CFG, mcpServers={"hive": {}, "new": {}})
    cfg.write_text(json.dumps(updated))
    run(folder, src, cfg, "--execute", "--sync-config")
    out = json.loads((folder / ".claude.json").read_text())
    assert out["oauthAccount"]["emailAddress"] == "spare@example.com"
    assert set(out["mcpServers"]) == {"hive", "new"}
    assert out["projects"]["/p"]["lastCost"] == 9.9


def test_a_wrong_existing_link_is_a_conflict_not_replaced(tmp_path, main_dir):
    src, cfg = main_dir
    folder = tmp_path / "spare"
    folder.mkdir()
    os.symlink("/elsewhere", folder / "projects")
    assert run(folder, src, cfg, "--execute") == 1
    assert os.readlink(folder / "projects") == "/elsewhere"


def test_a_linked_global_config_is_refused(tmp_path, main_dir):
    src, cfg = main_dir
    folder = tmp_path / "spare"
    folder.mkdir()
    os.symlink(cfg, folder / ".claude.json")
    assert run(folder, src, cfg, "--execute", "--sync-config") == 1
    assert json.loads(cfg.read_text()) == MAIN_CFG
