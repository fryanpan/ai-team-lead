#!/usr/bin/env python3
"""
Build or refresh a Claude Code config folder for a second subscription account.

A session started with CLAUDE_CONFIG_DIR=<folder> keeps its claude.ai login in
that folder: the macOS Keychain entry is keyed to the folder's path string, and
the global config (`.claude.json`, which holds `oauthAccount`) lives at
`<folder>/.claude.json` instead of `~/.claude.json`. Everything else Claude Code
keeps under `~/.claude` would move too, so this script links the shared parts
back to the main folder and keeps one copy of settings, plugins, transcripts
and memory.

Three kinds of entry:

  SHARED       symlinked to the main config folder. Settings, plugins,
               transcripts + auto memory (projects/), skills, hooks, plans,
               tasks, teams, file history, prompt history.
  PER_ACCOUNT  never linked. The login, the global config, and caches that
               are tied to the account or to one running process.
  SEED         `.claude.json` is created once from an allowlist of the main
               one's keys: MCP servers, per-project trust and MCP config,
               onboarding flags. Account keys (`oauthAccount`, the user id,
               every account-scoped cache) are never copied.

Anything in the main folder that is in neither list is reported, not linked,
so a new kind of state gets a decision instead of a default.

Usage:
  python3 account_dir.py <folder> [--execute] [--sync-config]
                         [--source <dir>] [--source-config <file>]

  <folder>        The account folder. The Keychain entry is keyed to the exact
                  path string, so a different spelling of the same folder is a
                  different login. This script and respawn.py both use the
                  realpath; run /login with that spelling (the script prints it).
  --execute       Make the changes. Without it this is a dry run.
  --sync-config   Re-copy `mcpServers` and the per-project keys into an
                  existing `<folder>/.claude.json`, leaving its account keys
                  alone. Run it only while no session is using the folder:
                  Claude Code rewrites that file itself.
  --source        Main config folder. Default ~/.claude.
  --source-config Main global config. Default ~/.claude.json.

Never touches the main folder or the main `.claude.json`: both are read only.
"""
import json
import os
import sys
import tempfile
from typing import Dict, List, Tuple

SHARED = [
    # configuration
    "settings.json", "settings.local.json", "CLAUDE.md", "keybindings.json",
    "agents", "commands", "skills", "rules", "output-styles", "workflows",
    "agent-memory", "themes", "hooks", "plugins",
    # history, transcripts and memory
    "projects", "history.jsonl", "file-history", "plans", "tasks", "teams",
    "uploads", "paste-cache", "usage-data",
    # tool and plugin state
    "channels", "claude-workspaces", "live-feedback", "jobs", "feedback", "chrome",
]

PER_ACCOUNT = {
    ".claude.json", ".credentials.json", "backups", "mcp-needs-auth-cache.json",
    "cache", "state", "daemon", "sessions", "session-env", "shell-snapshots",
    "debug", "telemetry", "statsig", "ide", "downloads", "image-cache",
    "feedback-bundles", "todos", "logs", "checkpoints", ".last-cleanup", ".last-update-result.json",
}

# `.claude.json` keys copied into a new folder. An allowlist: the file also
# holds the login's account record and caches of that account's entitlements,
# and a key added by a future release should not be copied until someone looks.
SEED_KEYS = {
    "mcpServers", "projects", "githubRepoPaths",
    "hasCompletedOnboarding", "lastOnboardingVersion", "lastReleaseNotesSeen",
    "installMethod", "autoUpdates", "autoUpdatesProtectedForNative", "machineID",
    "migrationVersion", "opusProMigrationComplete", "sonnet1m45MigrationComplete",
    "hasSeenTasksHint", "tipsHistory", "showSpinnerTree",
    "effortCalloutDismissed", "effortCalloutV2Dismissed", "hasShownOpus46Notice",
    "claudeInChromeDefaultEnabled", "hasCompletedClaudeInChromeOnboarding",
    "cachedChromeExtensionInstalled", "chromeExtension",
    "optionAsMetaKeyInstalled", "iterm2It2SetupComplete", "deepLinkTerminal",
    "remoteDialogSeen", "hasUsedRemoteControl", "agentPushNotifEnabled",
    "transcriptShareDismissed", "autoModeEnvSetup",
    "hasResetAutoModeOptInForDefaultOffer", "workflowSizeGuideline",
    "officialMarketplaceAutoInstallAttempted", "officialMarketplaceAutoInstalled",
}

# Per-project keys that matter to an agent: trust, and project-scoped MCP
# servers (respawn.py reads these for its direct channel flags). The rest of a
# project entry is last-session metrics.
PROJECT_KEYS = {
    "hasTrustDialogAccepted", "allowedTools", "mcpServers",
    "enabledMcpjsonServers", "disabledMcpjsonServers", "mcpContextUris",
    "hasClaudeMdExternalIncludesApproved", "hasClaudeMdExternalIncludesWarningShown",
}


def seed_config(src: dict) -> dict:
    out = {k: v for k, v in src.items() if k in SEED_KEYS}
    out["projects"] = {
        path: {k: v for k, v in (entry or {}).items() if k in PROJECT_KEYS}
        for path, entry in (src.get("projects") or {}).items()
    }
    return out


def sync_config(dst: dict, src: dict) -> dict:
    """Refresh MCP servers and per-project keys from `src`; keep everything
    else in `dst`, account keys included."""
    out = dict(dst)
    out["mcpServers"] = src.get("mcpServers") or {}
    projects = dict(out.get("projects") or {})
    for path, entry in (src.get("projects") or {}).items():
        merged = dict(projects.get(path) or {})
        merged.update({k: v for k, v in (entry or {}).items() if k in PROJECT_KEYS})
        projects[path] = merged
    out["projects"] = projects
    return out


def write_json_600(path: str, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".claude.json.")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def plan_links(folder: str, source: str) -> Tuple[List[Tuple[str, str]], List[str], List[str]]:
    """Return (links to create, conflicts, unclassified source entries).

    A link is (link path, target). An entry already linked to the right target
    is left out. Anything else already at a link path is a conflict and is
    never replaced.
    """
    present = set(os.listdir(source))
    # Loose top-level notes (*.md) are the user's files; share them too.
    wanted = [n for n in SHARED if n in present] + sorted(
        n for n in present
        if n.endswith(".md") and not n.startswith(".") and n not in SHARED)
    links, conflicts = [], []
    for name in wanted:
        link, target = os.path.join(folder, name), os.path.join(source, name)
        if os.path.islink(link):
            if os.readlink(link) != target:
                conflicts.append(f"{link} -> {os.readlink(link)} (want {target})")
            continue
        if os.path.exists(link):
            conflicts.append(f"{link} exists and is not a link")
            continue
        links.append((link, target))
    unclassified = sorted(
        n for n in present
        if n not in SHARED and n not in PER_ACCOUNT and n not in wanted)
    return links, conflicts, unclassified


def main(argv: List[str]) -> int:
    args = list(argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if args else 2
    execute = "--execute" in args
    sync = "--sync-config" in args

    def opt(name: str, default: str) -> str:
        if name in args:
            i = args.index(name)
            if i + 1 >= len(args):
                sys.exit(f"{name} requires a value")
            return args[i + 1]
        return default

    source = os.path.realpath(os.path.expanduser(opt("--source", "~/.claude")))
    source_config = os.path.expanduser(opt("--source-config", "~/.claude.json"))
    positional = [a for i, a in enumerate(args)
                  if not a.startswith("--") and (i == 0 or args[i - 1] not in ("--source", "--source-config"))]
    if len(positional) != 1:
        sys.exit("give exactly one account folder")
    # The Keychain entry is keyed to the exact path string, so every tool uses
    # the realpath spelling; respawn.py canonicalizes the same way.
    folder = os.path.realpath(os.path.expanduser(positional[0]))
    if folder == source:
        sys.exit("the account folder is the main config folder")

    mode = "EXECUTE" if execute else "dry run"
    print(f"[{mode}] account folder {folder}\n  shares from {source}")
    if execute:
        os.makedirs(folder, mode=0o700, exist_ok=True)

    links, conflicts, unclassified = plan_links(folder, source)
    for link, target in links:
        print(f"  link  {os.path.basename(link)} -> {target}")
        if execute:
            os.symlink(target, link)

    cfg_path = os.path.join(folder, ".claude.json")
    with open(source_config) as f:
        src_cfg = json.load(f)
    if os.path.islink(cfg_path):
        # A linked .claude.json shares oauthAccount between two logins.
        conflicts.append(f"{cfg_path} is a link; it must be this folder's own file")
    elif not os.path.lexists(cfg_path):
        seeded = seed_config(src_cfg)
        print(f"  seed  .claude.json: {len(seeded)} keys, {len(seeded['projects'])} projects "
              f"(no oauthAccount, no account caches)")
        if execute:
            write_json_600(cfg_path, seeded)
    elif sync:
        with open(cfg_path) as f:
            dst_cfg = json.load(f)
        print("  sync  .claude.json: mcpServers + per-project trust/MCP keys")
        if execute:
            write_json_600(cfg_path, sync_config(dst_cfg, src_cfg))
    else:
        print("  keep  .claude.json (exists; --sync-config refreshes MCP and project keys)")

    for c in conflicts:
        print(f"  CONFLICT {c}")
    for n in unclassified:
        print(f"  UNCLASSIFIED {n}: not linked; add it to SHARED or PER_ACCOUNT")
    print(f"\nLog in once with:  CLAUDE_CONFIG_DIR={folder} claude   then /login")
    if not execute:
        print("Dry run. Re-run with --execute.")
    return 1 if conflicts else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
