#!/usr/bin/env python3
"""Read this machine's own values from the `local:` block of registry.yaml.

Hostnames, local service addresses, launchd job names and launch details are
different on every machine, so they are not written in this repo. They live in
the gitignored registry.yaml, under one top-level block:

    local:
      hive_url: http://127.0.0.1:8000
      board_tunnel_host: board.example.com

registry.yaml.example lists every key with a placeholder value.

Where the values are read from, first hit wins:

  1. TEAM_LEAD_LOCAL_CONFIG, a path to either file below (tests use this);
  2. `local-config.json` beside this module. install_healthcheck.py writes it
     into the deploy root, because a launchd job cannot read the repo's volume;
  3. `registry.yaml` one directory above this module (a repo checkout).

There are three results, never two: the value, a LocalConfigError that says
the file could not be read, or a LocalConfigError that names the missing keys.
A caller must report the error. It must never fall back to a built-in value.

Minimal line parse, the same approach as the registry parsers: no PyYAML.

Usage:
    python3 scripts/local_config.py            # list the keys that are set
    python3 scripts/local_config.py hive_url   # print one value
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOYED_NAME = "local-config.json"
REGISTRY_NAME = "registry.yaml"
ENV = "TEAM_LEAD_LOCAL_CONFIG"

# Every key a script in this repo reads. registry.yaml.example carries the same
# list; tests/test_local_config.py fails when the two differ.
KEYS = (
    "hive_url",
    "github_broker_url",
    "notion_receiver_url",
    "notion_tunnel_host",
    "board_tunnel_host",
    "launchd_notion_receiver",
    "launchd_github_broker",
    "launchd_notion_tunnel",
    "launchd_board_tunnel",
    "notion_receiver_log",
    "github_broker_log",
    "discord_state_subdir",
    "discord_allowlist_file",
    "discord_empty_allowlist",
    "no_discord_state_dir",
    "dev_channel_flag",
)


class LocalConfigError(Exception):
    """The local values could not be read, or a needed key is not set."""


def parse_local(text):
    """The scalars of the top-level `local:` block, or None when there is no block."""
    values, inside = None, False
    for line in text.splitlines():
        if re.match(r"^local:\s*(#.*)?$", line):
            values, inside = {}, True
            continue
        if not inside:
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            break                       # the next top-level key ends the block
        m = re.match(r"^  ([a-z][a-z0-9_]*):\s*(.*)$", line)
        if not m:
            continue
        value = m.group(2).strip()
        quoted = re.match(r"""^(['"])(.*)\1\s*(#.*)?$""", value)
        if quoted:
            value = quoted.group(2)     # a quoted value keeps any # inside it
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if value:
            values[m.group(1)] = value
    return values


def source_path():
    """The file the values come from, and whether it is the deployed JSON copy."""
    override = os.environ.get(ENV)
    if override:
        return override, override.endswith(".json")
    deployed = os.path.join(HERE, DEPLOYED_NAME)
    if os.path.exists(deployed):
        return deployed, True
    return os.path.join(os.path.dirname(HERE), REGISTRY_NAME), False


def load(path=None):
    """Every local value as a dict. Raises LocalConfigError when it cannot look."""
    if path is None:
        path, is_json = source_path()
    else:
        is_json = path.endswith(".json")
    try:
        with open(path) as f:
            text = f.read()
    except OSError as e:
        raise LocalConfigError(
            f"COULD NOT READ local values: {path} ({e.strerror or e}). "
            f"Copy registry.yaml.example to registry.yaml and fill in its "
            f"`local:` block.") from e
    if is_json:
        try:
            values = json.loads(text)
        except ValueError as e:
            raise LocalConfigError(
                f"COULD NOT READ local values: {path} is not valid JSON ({e}). "
                f"Rerun scripts/install_healthcheck.py.") from e
        if not isinstance(values, dict):
            raise LocalConfigError(
                f"COULD NOT READ local values: {path} does not hold an object.")
        return {k: str(v) for k, v in values.items() if v not in (None, "")}
    values = parse_local(text)
    if values is None:
        raise LocalConfigError(
            f"COULD NOT READ local values: {path} has no `local:` block. "
            f"Copy the block from registry.yaml.example and fill it in.")
    return values


def require(*keys, path=None):
    """The named values, in order. Raises LocalConfigError naming every missing key."""
    values = load(path)
    missing = [k for k in keys if not values.get(k)]
    if missing:
        where = path or source_path()[0]
        raise LocalConfigError(
            f"COULD NOT READ local values: {where} does not set "
            f"{', '.join(missing)}. registry.yaml.example shows each key.")
    return [values[k] for k in keys]


def get(key, path=None):
    """One value. Raises LocalConfigError when it is not set."""
    return require(key, path=path)[0]


def main(argv):
    try:
        if len(argv) > 1:
            print(get(argv[1]))
        else:
            values = load()
            print(f"local values read from {source_path()[0]}")
            for k in KEYS:
                print(f"  {k}: {'set' if values.get(k) else 'MISSING'}")
            if any(not values.get(k) for k in KEYS):
                print("A key you do not need yet: delete its line. Never leave "
                      "a key with no value, because the project parsers read a "
                      "bare `  name:` line as a project.")
                return 1
    except LocalConfigError as e:
        print(e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
