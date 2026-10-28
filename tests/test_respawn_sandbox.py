"""--sandbox starts a session inside the lead sandbox.

The sandbox script execs the real binary, so the launcher must reproduce what
the ~/.zshrc `claude` function adds: the channel flags and the fleet prompt
file. And the result has to be valid zsh, because tmux hands it to `zsh -ic`.
"""
import importlib.util
import os
import subprocess

import pytest

_spec = importlib.util.spec_from_file_location(
    "respawn", os.path.join(os.path.dirname(__file__), "..", ".claude",
                            "skills", "respawn-sessions", "respawn.py"))
respawn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(respawn)


@pytest.fixture(autouse=True)
def _reset():
    respawn.SANDBOX_LEAD = None
    yield
    respawn.SANDBOX_LEAD = None


def test_without_sandbox_the_launcher_is_the_shell_function():
    assert respawn.claude_launcher() == "claude"


def test_sandbox_launcher_wraps_the_real_binary_with_the_functions_flags():
    respawn.SANDBOX_LEAD = "some-lead"
    s = respawn.claude_launcher()
    assert "lead-sandbox.ts" in s
    assert "--lead some-lead --" in s
    assert '"$(whence -p claude)"' in s
    assert '"${CLAUDE_CHANNEL_FLAGS[@]}"' in s
    assert "--append-system-prompt-file" in s


def test_sandbox_launcher_is_valid_zsh():
    respawn.SANDBOX_LEAD = "some-lead"
    cmd = respawn.claude_launcher() + " --continue -n 'A Name'"
    r = subprocess.run(["/bin/zsh", "-n", "-c", cmd], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
