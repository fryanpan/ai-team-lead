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
     into the deploy root, because a launchd job cannot read the repo's volume.
     It also holds the checkout's own path, which the installer works out;
  3. `registry.yaml` one directory above this module (a repo checkout).

There are three results, never two: the value, a LocalConfigError that says
the file could not be read, or a LocalConfigError that names the missing keys.
A key whose line holds only a comment is a missing key.
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
    "launchd_board_server",
    "launchd_board_server_former",
    "launchd_healthcheck",
    "launchd_guard",
    "launchd_log_rotate",
    "notion_receiver_log",
    "github_broker_log",
    "discord_state_subdir",
    "discord_allowlist_file",
    "discord_empty_allowlist",
    "no_discord_state_dir",
    "dev_channel_flag",
)


# Not a key of the `local:` block. install_healthcheck.py works this value out
# and writes it into the deployed copy; see repo_root().
REPO_ROOT_KEY = "repo_root"


class LocalConfigError(Exception):
    """The local values could not be read, or a needed key is not set."""


BOM = "\ufeff"


def parse_local(text):
    """The scalars of the top-level `local:` block, or None when there is no block.

    Raises LocalConfigError for a value this parse cannot read: one that opens
    with a quote and does not close it, or has text after the closing quote.
    """
    values, inside = None, False
    # Some editors save a byte order mark first. With it left in, the first
    # line never matches and a file whose first line is `local:` reads as
    # having no block.
    for line in text.lstrip(BOM).splitlines():
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
        key, value = m.group(1), m.group(2).strip()
        if value[:1] in ("'", '"'):
            # A quoted value keeps any # inside it. Backslashes are kept as
            # written, in double quotes too: this is not a YAML parser, and no
            # value here needs an escape.
            quoted = re.match(r"""^(['"])(.*)\1\s*(#.*)?$""", value)
            if not quoted or len(value) == 1:
                raise LocalConfigError(
                    f"COULD NOT READ local values: the value of `{key}` opens "
                    f"with a quote that is not closed, or has text after the "
                    f"closing quote: {value}")
            value = quoted.group(2)
        elif value.startswith("#"):
            value = ""                  # only a comment: the key is not set
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if value:
            values[key] = value
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
            values = json.loads(text.lstrip(BOM))
        except ValueError as e:
            raise LocalConfigError(
                f"COULD NOT READ local values: {path} is not valid JSON ({e}). "
                f"Rerun scripts/install_healthcheck.py.") from e
        if not isinstance(values, dict):
            raise LocalConfigError(
                f"COULD NOT READ local values: {path} does not hold an object.")
        return {k: str(v) for k, v in values.items() if v not in (None, "")}
    try:
        values = parse_local(text)
    except LocalConfigError as e:
        raise LocalConfigError(f"{e} (in {path})") from None
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


def repo_root():
    """The physical path of this repo's checkout.

    In a checkout it is the folder above this module, so nobody has to write
    it down. A deployed copy of this module is not inside the checkout and
    cannot work the path out, so install_healthcheck.py writes it into
    local-config.json and it is read from there. Raises LocalConfigError when
    the deployed copy does not hold it. There is no built-in path.
    """
    path, deployed = source_path()
    if deployed:
        if not load(path).get(REPO_ROOT_KEY):
            raise LocalConfigError(
                f"COULD NOT READ the checkout's path: {path} does not set "
                f"{REPO_ROOT_KEY}. It is not a key of the `local:` block: "
                f"install_healthcheck.py writes it into that file. Rerun the "
                f"installer from the main checkout.")
        return get(REPO_ROOT_KEY, path=path)
    return os.path.realpath(os.path.dirname(HERE))


WORKTREE_MARKER = "/.git/worktrees/"


def main_checkout_of(root):
    """The main checkout that the checkout at `root` belongs to.

    A main checkout's `.git` is a folder, and the answer is `root` itself. A
    linked worktree's `.git` is a file whose first line is
    `gitdir: <main checkout>/.git/worktrees/<name>`, and the answer is read
    from that line, with no other program involved. A `.git` file of any
    other shape raises LocalConfigError: the main checkout is not guessed.
    """
    git = os.path.join(root, ".git")
    if not os.path.isfile(git):
        return root
    try:
        with open(git) as f:
            first = f.readline().strip()
    except OSError as e:
        raise LocalConfigError(
            f"COULD NOT READ the main checkout: {git} cannot be read ({e}). "
            f"Run this from the main checkout.") from None
    if first.startswith("gitdir: /") and WORKTREE_MARKER in first:
        main, _, name = first[len("gitdir: "):].rpartition(WORKTREE_MARKER)
        if main and name and "/" not in name:
            return os.path.realpath(main)
    raise LocalConfigError(
        f"COULD NOT READ the main checkout: {root} is a linked worktree, and "
        f"its .git file does not name `<main checkout>{WORKTREE_MARKER}<name>` "
        f"(it starts {first[:80]!r}). Run this from the main checkout.")


def main_checkout():
    """The physical path of this repo's MAIN checkout, also when this module
    sits in a linked worktree of it. A deployed copy returns repo_root(): the
    installer only runs from the main checkout. Raises LocalConfigError."""
    if source_path()[1]:
        return repo_root()
    return main_checkout_of(repo_root())


def checkouts_dir():
    """The folder that holds this repo's main checkout. Other project
    checkouts usually sit beside it. Raises LocalConfigError like
    main_checkout()."""
    return os.path.dirname(main_checkout())


def try_read(read):
    """(read(), "") or (None, why) when the value cannot be read.

    For a script that must keep running without the value and report that it
    could not look. The caller says what it did not do; it never puts a
    built-in value in place of the one it could not read.
    """
    try:
        return read(), ""
    except Exception as e:
        return None, str(e) or type(e).__name__


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
