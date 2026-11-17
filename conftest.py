"""Tests never read this machine's own values.

Scripts read hostnames, local addresses and launch details from the `local:`
block of the gitignored registry.yaml (scripts/local_config.py). A test that
reached the real file would pass on one machine and fail on a fresh clone, and
could talk to a live service. So before any test module is imported, the
reader is pointed at a file of made-up values. A test that needs the
"could not read" case points TEAM_LEAD_LOCAL_CONFIG somewhere else itself.
"""
import json
import os
import tempfile

TEST_LOCAL_VALUES = {
    "hive_url": "http://hive.test:1111",
    "github_broker_url": "http://github-broker.test:2222",
    "notion_receiver_url": "http://notion-receiver.test:3333",
    "notion_tunnel_host": "notion.test",
    "board_tunnel_host": "board.test",
    "launchd_notion_receiver": "test.notion-receiver",
    "launchd_github_broker": "test.github-broker",
    "launchd_notion_tunnel": "test.notion-tunnel",
    "launchd_board_tunnel": "test.board-tunnel",
    "notion_receiver_log": "~/test-logs/notion-receiver.log",
    "github_broker_log": "~/test-logs/github-broker.log",
    "discord_state_subdir": ".test-discord",
    "discord_allowlist_file": "allow.test.json",
    "discord_empty_allowlist": '{"test": []}',
    "no_discord_state_dir": os.path.join(tempfile.gettempdir(),
                                         "team-lead-test-empty-discord"),
    "dev_channel_flag": "--test-channel-flag",
}

_fd, TEST_LOCAL_CONFIG = tempfile.mkstemp(prefix="local-config-", suffix=".json")
with os.fdopen(_fd, "w") as _f:
    json.dump(TEST_LOCAL_VALUES, _f)
os.environ["TEAM_LEAD_LOCAL_CONFIG"] = TEST_LOCAL_CONFIG
