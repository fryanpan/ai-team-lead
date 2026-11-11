"""The launch scripts add the private system-prompt add-on when it exists.

When it is missing they launch anyway and warn, naming the missing file. The
command must stay valid zsh, because tmux hands it to `zsh -ic`, and zsh must
read the add-on into one argument.
"""
import importlib.util
import os
import subprocess

import pytest

HERE = os.path.dirname(__file__)
SKILL_DIR = os.path.join(HERE, "..", ".claude", "skills", "respawn-sessions")
SELF_RESPAWN = os.path.join(SKILL_DIR, "self-respawn.sh")

_spec = importlib.util.spec_from_file_location(
    "respawn", os.path.join(SKILL_DIR, "respawn.py"))
respawn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(respawn)

ADDON_TEXT = "# Add-on\n\nIt's a rule with \"quotes\", a $dollar and `ticks`.\n"


@pytest.fixture
def overlay(tmp_path):
    """A config naming a checkout (with a space in its path) that holds the add-on."""
    checkout = tmp_path / "private checkout"
    (checkout / "plugin").mkdir(parents=True)
    addon = checkout / "plugin" / "system-prompt-append.md"
    addon.write_text(ADDON_TEXT)
    conf = tmp_path / "overlay.conf"
    conf.write_text(f"# test\nrules_dir=/nowhere\ncheckout={checkout}\n")
    return conf, addon


def zsh_argv(cmd: str) -> list:
    """The argv zsh would build from `cmd`, with `claude` stubbed to print it."""
    stub = "claude() { for a in \"$@\"; do printf '%s\\0' \"$a\"; done; }; "
    r = subprocess.run(["/bin/zsh", "-f", "-c", stub + cmd],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.split("\0")[:-1]


# ---- respawn.py ----

def test_addon_present_adds_the_flag_and_zsh_reads_the_file(overlay, capsys):
    conf, addon = overlay
    flag = respawn.private_prompt_flag(str(conf))
    assert "--append-system-prompt " in flag
    assert "--append-system-prompt-file" not in flag
    assert "WARNING" not in capsys.readouterr().err
    argv = zsh_argv("claude" + flag + " --continue")
    assert argv == ["--append-system-prompt", ADDON_TEXT.rstrip("\n"), "--continue"]


def test_addon_absent_launches_without_it_and_warns_naming_the_file(overlay, capsys):
    conf, addon = overlay
    addon.unlink()
    assert respawn.private_prompt_flag(str(conf)) == ""
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert str(addon) in err


def test_empty_addon_counts_as_missing(overlay, capsys):
    conf, addon = overlay
    addon.write_text("")
    assert respawn.private_prompt_flag(str(conf)) == ""
    assert str(addon) in capsys.readouterr().err


def test_no_config_file_warns_naming_the_config(tmp_path, capsys):
    conf = tmp_path / "absent.conf"
    assert respawn.private_prompt_flag(str(conf)) == ""
    assert str(conf) in capsys.readouterr().err


def test_config_without_checkout_line_warns(tmp_path, capsys):
    conf = tmp_path / "overlay.conf"
    conf.write_text("rules_dir=/x\n")
    assert respawn.private_prompt_flag(str(conf)) == ""
    assert "no checkout= line" in capsys.readouterr().err


def test_env_var_names_the_config(overlay, monkeypatch):
    conf, _ = overlay
    monkeypatch.setenv("TEAM_LEAD_FLEET_PRIVATE_CONF", str(conf))
    assert respawn.private_prompt_flag().startswith(" --append-system-prompt ")


def test_sandbox_command_with_addon_is_valid_zsh(overlay):
    conf, _ = overlay
    respawn.SANDBOX_LEAD = "some-lead"
    try:
        cmd = respawn.claude_launcher() + respawn.private_prompt_flag(str(conf)) + " --continue"
    finally:
        respawn.SANDBOX_LEAD = None
    r = subprocess.run(["/bin/zsh", "-n", "-c", cmd], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ---- self-respawn.sh ----

def run_self_respawn(conf, tmp_path):
    env = dict(os.environ, TEAM_LEAD_FLEET_PRIVATE_CONF=str(conf),
               SELF_RESPAWN_PRINT_CMD="1", TL_NAME="Team Lead",
               TL_SELFRESPAWN_LOG=str(tmp_path / "log"))
    r = subprocess.run(["/bin/bash", SELF_RESPAWN, "1"], capture_output=True,
                       text=True, env=env)
    assert r.returncode == 0, r.stderr
    # The script prints the word as the job's bash sees it; let bash unquote it.
    word = r.stdout.strip()
    d = subprocess.run(["/bin/bash", "-c", "printf '%s' " + word],
                       capture_output=True, text=True)
    assert d.returncode == 0, d.stderr
    return d.stdout, r.stderr


def test_self_respawn_addon_present(overlay, tmp_path):
    conf, _ = overlay
    cmd, err = run_self_respawn(conf, tmp_path)
    assert "WARNING" not in err
    assert zsh_argv(cmd) == ["--continue", "-n", "Team Lead", "--remote-control",
                             "Team Lead", "--append-system-prompt",
                             ADDON_TEXT.rstrip("\n")]


def test_self_respawn_addon_absent(overlay, tmp_path):
    conf, addon = overlay
    addon.unlink()
    cmd, err = run_self_respawn(conf, tmp_path)
    assert "WARNING" in err and str(addon) in err
    assert "--append-system-prompt" not in cmd
    assert zsh_argv(cmd) == ["--continue", "-n", "Team Lead", "--remote-control",
                             "Team Lead"]
