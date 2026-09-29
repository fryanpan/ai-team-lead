"""--account-dir puts a spawn on a second account's login folder.

The Keychain entry for a folder's login is keyed to the exact path string, so
the spelling respawn passes must be the one /login was run with. And a folder
with no login must be refused: a session spawned there would sit at a login
prompt with nobody to answer it.
"""
import importlib.util
import json
import os

import pytest

_spec = importlib.util.spec_from_file_location(
    "respawn", os.path.join(os.path.dirname(__file__), "..", ".claude",
                            "skills", "respawn-sessions", "respawn.py"))
respawn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(respawn)


@pytest.fixture(autouse=True)
def _reset():
    respawn.ACCOUNT_DIR = None
    yield
    respawn.ACCOUNT_DIR = None


def _folder(tmp_path, cfg):
    d = tmp_path / "spare1"
    d.mkdir()
    if cfg is not None:
        (d / ".claude.json").write_text(json.dumps(cfg))
    return d


def test_a_logged_in_folder_resolves_to_its_realpath(tmp_path):
    d = _folder(tmp_path, {"oauthAccount": {"accountUuid": "x"}})
    alias = tmp_path / "alias"
    os.symlink(d, alias)
    path, problem = respawn.resolve_account_dir(str(alias) + "/")
    assert problem is None
    assert path == os.path.realpath(d)


def test_a_folder_with_no_login_is_refused(tmp_path):
    d = _folder(tmp_path, {"mcpServers": {}})
    _, problem = respawn.resolve_account_dir(str(d))
    assert problem and "/login" in problem


def test_a_missing_folder_is_refused(tmp_path):
    _, problem = respawn.resolve_account_dir(str(tmp_path / "nope"))
    assert problem


def test_a_linked_global_config_is_refused(tmp_path):
    real = tmp_path / "main.json"
    real.write_text(json.dumps({"oauthAccount": {"accountUuid": "main"}}))
    d = _folder(tmp_path, None)
    os.symlink(real, d / ".claude.json")
    _, problem = respawn.resolve_account_dir(str(d))
    assert problem and "link" in problem


def test_spawn_env_carries_the_folder_only_when_set(tmp_path):
    assert respawn.account_env() == []
    respawn.ACCOUNT_DIR = "/x/spare1"
    assert respawn.account_env() == ["-e", "CLAUDE_CONFIG_DIR=/x/spare1"]


def test_channel_flags_read_the_account_folders_config(tmp_path):
    """Project-scoped MCP servers live in the folder's own .claude.json."""
    proj = tmp_path / "proj"
    proj.mkdir()
    d = _folder(tmp_path, {"oauthAccount": {"accountUuid": "x"},
                           "projects": {os.path.realpath(proj): {"mcpServers": {
                               "plugin_discord_discord": {}}}}})
    respawn.ACCOUNT_DIR = str(d)
    assert "server:plugin_discord_discord" in respawn.direct_channel_flags(str(proj))
