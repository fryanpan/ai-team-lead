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
def installer(tmp_path, monkeypatch):
    """The installer, as if it ran from a main checkout. It refuses to run
    from a linked worktree, and the tests may themselves run inside one."""
    mod = load("install_healthcheck_lc", os.path.join(SCRIPTS, "install_healthcheck.py"))
    main_checkout = tmp_path / "main-checkout"
    (main_checkout / ".git").mkdir(parents=True)
    monkeypatch.setattr(mod, "REPO", str(main_checkout))
    return mod


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


# ── launchd labels and the checkout's path ───────────────────────────────────

LABEL_KEYS = ("launchd_healthcheck", "launchd_guard", "launchd_log_rotate",
              "launchd_board_server", "launchd_board_server_former")


def example_values(lc):
    with open(EXAMPLE) as f:
        return lc.parse_local(f.read())


def example_text():
    with open(EXAMPLE) as f:
        return f.read()


def test_every_launchd_label_in_the_checks_comes_from_the_local_values(installer, lc):
    """No label is written in the installer: change a value and the check
    that carries it changes with it."""
    values = dict(example_values(lc), launchd_guard="another.guard-label")
    filled = installer.fill_local(installer.BASE_CHECKS, values)
    labels = [c["label"] for c in filled if c["type"] in ("launchd", "launchd_ran")]
    flat = [x for l in labels for x in ([l] if isinstance(l, str) else l)]
    known = {v for k, v in values.items() if k.startswith("launchd_")}
    assert flat and set(flat) <= known
    assert "another.guard-label" in flat
    assert ["com.example.board-server", "com.example.board-server-old"] in labels
    for key in LABEL_KEYS:
        assert values[key] in flat, key


def install_into(installer, tmp_path, monkeypatch, registry_text, argv=(),
                 overlay=None):
    """Run the installer's main() against a temp deploy root and LaunchAgents
    folder, with launchctl stubbed. Returns (exit code, launchctl calls, root)."""
    root = tmp_path / "root"
    agents = tmp_path / "LaunchAgents"
    agents.mkdir(exist_ok=True)
    registry = tmp_path / "registry.yaml"
    registry.write_text(registry_text)
    for name in ("DEST", "GUARD_DEST", "ROTATE_DEST", "CONFIG",
                 "LOCAL_CONFIG_DEST", "LOCAL_VALUES_DEST"):
        monkeypatch.setattr(
            installer, name, str(root / os.path.basename(getattr(installer, name))))
    monkeypatch.setattr(installer, "REGISTRY", str(registry))
    monkeypatch.setattr(installer, "STATE_DIR", str(root))
    monkeypatch.setattr(installer, "FALLBACK_ROOT", str(root))
    monkeypatch.setattr(installer, "LAUNCH_AGENTS", str(agents))
    overlay_path = tmp_path / "overlay.json"
    if overlay is not None:
        overlay_path.write_text(json.dumps({"checks": overlay}))
    monkeypatch.setattr(installer, "OVERLAY", str(overlay_path))
    for name in ("registry_sessions", "plugin_version_checks", "registry_stable_ids"):
        monkeypatch.setattr(installer, name, lambda: [])
    monkeypatch.setattr(sys, "argv", ["install_healthcheck.py", *argv])
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    def run(cmd, **kw):
        calls.append(cmd)
        return Done()

    monkeypatch.setattr(installer.subprocess, "run", run)
    return installer.main(), calls, root


def test_install_names_its_three_jobs_from_the_local_values(
        installer, tmp_path, monkeypatch):
    import plistlib
    rc, calls, root = install_into(installer, tmp_path, monkeypatch, example_text())
    assert rc == 0
    agents = tmp_path / "LaunchAgents"
    want = {"com.example.fleet-healthcheck": "fleet_healthcheck.py",
            "com.example.fleet-guard": "fleet_guard.py",
            "com.example.fleet-log-rotate": "rotate_fleet_logs.py"}
    assert sorted(p.name for p in agents.iterdir()) == sorted(
        f"{label}.plist" for label in want)
    uid = os.getuid()
    for label, program in want.items():
        with open(agents / f"{label}.plist", "rb") as f:
            plist = plistlib.load(f)
        assert plist["Label"] == label
        assert plist["ProgramArguments"][1] == str(root / program)
        assert ["launchctl", "bootout", f"gui/{uid}/{label}"] in calls
        assert ["launchctl", "bootstrap", f"gui/{uid}",
                str(agents / f"{label}.plist")] in calls
    # a second run finds its own three jobs under the configured labels: fine
    rc, _, _ = install_into(installer, tmp_path, monkeypatch, example_text())
    assert rc == 0


@pytest.mark.parametrize("key", LABEL_KEYS)
def test_install_deploys_nothing_when_a_label_is_not_set(
        installer, tmp_path, monkeypatch, capsys, key):
    text = "".join(l + "\n" for l in example_text().splitlines()
                   if not l.startswith(f"  {key}:"))
    rc, calls, root = install_into(installer, tmp_path, monkeypatch, text)
    assert rc == 2 and not calls and not root.exists()
    assert not list((tmp_path / "LaunchAgents").iterdir())
    err = capsys.readouterr().err
    assert "NOT DEPLOYED" in err and key in err


def test_install_writes_the_checkouts_path_for_the_deployed_scripts(
        installer, lc, tmp_path, monkeypatch):
    rc, _, root = install_into(installer, tmp_path, monkeypatch, example_text(),
                               ["--no-agent"])
    assert rc == 0
    deployed = json.loads((root / "local-config.json").read_text())
    assert deployed == {"hive_url": "http://127.0.0.1:8000",
                        "repo_root": os.path.realpath(ROOT)}
    # and the deployed reader hands it to a deployed script
    reader = load("deployed_local_config", str(root / "local_config.py"))
    monkeypatch.delenv("TEAM_LEAD_LOCAL_CONFIG")
    assert reader.repo_root() == os.path.realpath(ROOT)
    assert reader.checkouts_dir() == os.path.dirname(os.path.realpath(ROOT))


def test_install_refuses_to_run_from_a_linked_worktree(
        installer, tmp_path, monkeypatch, capsys):
    """Its path would go into the deploy root, and the guard would restart the
    loops from a folder that is removed when the branch is done."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /somewhere/.git/worktrees/w\n")
    monkeypatch.setattr(installer, "REPO", str(worktree))
    rc, calls, root = install_into(installer, tmp_path, monkeypatch, example_text())
    assert rc == 2 and not calls and not root.exists()
    assert not list((tmp_path / "LaunchAgents").iterdir())
    err = capsys.readouterr().err
    assert "NOT DEPLOYED" in err and "linked git worktree" in err
    assert "main checkout" in err
    # a checkout with no .git at all is not a linked worktree
    assert installer.linked_worktree(str(tmp_path)) is False
    assert installer.linked_worktree(str(worktree)) is True


@pytest.mark.parametrize("program, key", [
    ("fleet_healthcheck.py", "launchd_healthcheck"),
    ("fleet_guard.py", "launchd_guard"),
    ("rotate_fleet_logs.py", "launchd_log_rotate"),
])
def test_install_stops_when_a_job_runs_its_program_under_another_label(
        installer, tmp_path, monkeypatch, capsys, program, key):
    import plistlib
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    stale = agents / "old.label.plist"
    with open(stale, "wb") as f:
        plistlib.dump({"Label": "old.label", "ProgramArguments": [
            "/usr/bin/python3", str(tmp_path / "root" / program)]}, f)
    # another program's job, and a file that is not a plist, are left alone
    with open(agents / "someone.else.plist", "wb") as f:
        plistlib.dump({"Label": "someone.else",
                       "ProgramArguments": ["/usr/bin/true"]}, f)
    (agents / "broken.plist").write_text("not a plist")
    before = sorted(p.name for p in agents.iterdir())
    rc, calls, root = install_into(installer, tmp_path, monkeypatch, example_text())
    assert rc == 2 and not calls and not root.exists()
    assert sorted(p.name for p in agents.iterdir()) == before
    captured = capsys.readouterr()
    assert "NOT DEPLOYED" in captured.err and "Nothing was changed" in captured.err
    assert str(stale) in captured.err and "`old.label`" in captured.err
    assert f"`{key}`" in captured.err
    assert "could not read" in captured.out and "broken.plist" in captured.out
    # --no-agent creates no job, so it is not stopped by one
    rc, calls, root = install_into(installer, tmp_path, monkeypatch, example_text(),
                                   ["--no-agent"])
    assert rc == 0 and not calls


def test_an_overlay_can_replace_a_check_whose_label_is_a_list(
        installer, tmp_path, monkeypatch):
    both = ["com.example.board-server", "com.example.board-server-old"]
    overlay = [
        {"type": "launchd", "label": both, "skip": "replaced by the overlay"},
        {"type": "port", "port": 8787, "name": "live-feedback", "skip": "overlay"},
        {"type": "http", "name": "only in the overlay", "url": "http://127.0.0.1:1/"},
    ]
    rc, _, root = install_into(installer, tmp_path, monkeypatch, example_text(),
                               ["--no-agent"], overlay=overlay)
    assert rc == 0
    checks = json.loads((root / "healthcheck-config.json").read_text())["checks"]
    board = [c for c in checks if c.get("label") == both]
    assert board == [overlay[0]]
    assert [c for c in checks if c.get("name") == "live-feedback"
            and c["type"] == "port"] == [overlay[1]]
    assert overlay[2] in checks
    # every other check is still there, matched the way the merge matches
    idents = [installer.check_ident(c) for c in checks]
    assert len(idents) == len(set(idents))
    assert "sentry subscriptions" in idents and "com.example.fleet-guard" in idents
    assert installer.check_ident({"label": both}) == ",".join(both)
    assert installer.check_ident({"type": "port", "port": 1}) == "port:1"


def test_the_checker_version_check_reads_the_source_from_this_checkout(installer):
    spec = next(c for c in installer.BASE_CHECKS if c["type"] == "self_version")
    assert spec["source"] == os.path.join(
        os.path.realpath(ROOT), "scripts", "fleet_healthcheck.py")
    assert os.path.exists(spec["source"])


def test_repo_root_in_a_checkout_is_the_folder_above_the_scripts(
        lc, tmp_path, monkeypatch):
    registry = tmp_path / "registry.yaml"
    registry.write_text("local:\n  hive_url: http://127.0.0.1:8000\n")
    monkeypatch.setenv("TEAM_LEAD_LOCAL_CONFIG", str(registry))
    assert lc.repo_root() == os.path.realpath(ROOT)
    monkeypatch.delenv("TEAM_LEAD_LOCAL_CONFIG")
    assert lc.repo_root() == os.path.realpath(ROOT)
    assert lc.checkouts_dir() == os.path.dirname(os.path.realpath(ROOT))


def test_a_deployed_copy_without_the_path_says_so(lc, tmp_path, monkeypatch):
    values = tmp_path / "local-config.json"
    values.write_text(json.dumps({"hive_url": "http://127.0.0.1:8000"}))
    monkeypatch.setenv("TEAM_LEAD_LOCAL_CONFIG", str(values))
    with pytest.raises(lc.LocalConfigError, match="does not set repo_root"):
        lc.repo_root()
    assert lc.try_read(lc.repo_root)[0] is None
    assert "does not set repo_root" in lc.try_read(lc.checkouts_dir)[1]
    values.write_text(json.dumps({"repo_root": "/Volumes/Disk/Users/someone/dev/x"}))
    assert lc.repo_root() == "/Volumes/Disk/Users/someone/dev/x"
    assert lc.try_read(lc.checkouts_dir) == ("/Volumes/Disk/Users/someone/dev", "")


def load_guard(name, tmp_path, monkeypatch, repo_root, home):
    values = tmp_path / "local-config.json"
    values.write_text(json.dumps({"hive_url": "http://hive.test:1111",
                                  "repo_root": repo_root}))
    monkeypatch.setenv("TEAM_LEAD_LOCAL_CONFIG", str(values))
    monkeypatch.setenv("HOME", home)
    monkeypatch.delenv("TEAM_LEAD_DIR", raising=False)
    return load(name, os.path.join(SCRIPTS, "fleet_guard.py"))


def test_guard_builds_each_loop_command_from_the_path_and_the_home_folder(
        tmp_path, monkeypatch):
    """The commands are handed to tmux as written and show in the process
    table, so they are compared whole. With the checkout at
    <volume>/Users/<user>/dev/<repo> and the home folder at /Users/<user>,
    these are the three strings this file used to spell out: a path with
    nothing in it to quote is not changed by the quoting."""
    guard = load_guard("fleet_guard_loops", tmp_path, monkeypatch,
                       "/Volumes/Disk/Users/someone/dev/team-lead-checkout",
                       "/Users/someone")
    assert guard.LOOPS == {
        "fleet-budget": "/Volumes/Disk/Users/someone/dev/team-lead-checkout"
                        "/scripts/fleet_budget_loop.sh",
        "fleet-monitor": "/Volumes/Disk/Users/someone/dev/team-lead-checkout"
                         "/scripts/fleet_monitor_loop.sh",
        "hive-broker": "/Users/someone/.bun/bin/bun "
                       "/Users/someone/claude-hive-mcp/broker.ts",
    }
    assert guard.CHECKOUTS_DIR == "/Volumes/Disk/Users/someone/dev"
    for name, script in guard.LOOP_SCRIPTS.items():
        assert os.path.exists(os.path.join(SCRIPTS, script)), name


def test_guard_selftest_lists_the_folder_that_holds_the_checkout(
        tmp_path, monkeypatch):
    guard = load_guard("fleet_guard_selftest", tmp_path, monkeypatch,
                       "/Volumes/Disk/Users/someone/dev/team-lead-checkout",
                       "/Users/someone")
    revived, ran = [], []
    monkeypatch.setattr(guard, "revive",
                        lambda name, cmd: (revived.append(cmd), (True, "READABLE"))[1])
    monkeypatch.setattr(guard, "sh", lambda cmd, timeout=10: (ran.append(cmd), ("", 0))[1])
    monkeypatch.setattr(guard, "notify", lambda *a: True)
    assert guard.selftest() == 0
    assert revived == [
        "/bin/bash -c 'ls /Volumes/Disk/Users/someone/dev "
        ">/dev/null 2>&1 && echo SECONDARY-VOLUME-READABLE "
        "|| echo SECONDARY-VOLUME-DENIED; sleep 30'"]
    guard.probe()
    assert "/bin/ls /Volumes/Disk/Users/someone/dev >/dev/null" in ran


def as_tmux_runs_it(command, tmp_path):
    """What ends up running when revive() hands `command` to tmux.

    revive() writes `tmux new-session -d -s <name> <command>` as a shell line.
    tmux runs several arguments as they are, and gives a single argument to a
    shell of its own. A stand-in for tmux does the same here and records the
    final argv. Returns that argv.
    """
    import subprocess
    record = tmp_path / "argv.json"
    printer = tmp_path / "print_argv.py"
    printer.write_text(
        "import json, sys\n"
        f"json.dump(sys.argv[1:], open({str(record)!r}, 'w'))\n")
    stand_in = tmp_path / "tmux_stand_in.py"
    stand_in.write_text(
        "import subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "if len(args) == 1:\n"
        "    sys.exit(subprocess.run(['/bin/sh', '-c', args[0]]).returncode)\n"
        "sys.exit(subprocess.run(args).returncode)\n")
    # the program at the head of the command is replaced by one that records
    # its own argv; everything after it goes through both shells untouched
    assert subprocess.run(
        f"{sys.executable} {stand_in} {command}", shell=True,
        env=dict(os.environ, RECORD=str(record))).returncode in (0, 126, 127)
    return record


def test_a_path_with_a_space_reaches_tmux_and_its_shell_whole(tmp_path, monkeypatch):
    spaced = tmp_path / "My Disk" / "dev folder" / "team lead"
    scripts = spaced / "scripts"
    scripts.mkdir(parents=True)
    home = tmp_path / "home dir"
    (home / ".bun" / "bin").mkdir(parents=True)
    (home / "claude-hive-mcp").mkdir()
    guard = load_guard("fleet_guard_spaces", tmp_path, monkeypatch,
                       str(spaced), str(home))
    import shlex
    import subprocess
    # a loop script: one argument for tmux, which its shell reads as one path
    for name, script in guard.LOOP_SCRIPTS.items():
        outer = shlex.split(guard.LOOPS[name])
        assert len(outer) == 1, "tmux must get the command as one argument"
        assert shlex.split(outer[0]) == [str(scripts / script)]
        marker = tmp_path / f"{name}.ran"
        (scripts / script).write_text(f"#!/bin/sh\necho ran > {shlex.quote(str(marker))}\n")
        (scripts / script).chmod(0o755)
        # the shell line revive() builds, with a stand-in that does what tmux
        # does with one argument: give it to a shell
        line = f"/bin/sh -c 'exec /bin/sh -c \"$1\"' tmux {guard.LOOPS[name]}"
        assert subprocess.run(line, shell=True).returncode == 0
        assert marker.read_text() == "ran\n", name
    # the broker: two arguments, which tmux runs without a shell
    assert shlex.split(guard.LOOPS[guard.HIVE_BROKER]) == [
        str(home / ".bun" / "bin" / "bun"),
        str(home / "claude-hive-mcp" / "broker.ts")]
    # selftest and probe: the folder is one argument to ls
    revived, ran = [], []
    monkeypatch.setattr(guard, "revive",
                        lambda name, cmd: (revived.append(cmd), (True, "READABLE"))[1])
    monkeypatch.setattr(guard, "sh", lambda cmd, timeout=10: (ran.append(cmd), ("", 0))[1])
    monkeypatch.setattr(guard, "notify", lambda *a: True)
    assert guard.selftest() == 0
    argv = shlex.split(revived[0])
    assert argv[:2] == ["/bin/bash", "-c"] and len(argv) == 3
    assert shlex.split(argv[2])[:2] == ["ls", str(spaced.parent)]
    out = subprocess.run(["/bin/bash", "-c", argv[2].replace("sleep 30", "true")],
                         capture_output=True, text=True).stdout
    assert out.strip() == "SECONDARY-VOLUME-READABLE"
    guard.probe()
    ls = next(c for c in ran if c.startswith("/bin/ls "))
    assert shlex.split(ls)[:2] == ["/bin/ls", str(spaced.parent)]


@pytest.fixture
def deployed_unreadable(monkeypatch, tmp_path):
    """A deployed copy whose values file is not there. Unlike a checkout, it
    has no way to work the checkout's path out."""
    monkeypatch.setenv("TEAM_LEAD_LOCAL_CONFIG", str(tmp_path / "local-config.json"))
    monkeypatch.delenv("TEAM_LEAD_DIR", raising=False)


def test_guard_without_the_path_still_checks_loops_and_says_why_none_restart(
        deployed_unreadable, monkeypatch, capsys):
    guard = load("fleet_guard_no_path", os.path.join(SCRIPTS, "fleet_guard.py"))
    assert guard.REPO_ROOT is None and "COULD NOT READ" in guard.REPO_ROOT_ERROR
    assert guard.LOOPS["fleet-budget"] is None and guard.LOOPS["fleet-monitor"] is None
    calls = []
    monkeypatch.setattr(guard, "TMUX", "/bin/sh")       # any file that exists
    monkeypatch.setattr(guard, "notify", lambda *a: True)

    def sh(cmd, timeout=10):
        calls.append(cmd)
        return ("fleet-budget: 1 windows", 0) if cmd.endswith(" ls") else ("", 0)

    monkeypatch.setattr(guard, "sh", sh)
    status = guard.loop_status()
    # still looked for, and still reported down when it is not there
    assert status["fleet-budget"] == "up" and status["fleet-monitor"] == "down"
    calls.clear()
    ok, detail = guard.revive("fleet-monitor", guard.LOOPS["fleet-monitor"])
    assert ok is False and "COULD NOT READ" in detail
    assert "install_healthcheck.py from the main checkout" in detail
    assert not calls, "no session may be killed or started without a command"
    assert guard.selftest() == 1 and not calls
    guard.probe()
    out = capsys.readouterr().out
    assert "selftest NOT RUN" in out and "secondary volume NOT CHECKED" in out


def test_the_loop_down_alert_says_why_and_what_to_do(deployed_unreadable, monkeypatch):
    guard = load("fleet_guard_alert_text", os.path.join(SCRIPTS, "fleet_guard.py"))
    messages = {}
    calls = []
    monkeypatch.setattr(guard, "sh", lambda cmd, timeout=10: (calls.append(cmd), ("", 0))[1])
    loops = {"fleet-monitor": "down", "fleet-budget": "up"}
    titles, saved = run_guard_main(guard, monkeypatch, loops, {})
    # run_guard_main replaced notify; put a recording one in and run again on
    # a fresh state so the loop is newly down once more
    monkeypatch.setattr(guard, "notify",
                        lambda title, msg: messages.setdefault(title, msg) or True)
    assert guard.main() == 0
    assert "Monitor loop down" in titles and "Monitor loop restarted" not in titles
    text = messages["Monitor loop down"]
    assert "fleet-monitor" in text and "could not be restarted" in text
    assert "the checkout's path could not be read" in text and "COULD NOT READ" in text
    assert "Rerun scripts/install_healthcheck.py from the main checkout" in text
    assert saved["loops"]["fleet-monitor"] == "revive-failed"
    assert not any("new-session" in c or "kill-session" in c for c in calls)


def test_guard_finds_the_team_lead_session_by_the_checkouts_path(
        tmp_path, monkeypatch):
    checkout = tmp_path / "physical" / "team-lead-checkout"
    checkout.mkdir(parents=True)
    link = tmp_path / "linked"
    link.symlink_to(tmp_path / "physical")
    guard = load_guard("fleet_guard_stable_id", tmp_path, monkeypatch,
                       os.path.realpath(checkout), str(tmp_path))
    peers = [
        {"stable_id": "elsewhere", "cwd": str(tmp_path)},
        # the session's cwd may be spelled through a link: same folder
        {"stable_id": "abc123", "cwd": str(link / "team-lead-checkout")},
        {"stable_id": "abc123-9f", "cwd": os.path.realpath(checkout)},
    ]
    monkeypatch.setattr(guard, "sh", lambda cmd, timeout=10: (json.dumps(peers), 0))
    assert guard.team_lead_dir() == os.path.realpath(checkout)
    assert guard.team_lead_stable_id() == "abc123"
    # TEAM_LEAD_DIR still names another folder when it is set
    monkeypatch.setenv("TEAM_LEAD_DIR", str(tmp_path))
    assert guard.team_lead_stable_id() == "elsewhere"


def test_guard_without_the_path_does_not_guess_where_the_team_lead_is(
        tmp_path, monkeypatch, capsys):
    values = tmp_path / "local-config.json"
    values.write_text(json.dumps({"hive_url": "http://hive.test:1111"}))
    monkeypatch.setenv("TEAM_LEAD_LOCAL_CONFIG", str(values))
    monkeypatch.delenv("TEAM_LEAD_DIR", raising=False)
    guard = load("fleet_guard_no_lead_dir", os.path.join(SCRIPTS, "fleet_guard.py"))
    calls = []
    monkeypatch.setattr(guard, "sh", lambda cmd, timeout=10: (calls.append(cmd), ("[]", 0))[1])
    assert guard.HIVE_SEND and guard.team_lead_dir() is None
    assert guard.team_lead_stable_id() is None
    assert guard.hive_alert("t", "m") == "no-config"
    assert not calls, "no peer is looked up for a folder that was not read"
    out = capsys.readouterr().out
    assert "HIVE ALERT NOT SENT" in out and "install_healthcheck.py" in out
    with open(os.path.join(SCRIPTS, "fleet_guard.py")) as f:
        assert "~/dev/" not in f.read()


def load_checker(name, tmp_path, monkeypatch, values):
    path = tmp_path / "local-config.json"
    if values is not None:
        path.write_text(json.dumps(values))
    monkeypatch.setenv("TEAM_LEAD_LOCAL_CONFIG", str(path))
    return load(name, os.path.join(SCRIPTS, "fleet_healthcheck.py"))


def test_checker_probes_a_file_in_the_checkout(tmp_path, monkeypatch):
    hc = load_checker("fleet_healthcheck_probe", tmp_path, monkeypatch,
                      {"repo_root": "/Volumes/Disk/Users/someone/dev/x"})
    assert hc.PROBE_DATA_FILE == "/Volumes/Disk/Users/someone/dev/x/README.md"
    seen = []

    class Done:
        returncode, stdout, stderr = 0, "42\n", ""

    monkeypatch.setattr(hc, "BUN_CANDIDATES", [sys.executable])
    monkeypatch.setattr(hc.subprocess, "run",
                        lambda cmd, **kw: (seen.append(cmd), Done())[1])
    assert hc.resolve_bun()[0] == sys.executable
    assert "/Volumes/Disk/Users/someone/dev/x/README.md" in seen[0][2]
    assert hc.bun_works() is True


def test_checker_tells_no_bun_works_from_nothing_probed(tmp_path, monkeypatch):
    """Three results: a bun reads the volume, none can, or nothing was probed."""
    hc = load_checker("fleet_healthcheck_none_works", tmp_path, monkeypatch,
                      {"repo_root": "/Volumes/Disk/Users/someone/dev/x"})

    class Failed:
        returncode, stdout, stderr = 1, "", "EPERM: operation not permitted\n"

    monkeypatch.setattr(hc, "BUN_CANDIDATES", [sys.executable])
    monkeypatch.setattr(hc.subprocess, "run", lambda cmd, **kw: Failed())
    assert hc.bun_works() is False
    assert hc.bun_unavailable() == hc.BUN_UNAVAILABLE
    ok, detail = hc.check_self_version({"name": "checker version", "source": "/x"})
    assert ok is False and hc.BUN_UNAVAILABLE in detail and "COULD NOT LOOK" not in detail


@pytest.fixture
def blind_checker(tmp_path, monkeypatch):
    """The checker as a deployed copy with no values file: it has no file to
    probe with, so it never finds out whether any bun reads the volume."""
    hc = load_checker("fleet_healthcheck_blind", tmp_path, monkeypatch, None)
    ran = []
    monkeypatch.setattr(hc, "BUN_CANDIDATES", [sys.executable])
    real_run = hc.subprocess.run

    def run(cmd, *a, **k):
        if cmd and cmd[0] == sys.executable:
            ran.append(cmd)
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(hc.subprocess, "run", run)
    hc.ran = ran
    return hc


def test_checker_without_the_path_could_not_look(blind_checker):
    hc = blind_checker
    assert hc.PROBE_DATA_FILE is None
    path, reason = hc.resolve_bun()
    assert path is None and not hc.ran
    assert reason.startswith("COULD NOT LOOK") and "COULD NOT READ" in reason
    assert "install_healthcheck.py from the main checkout" in reason
    assert hc.bun_works() is None, "not probed is not the same as no bun works"
    assert hc.bun_unavailable() == reason


@pytest.mark.parametrize("check, spec", [
    ("check_self_version", {"name": "checker version", "source": "/x"}),
    ("check_archive_backlog", {"name": "transcript archive", "live_root": "/a",
                               "archive_root": "/b"}),
    ("check_rate_limit_hits", {"name": "session-limit hits"}),
    ("check_tcc_grant", {"name": "secondary-volume access"}),
])
def test_a_check_that_needs_bun_reports_could_not_look(blind_checker, check, spec):
    hc = blind_checker
    ok, detail = getattr(hc, check)(spec)
    assert ok is False, "a check that could not look is never green"
    assert detail.startswith(spec["name"]) and "COULD NOT LOOK" in detail
    assert hc.BUN_UNAVAILABLE not in detail, "nothing was tried, so nothing failed"
    assert "UNREADABLE" not in detail and not hc.ran


def test_the_trend_log_read_says_could_not_look_when_it_falls_back(
        blind_checker, tmp_path):
    hc = blind_checker
    text, err = hc.read_text_via_bun(str(tmp_path / "absent.md"))
    assert text is None and "COULD NOT LOOK" in err and hc.BUN_UNAVAILABLE not in err
    # without bun it still tries the plain read, and that can succeed
    (tmp_path / "there.md").write_text("x")
    assert hc.read_text_via_bun(str(tmp_path / "there.md")) == ("x", None)


def test_the_grant_record_keeps_its_last_real_observation(blind_checker, monkeypatch):
    """Not probed is not "unreadable". Stored as unreadable, the next run after
    a reboot would report a grant that did not survive it."""
    hc = blind_checker
    prior = {"boot": "{ sec = 1, usec = 0 }", "readable": True, "binary": "/x/bun"}
    monkeypatch.setattr(hc, "prev_status", lambda: {"tcc": dict(prior)})
    monkeypatch.setattr(hc, "_sysctl", lambda name: "{ sec = 2, usec = 0 }")
    ok, detail = hc.check_tcc_grant({"name": "secondary-volume access"})
    assert ok is False and "COULD NOT LOOK" in detail
    assert "DID NOT SURVIVE" not in detail
    assert hc._TCC_RECORD == prior
    # and with no earlier observation there is nothing to keep
    hc._TCC_RECORD.clear()
    monkeypatch.setattr(hc, "prev_status", lambda: {})
    hc.check_tcc_grant({"name": "secondary-volume access"})
    assert "readable" not in hc._TCC_RECORD


def budget_watch_at(name, monkeypatch, tmp_path, home_link=False):
    """The budget watch with the checkouts folder at <tmp>/Disk/Users/someone/dev
    and the home folder at <tmp>/home. With `home_link`, the home folder has a
    link named `dev` to the checkouts folder."""
    holder = tmp_path / "Disk" / "Users" / "someone" / "dev"
    for project in ("team-lead-checkout", "two-word-project", "coach"):
        (holder / project).mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    if home_link:
        (home / "dev").symlink_to(holder)
    values = tmp_path / "local-config.json"
    values.write_text(json.dumps({
        "hive_url": "http://hive.test:1111",
        "repo_root": os.path.realpath(holder / "team-lead-checkout")}))
    monkeypatch.setenv("TEAM_LEAD_LOCAL_CONFIG", str(values))
    monkeypatch.setenv("HOME", str(home))
    bw = load_budget_watch(name, monkeypatch)
    return bw, os.path.realpath(holder), str(home)


def test_budget_watch_names_projects_as_before_for_this_layout(monkeypatch, tmp_path):
    """Every shape of transcript folder name the old rule handled, under a
    checkouts folder laid out as <disk>/Users/<user>/dev with a `dev` link in
    the home folder. The old rule looked for a `dev` segment; the names it gave
    are the expected values here."""
    bw, holder, home = budget_watch_at("budget_watch_layout", monkeypatch, tmp_path, True)
    assert bw.CHECKOUTS_DIR == holder
    enc, enc_home = bw.encoded(holder), bw.encoded(os.path.join(home, "dev"))
    assert bw.CHECKOUT_PREFIXES == [enc + "-", enc_home + "-"]
    cases = {
        # a project, a project with a two-word name, and one with no folder
        f"{enc}-coach": "coach",
        f"{enc}-two-word-project": "two-word-project",
        f"{enc}-gone-project": "gone",
        # a worktree bills to its repo: both ways a worktree folder is spelled
        f"{enc}-two-word-project--claude-worktrees-agent-a1": "two-word-project",
        f"{enc}-two-word-project-worktrees-w": "two-word-project",
        f"{enc}-team-lead-checkout-": "team-lead-checkout",
        # a session started through the link in the home folder
        f"{enc_home}-coach": "coach",
        # a scratch folder named after a checkout
        f"-private-tmp-claude-501-{enc}-team-lead-checkout-0a1b-scratchpad":
            "team-lead-checkout",
        # the checkouts folder itself, and folders that are not under it
        enc: enc,
        "-private-tmp": "-private-tmp",
        "-Users-other-Library-CloudStorage-Notes": "-Users-other-Library-CloudStorage-Notes",
        "-": "-",
    }
    for folder, project in cases.items():
        assert bw.project_of("/projects/" + folder) == project, folder


def test_budget_watch_needs_no_folder_named_dev(monkeypatch, tmp_path):
    holder = tmp_path / "Work Disk" / "code"
    (holder / "team-lead-checkout").mkdir(parents=True)
    (holder / "some-project").mkdir()
    values = tmp_path / "local-config.json"
    values.write_text(json.dumps({"repo_root": os.path.realpath(holder / "team-lead-checkout")}))
    monkeypatch.setenv("TEAM_LEAD_LOCAL_CONFIG", str(values))
    bw = load_budget_watch("budget_watch_no_dev", monkeypatch)
    enc = bw.encoded(os.path.realpath(holder))
    assert bw.project_of(f"/p/{enc}-some-project-worktrees-w") == "some-project"
    # a `dev` segment somewhere else in a name is no longer a marker
    assert bw.project_of("/p/-private-tmp-dev-some-project") == "-private-tmp-dev-some-project"
    with open(os.path.join(SCRIPTS, "fleet_budget_watch.py")) as f:
        source = f.read()
    assert '"dev"' not in source and '".."' not in source


def test_budget_watch_without_the_path_says_projects_are_not_mapped(
        deployed_unreadable, monkeypatch, capsys):
    bw = load_budget_watch("budget_watch_unmapped", monkeypatch)
    assert bw.CHECKOUTS_DIR is None and bw.CHECKOUT_PREFIXES == []
    assert bw.project_of("/p/-Volumes-Disk-Users-someone-dev-coach") == \
        "-Volumes-Disk-Users-someone-dev-coach"
    bw.project_of("/p/-another")
    err = capsys.readouterr().err
    assert err.count("NOT MAPPED") == 1 and "COULD NOT READ" in err


def test_turn_attribution_strips_the_checkouts_folder_from_a_transcript_name(
        tmp_path, monkeypatch):
    values = tmp_path / "local-config.json"
    values.write_text(json.dumps({"repo_root": "/Volumes/Disk/Users/someone/dev/x"}))
    monkeypatch.setenv("TEAM_LEAD_LOCAL_CONFIG", str(values))
    ta = load("turn_attribution_prefix", os.path.join(SCRIPTS, "turn_attribution.py"))
    assert ta.checkouts_prefix() == "-Volumes-Disk-Users-someone-dev-"
    values.write_text("{}")
    monkeypatch.setattr(sys, "argv", ["turn_attribution.py"])
    with pytest.raises(SystemExit) as e:
        ta.main()
    assert "nothing measured" in str(e.value.code) and "repo_root" in str(e.value.code)


def script_head(script, last_line_starts):
    """`script` from its first line through the line that starts with
    `last_line_starts`: the part that works out a path, without what follows."""
    out = []
    with open(os.path.join(ROOT, script)) as f:
        for line in f:
            out.append(line)
            if line.startswith(last_line_starts):
                return "".join(out)
    raise AssertionError(f"{script} has no line starting {last_line_starts!r}")


def run_head(folder, head, show, args=(), env=None):
    """Put `head` in `folder` as a script that then prints $`show`, and run
    it. Returns (exit code, stdout, stderr)."""
    import subprocess
    folder.mkdir(parents=True, exist_ok=True)
    probe = folder / "probe.sh"
    probe.write_text(head + f'echo "${show}"\n')
    r = subprocess.run(["/bin/bash", str(probe), *args], capture_output=True,
                       text=True, env=dict(os.environ, **(env or {})))
    return r.returncode, r.stdout.strip(), r.stderr + r.stdout


@pytest.mark.parametrize("script, program", [
    ("scripts/fleet_budget_loop.sh", "fleet_budget_watch.py"),
    ("scripts/fleet_monitor_loop.sh", "fleet_context_report.py"),
])
def test_a_loop_script_runs_the_main_checkouts_program(script, program, tmp_path):
    assert os.path.exists(os.path.join(SCRIPTS, program))
    head = script_head(script, "SCRIPT=")
    main = tmp_path / "main"
    (main / ".git" / "worktrees" / "w").mkdir(parents=True)
    want = os.path.join(os.path.realpath(main), "scripts", program)
    # in the main checkout, also when started through a link to it
    assert run_head(main / "scripts", head, "SCRIPT")[:2] == (0, want)
    link = tmp_path / "link"
    link.symlink_to(main)
    import subprocess
    r = subprocess.run(["/bin/bash", str(link / "scripts" / "probe.sh")],
                       capture_output=True, text=True)
    assert (r.returncode, r.stdout.strip()) == (0, want)
    # a checkout with no .git at all is its own checkout
    plain = tmp_path / "plain"
    assert run_head(plain / "scripts", head, "SCRIPT")[:2] == (
        0, os.path.join(os.path.realpath(plain), "scripts", program))
    # a copy in a linked worktree still runs the main checkout's program
    worktree = tmp_path / "elsewhere" / "w"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(
        f"gitdir: {os.path.realpath(main)}/.git/worktrees/w\n")
    assert run_head(worktree / "scripts", head, "SCRIPT")[:2] == (0, want)
    # and stops when its .git file does not name a main checkout
    (worktree / ".git").write_text("gitdir: ../somewhere\n")
    rc, out, said = run_head(worktree / "scripts", head, "SCRIPT")
    assert rc == 1 and out == "" and "ABORT" in said


def git(*args, cwd):
    import subprocess
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "core.hooksPath=/dev/null", *args],
                   cwd=cwd, check=True, capture_output=True)


def test_self_respawn_finds_the_main_checkout_from_a_worktrees_copy(
        tmp_path, monkeypatch):
    monkeypatch.delenv("TL_DIR", raising=False)
    for name in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(name)
    script = ".claude/skills/respawn-sessions/self-respawn.sh"
    head = script_head(script, '[ -n "$TL_DIR" ]')
    inside = os.path.join(".claude", "skills", "respawn-sessions")
    main = tmp_path / "main"
    main.mkdir()
    git("init", "-q", cwd=main)
    git("commit", "-q", "--allow-empty", "-m", "x", cwd=main)
    worktree = tmp_path / "elsewhere" / "w"
    git("worktree", "add", "-q", str(worktree), "-b", "w", cwd=main)
    want = os.path.realpath(main)
    assert run_head(main / inside, head, "TL_DIR", ["123"])[:2] == (0, want)
    assert run_head(worktree / inside, head, "TL_DIR", ["123"])[:2] == (0, want)
    # an explicit TL_DIR is used as given
    assert run_head(worktree / inside, head, "TL_DIR", ["123"],
                    {"TL_DIR": "/somewhere/else"})[:2] == (0, "/somewhere/else")
    # outside any checkout it stops, before the part of the script that kills
    rc, out, said = run_head(tmp_path / "nowhere" / inside, head, "TL_DIR", ["123"],
                             {"GIT_CEILING_DIRECTORIES": str(tmp_path)})
    assert rc == 1 and "ABORT" in said and "Not killing self" in said
    assert out.startswith("ABORT"), "nothing ran after the abort"
    with open(os.path.join(ROOT, script)) as f:
        body = f.read()
    stop = body.index('[ -n "$TL_DIR" ]')
    assert stop < body.index("kill-session") and stop < body.index('kill "$SELF_PID"')
