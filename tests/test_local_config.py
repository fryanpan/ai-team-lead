"""This machine's own values are read from a local file, never from the repo.

Hostnames, local service addresses, launchd job names and launch details live
in the `local:` block of the gitignored registry.yaml. Each script that needs
one reads it through scripts/local_config.py, and each has three results: the
value, a loud "could not read", or a loud "key not set". None of them may fall
back to a built-in value, because a guessed address that answers is some other
service and reads as healthy.
"""
import importlib.util
import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
EXAMPLE = os.path.join(ROOT, "registry.yaml.example")
HOOK = os.path.join(ROOT, "plugin", "team-lead-fleet", "hooks",
                    "permission-wait-alert.py")
RESPAWN = os.path.join(ROOT, ".claude", "skills", "respawn-sessions", "respawn.py")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def lc():
    return load("local_config_under_test", os.path.join(SCRIPTS, "local_config.py"))


@pytest.fixture
def installer():
    return load("install_healthcheck_lc", os.path.join(SCRIPTS, "install_healthcheck.py"))


@pytest.fixture
def unreadable(monkeypatch, tmp_path):
    """Point every reader at a file that does not exist."""
    monkeypatch.setenv("TEAM_LEAD_LOCAL_CONFIG", str(tmp_path / "absent.yaml"))


# ── the reader ───────────────────────────────────────────────────────────────

REGISTRY_TEXT = """\
calendars:
  primary: someone@example.com

local:
  # a comment inside the block
  hive_url: http://127.0.0.1:8000   # trailing comment
  board_tunnel_host: "board.example.com"
  notion_tunnel_host: "notion.example.com"   # a comment after a quoted value
  no_discord_state_dir: '{"a": "#x"}'  # and after quoted JSON
  discord_empty_allowlist: '{"a": [], "b": "#not-a-comment"}'
  empty_one:

projects:
  my-app:
    path: ~/dev/my-app
    hive_url: not-this-one
"""


def test_parse_reads_only_the_local_block(lc):
    values = lc.parse_local(REGISTRY_TEXT)
    assert values == {
        "hive_url": "http://127.0.0.1:8000",
        "board_tunnel_host": "board.example.com",
        "discord_empty_allowlist": '{"a": [], "b": "#not-a-comment"}',
        "notion_tunnel_host": "notion.example.com",
        "no_discord_state_dir": '{"a": "#x"}',
    }


def test_parse_says_when_there_is_no_block(lc):
    assert lc.parse_local("projects:\n  my-app:\n    path: ~/x\n") is None


def test_a_missing_file_is_could_not_read_not_empty(lc, tmp_path):
    with pytest.raises(lc.LocalConfigError, match="COULD NOT READ"):
        lc.load(str(tmp_path / "absent.yaml"))


def test_a_registry_without_the_block_is_could_not_read(lc, tmp_path):
    p = tmp_path / "registry.yaml"
    p.write_text("projects:\n  my-app:\n    path: ~/x\n")
    with pytest.raises(lc.LocalConfigError, match="no `local:` block"):
        lc.load(str(p))


def test_a_missing_key_is_named(lc, tmp_path):
    p = tmp_path / "registry.yaml"
    p.write_text(REGISTRY_TEXT)
    assert lc.require("hive_url", path=str(p)) == ["http://127.0.0.1:8000"]
    with pytest.raises(lc.LocalConfigError, match="github_broker_url, empty_one"):
        lc.require("hive_url", "github_broker_url", "empty_one", path=str(p))


def test_the_deployed_json_copy_is_read_the_same_way(lc, tmp_path):
    p = tmp_path / "local-config.json"
    p.write_text(json.dumps({"hive_url": "http://127.0.0.1:8000"}))
    assert lc.get("hive_url", path=str(p)) == "http://127.0.0.1:8000"
    p.write_text("{not json")
    with pytest.raises(lc.LocalConfigError, match="not valid JSON"):
        lc.load(str(p))


def test_the_example_lists_exactly_the_keys_scripts_read(lc):
    """A key added to a script and not to the example leaves a fresh clone
    with an error that names a key nobody documented."""
    with open(EXAMPLE) as f:
        example = lc.parse_local(f.read())
    assert example is not None, "registry.yaml.example has no `local:` block"
    assert set(example) == set(lc.KEYS)


def test_the_local_block_is_not_read_as_projects(lc):
    """The registry's project parsers must not see the block's keys."""
    respawn = load("respawn_lc_projects", RESPAWN)
    sys.path.insert(0, SCRIPTS)
    import refresh_team_state as rts
    for parse in (respawn.parse_registry, rts.parse_registry):
        projects = parse(EXAMPLE)
        assert not set(projects) & (set(lc.KEYS) | {"local"})
        assert "my-app" in projects


# ── the installer ────────────────────────────────────────────────────────────

def test_fill_replaces_every_token(installer, lc):
    with open(EXAMPLE) as f:
        values = lc.parse_local(f.read())
    filled = installer.fill_local(installer.BASE_CHECKS, values)
    assert "{local:" not in json.dumps(filled) and "{port:" not in json.dumps(filled)
    ports = {c["name"]: c["port"] for c in filled if c["type"] == "port"}
    assert ports["claude-hive"] == 8000 and ports["github broker"] == 8001
    urls = {c["name"]: c["url"] for c in filled if c["type"] == "http"}
    assert urls["live-feedback tunnel"] == "https://board.example.com/"
    assert urls["github broker"] == "http://127.0.0.1:8001/health"


def test_base_checks_use_only_documented_keys(installer, lc):
    used = {m.group(2) for m in
            installer.LOCAL_TOKEN.finditer(json.dumps(installer.BASE_CHECKS))}
    assert used and used <= set(lc.KEYS)


def test_fill_names_every_missing_key(installer):
    with pytest.raises(installer.local_config.LocalConfigError) as e:
        installer.fill_local(installer.BASE_CHECKS, {"hive_url": "http://127.0.0.1:8000"})
    assert "COULD NOT READ" in str(e.value)
    assert "board_tunnel_host" in str(e.value) and "github_broker_url" in str(e.value)


def test_fill_refuses_a_url_with_no_port(installer):
    with pytest.raises(installer.local_config.LocalConfigError, match="no port"):
        installer.fill_local({"port": "{port:hive_url}"}, {"hive_url": "http://127.0.0.1"})


def test_install_deploys_nothing_when_the_block_is_missing(
        installer, tmp_path, monkeypatch, capsys):
    registry = tmp_path / "registry.yaml"
    registry.write_text("projects:\n")
    copied = []
    monkeypatch.setattr(installer, "REGISTRY", str(registry))
    monkeypatch.setattr(installer, "STATE_DIR", str(tmp_path / "root"))
    monkeypatch.setattr(installer.shutil, "copy2", lambda *a: copied.append(a))
    assert installer.main() == 2
    assert not copied and not (tmp_path / "root").exists()
    assert "NOT DEPLOYED" in capsys.readouterr().err


# ── the guard ────────────────────────────────────────────────────────────────

def test_guard_reads_the_hive_address_from_the_local_values():
    guard = load("fleet_guard_lc", os.path.join(SCRIPTS, "fleet_guard.py"))
    assert guard.HIVE_HEALTH == "http://hive.test:1111/health"
    assert guard.HIVE_SEND == "http://hive.test:1111/send-message"


def test_guard_without_the_address_reports_it_and_does_not_revive(unreadable, capsys):
    guard = load("fleet_guard_lc_missing", os.path.join(SCRIPTS, "fleet_guard.py"))
    assert guard.HIVE_HEALTH is None and "COULD NOT READ" in guard.HIVE_URL_ERROR
    assert guard.hive_broker_up() is None
    calls = []
    guard.sh = lambda cmd, timeout=10: (calls.append(cmd), ("", 0))[1]
    status = guard.loop_status()
    # "down" is the only status the guard revives, and anything but "up" is
    # still reported: so not checked must be neither.
    assert status[guard.HIVE_BROKER] == "no-config"
    assert guard.hive_alert("t", "m") == "no-config"
    assert not any("curl" in c for c in calls), "nothing may be sent to a guessed address"
    out = capsys.readouterr().out
    assert "NOT CHECKED" in out and "NOT SENT" in out


# ── the budget watch ─────────────────────────────────────────────────────────

def load_budget_watch(name, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["fleet_budget_watch.py"])
    return load(name, os.path.join(SCRIPTS, "fleet_budget_watch.py"))


def test_budget_watch_reads_the_hive_address(monkeypatch):
    bw = load_budget_watch("budget_watch_lc", monkeypatch)
    assert bw.HIVE == "http://hive.test:1111/send-message"
    assert bw.HIVE_PEERS == "http://hive.test:1111/list-peers"


def test_budget_watch_without_the_address_says_so(unreadable, monkeypatch, capsys):
    bw = load_budget_watch("budget_watch_lc_missing", monkeypatch)
    ran = []
    monkeypatch.setattr(bw.subprocess, "run", lambda *a, **k: ran.append(a))
    assert bw.send_hold("abc", "text") is False
    assert bw.peer_stable_ids() == {}
    assert not ran, "nothing may be sent to a guessed address"
    err = capsys.readouterr().err
    assert "hold message NOT DONE" in err and "peer lookup NOT DONE" in err
    assert "COULD NOT READ" in err


# ── the health checker ───────────────────────────────────────────────────────

def test_subscription_check_says_when_it_has_no_hive_address(tmp_path):
    hc = load("fleet_healthcheck_lc", os.path.join(SCRIPTS, "fleet_healthcheck.py"))
    db = tmp_path / "subs.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE subscriptions (peer_stable_id TEXT, project_slug TEXT)")
    con.execute("INSERT INTO subscriptions VALUES ('known1', 'proj')")
    con.commit()
    con.close()
    ok, detail = hc.check_subscription_targets({
        "name": "subs", "db": str(db), "table": "subscriptions",
        "column": "peer_stable_id", "label_column": "project_slug",
        "known_ids": ["known1"]})
    assert ok is False, "a check that could not look is never green"
    assert detail.startswith("subs: COULD NOT LOOK")
    assert "hive_url" in detail and "undeliverable" not in detail


# ── the permission-wait hook ─────────────────────────────────────────────────

@pytest.fixture
def hook(tmp_path, monkeypatch):
    lead = tmp_path / "lead"
    lead.mkdir()
    monkeypatch.setenv("TEAM_LEAD_DIR", str(lead))
    monkeypatch.delenv("CLAUDE_HIVE_PORT", raising=False)
    monkeypatch.delenv("CLAUDE_PEERS_PORT", raising=False)
    mod = load("pwa_lc", HOOK)
    mod.lead = lead
    return mod


def test_hook_reads_the_address_from_the_registry(hook):
    (hook.lead / "registry.yaml").write_text(REGISTRY_TEXT)
    assert hook.hive_url() == "http://127.0.0.1:8000"


def test_hook_prefers_the_hives_own_port_variable(hook, monkeypatch):
    monkeypatch.setenv("CLAUDE_HIVE_PORT", "8123")
    assert hook.hive_url() == "http://127.0.0.1:8123"


def test_hook_without_an_address_is_no_config_not_a_guess(hook, monkeypatch):
    opened = []
    monkeypatch.setattr(hook.urllib.request, "urlopen",
                        lambda *a, **k: opened.append(a))
    assert hook.deliver("s", "/x", "m") == "no-config"
    (hook.lead / "registry.yaml").write_text("projects:\n  hive_url: x\n")
    assert hook.deliver("s", "/x", "m") == "no-config"
    assert not opened, "nothing may be sent to a guessed address"


# ── respawn ──────────────────────────────────────────────────────────────────

def test_respawn_scopes_discord_state_from_the_local_values(tmp_path):
    respawn = load("respawn_lc", RESPAWN)
    peer = tmp_path / "peer"
    (peer / ".test-discord").mkdir(parents=True)
    assert respawn.discord_state_dir_for(str(peer)) == respawn.no_discord_state_dir()
    (peer / ".test-discord" / "allow.test.json").write_text("{}")
    assert respawn.discord_state_dir_for(str(peer)) == os.path.join(
        os.path.realpath(peer), ".test-discord")


def test_respawn_writes_the_empty_allowlist_it_was_given(tmp_path, monkeypatch):
    respawn = load("respawn_lc_empty", RESPAWN)
    values = dict(respawn.local_values(), no_discord_state_dir=str(tmp_path / "none"))
    monkeypatch.setattr(respawn, "_LOCAL_VALUES", values)
    respawn.ensure_no_discord_state()
    assert (tmp_path / "none" / "allow.test.json").read_text() == '{"test": []}\n'


def test_respawn_stops_before_doing_anything_when_values_are_missing(
        unreadable, monkeypatch):
    respawn = load("respawn_lc_missing", RESPAWN)
    monkeypatch.setattr(sys, "argv", ["respawn.py", "--mode", "all", "--execute"])
    ran = []
    monkeypatch.setattr(respawn.subprocess, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(SystemExit) as e:
        respawn.main()
    assert "COULD NOT READ" in str(e.value.code)
    assert "nothing killed, nothing spawned" in str(e.value.code)
    assert not ran


# ── added after review ───────────────────────────────────────────────────────

def test_a_comment_after_a_quoted_value_does_not_keep_the_quotes(lc):
    values = lc.parse_local(REGISTRY_TEXT)
    assert values["notion_tunnel_host"] == "notion.example.com"
    assert values["no_discord_state_dir"] == '{"a": "#x"}'
    assert values["board_tunnel_host"] == "board.example.com"
    assert values["discord_empty_allowlist"] == '{"a": [], "b": "#not-a-comment"}'


def test_a_skipped_check_does_not_need_its_keys(installer):
    checks = [{"type": "launchd", "label": "{local:launchd_notion_receiver}", "skip": "retired"},
              {"type": "port", "port": "{port:notion_receiver_url}", "skip": "retired"},
              {"type": "http", "url": "{local:hive_url}/health"}]
    out = installer.fill_local(checks, {"hive_url": "http://127.0.0.1:8000"})
    assert out[2]["url"] == "http://127.0.0.1:8000/health"
    assert out[0]["skip"] == "retired" and "{local" not in json.dumps(out)
    # the same key on a check that runs is still required
    with pytest.raises(installer.local_config.LocalConfigError, match="launchd_notion_receiver"):
        installer.fill_local([{"type": "launchd", "label": "{local:launchd_notion_receiver}"}], {})


def test_install_writes_the_reader_and_values_before_the_guard(
        installer, lc, tmp_path, monkeypatch):
    """The guard runs every few minutes and imports the reader, which reads the
    values file. So when the new guard lands, both must already be in place."""
    root = tmp_path / "root"
    registry = tmp_path / "registry.yaml"
    with open(EXAMPLE) as f:
        registry.write_text(f.read())
    dests = {name: str(root / os.path.basename(getattr(installer, name)))
             for name in ("DEST", "GUARD_DEST", "ROTATE_DEST", "CONFIG",
                          "LOCAL_CONFIG_DEST", "LOCAL_VALUES_DEST")}
    for name, path in dests.items():
        monkeypatch.setattr(installer, name, path)
    monkeypatch.setattr(installer, "REGISTRY", str(registry))
    monkeypatch.setattr(installer, "STATE_DIR", str(root))
    monkeypatch.setattr(installer, "FALLBACK_ROOT", str(root))
    monkeypatch.setattr(installer, "OVERLAY", str(tmp_path / "no-overlay.json"))
    for name in ("registry_sessions", "plugin_version_checks", "registry_stable_ids"):
        monkeypatch.setattr(installer, name, lambda: [])
    monkeypatch.setattr(sys, "argv", ["install_healthcheck.py", "--no-agent"])

    seen = {}
    real_copy = installer.shutil.copy2

    def copy(src, dst):
        if dst == dests["GUARD_DEST"]:
            seen["reader"] = os.path.exists(dests["LOCAL_CONFIG_DEST"])
            try:
                seen["hive_url"] = lc.get("hive_url", path=dests["LOCAL_VALUES_DEST"])
            except lc.LocalConfigError as e:
                seen["hive_url"] = str(e)
        return real_copy(src, dst)

    monkeypatch.setattr(installer.shutil, "copy2", copy)
    assert installer.main() == 0
    assert seen == {"reader": True, "hive_url": "http://127.0.0.1:8000"}
    assert os.path.exists(dests["GUARD_DEST"])


def run_guard_main(guard, monkeypatch, loops, state):
    """fleet_guard.main() with every outside call stubbed. Returns the titles
    it notified under and the state it saved."""
    m = {"swap_gb": 0.5, "free_pct": 60, "load_per_core": 0.4,
         "orphan_workers": 0, "anon_gb": 5.0, "boot_free_gb": 90.0,
         "healthcheck_age_min": 5.0, "pagesize": 16384, "epoch": 1.0,
         "pageins": 0, "pageouts": 0, "compressed_gb": 1.0,
         "orphan_worker_gb": 0.0, "claude_sessions": 2, "claude_gb": 1.0}
    titles, saved = [], []
    monkeypatch.setattr(guard.sys, "argv", ["fleet_guard.py"])
    monkeypatch.setattr(guard, "read_metrics", lambda: m)
    monkeypatch.setattr(guard, "load_state", lambda: state)
    monkeypatch.setattr(guard, "read_history", lambda: [])
    monkeypatch.setattr(guard, "read_process_table", lambda: [])
    monkeypatch.setattr(guard, "loop_status", lambda: dict(loops))
    monkeypatch.setattr(guard, "reap_orphaned_chromes", lambda **kw: ([], []))
    monkeypatch.setattr(guard, "top_holders", lambda: "holders")
    monkeypatch.setattr(guard, "notify", lambda title, msg: titles.append(title) or True)
    monkeypatch.setattr(guard, "hive_alert", lambda title, msg: "no-config")
    monkeypatch.setattr(guard, "save_state", saved.append)
    monkeypatch.setattr(guard, "record_history", lambda s: None)
    assert guard.main() == 0
    return titles, saved[0]


def test_guard_reports_not_checked_under_its_own_banner(unreadable, monkeypatch):
    guard = load("fleet_guard_lc_banner", os.path.join(SCRIPTS, "fleet_guard.py"))
    loops = {guard.HIVE_BROKER: "no-config", "some-loop": "up"}
    titles, saved = run_guard_main(guard, monkeypatch, loops, {})
    # not counted as a loop that is down and failed to restart
    assert "Monitor loop not checked" in titles
    assert "Monitor loop down" not in titles and "Monitor loop restarted" not in titles
    assert saved["loops_down"] == []
    assert saved["loops_not_checked"] == [guard.HIVE_BROKER]
    # said once, not on every run while it stays unread
    titles, _ = run_guard_main(guard, monkeypatch, loops, saved)
    assert "Monitor loop not checked" not in titles


def test_a_revive_that_cannot_probe_the_broker_does_not_claim_it_came_up(
        unreadable, monkeypatch):
    guard = load("fleet_guard_lc_revive", os.path.join(SCRIPTS, "fleet_guard.py"))
    guard.sh = lambda cmd, timeout=10: ("", 0)
    monkeypatch.setattr(guard.time, "sleep", lambda s: None)
    ok, detail = guard.revive(guard.HIVE_BROKER, "true")
    assert ok is False and "/health" in detail


# ── a value that is not there, and a file that cannot be parsed ──────────────

@pytest.mark.parametrize("line", [
    "  hive_url:   # fill me in",
    "  hive_url: #fill me in",
    "  hive_url: #",
])
def test_a_key_with_only_a_comment_is_not_set(lc, tmp_path, line):
    p = tmp_path / "registry.yaml"
    p.write_text(f"local:\n{line}\n  board_tunnel_host: board.example.com\n")
    assert lc.load(str(p)) == {"board_tunnel_host": "board.example.com"}
    with pytest.raises(lc.LocalConfigError, match="does not set hive_url"):
        lc.get("hive_url", path=str(p))


def test_a_quoted_value_may_start_with_a_hash(lc):
    assert lc.parse_local("local:\n  dev_channel_flag: '#x'\n") == {
        "dev_channel_flag": "#x"}


def test_hook_reads_a_key_with_only_a_comment_as_not_set(hook, monkeypatch):
    opened = []
    monkeypatch.setattr(hook.urllib.request, "urlopen",
                        lambda *a, **k: opened.append(a))
    for line in ("  hive_url:   # fill me in", "  hive_url: #fill"):
        (hook.lead / "registry.yaml").write_text(f"local:\n{line}\n")
        with pytest.raises(hook.NoHiveAddress):
            hook.hive_url()
        assert hook.deliver("s", "/x", "m") == "no-config"
    assert not opened, "nothing may be sent to a guessed address"


def test_a_leading_byte_order_mark_is_ignored(lc, tmp_path):
    p = tmp_path / "registry.yaml"
    p.write_bytes(b"\xef\xbb\xbflocal:\n  hive_url: http://127.0.0.1:8000\n")
    assert lc.get("hive_url", path=str(p)) == "http://127.0.0.1:8000"
    j = tmp_path / "local-config.json"
    j.write_bytes(b"\xef\xbb\xbf" + json.dumps({"hive_url": "http://127.0.0.1:8000"}).encode())
    assert lc.get("hive_url", path=str(j)) == "http://127.0.0.1:8000"


@pytest.mark.parametrize("value", [
    '"http://127.0.0.1:8000',
    "'http://127.0.0.1:8000",
    '"http://127.0.0.1:8000\'',
    '"http://127.0.0.1:8000" trailing',
    '"',
])
def test_an_unbalanced_quote_is_could_not_read_not_a_value(lc, tmp_path, value):
    p = tmp_path / "registry.yaml"
    p.write_text(f"local:\n  board_tunnel_host: board.example.com\n  hive_url: {value}\n")
    with pytest.raises(lc.LocalConfigError, match="COULD NOT READ.*hive_url.*quote"):
        lc.load(str(p))
    # also for a caller that asked only for another key: the file is unreadable
    with pytest.raises(lc.LocalConfigError, match="quote"):
        lc.get("board_tunnel_host", path=str(p))


def test_backslashes_in_double_quotes_are_kept_as_written(lc):
    assert lc.parse_local('local:\n  notion_receiver_log: "a\\nb"\n') == {
        "notion_receiver_log": "a\\nb"}
