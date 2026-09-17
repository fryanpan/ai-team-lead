#!/usr/bin/env python3
"""Haiku-based diff scrub pass — catches leaks the regex check can't.

Invoked by .githooks/pre-push AFTER scripts/scrub-check.py passes. The regex
check is fast and deterministic for known patterns (registry project names,
denylist entries). Haiku adds a context-aware AI scan for things the regex
can't anticipate: unrecognized real names, contextual identifiers, quotes
that reveal a private person, financial/health specifics in personal context.

Usage:
  scrub-haiku.py --diff-range A..B    # scan diff in range
  scrub-haiku.py                       # read diff from stdin

Exit codes:
  0  clean (or Haiku unavailable — defensive non-block)
  1  leaks found — push blocked
  2  setup error (treated as 0 by the hook so missing key / network blip
     doesn't break pushes; the regex check still ran)

Bypass entirely with SCRUB_SKIP=1. Skip just Haiku with SCRUB_SKIP_HAIKU=1.

Spend accounting
----------------
Every call this script makes costs money on a metered API key, and that spend
is invisible to the subscription quota meters — nothing else in the fleet sees
it. On 2026-09-11 roughly $55-60 went through this key in a day without anyone
noticing until the bill did, so each invocation now records what it cost and
refuses to start once the day's total crosses a cap.

  SCRUB_HAIKU_DAILY_USD   the cap, default 1.00 (the fleet rule for CI calling
                          a paid model)
  SCRUB_HAIKU_SPEND_LOG   where the ledger lives, default
                          ~/.local/state/scrub-haiku/spend.jsonl
  SCRUB_HAIKU_BUDGET_BLOCK=1
                          make a budget abort block the push. Default is
                          non-blocking, matching this layer's existing
                          fail-open behaviour — but never silently: a skipped
                          scan prints a banner, because a scan that did not run
                          reporting the same quiet nothing as a clean one is the
                          exact bug that let a real name reach a public repo.

The ledger deliberately lives OUTSIDE any repo. It records diff ranges and
timestamps, and this gate exists to keep exactly that sort of thing out of a
public checkout.
"""

from __future__ import annotations

import datetime
import base64
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Optional

MODEL = "claude-haiku-4-5-20251001"
# Haiku 4.5 published rates, USD per million tokens. These are for the estimate
# in the ledger; the invoice is the Anthropic Console's number, not this one.
PRICE_IN_PER_MTOK = 1.00
PRICE_OUT_PER_MTOK = 5.00
DEFAULT_DAILY_USD = 1.00
# Where the key actually lives on this machine. `security add-generic-password
# -a "$USER" -s scrub-haiku-api-key -w` (omit the value; it prompts, so the key
# stays out of shell history).
KEYCHAIN_SERVICE = "scrub-haiku-api-key"

# The fleet key, stored by the Workspaces secret card rather than by hand.
# That store namespaces every name it is asked for under a fixed prefix and a
# fixed account, and base64-encodes the value before it goes down
# `security -i` — a raw read returns base64, not the key. Preferred over the
# hand-written entry because real dollars only split by Console workspace,
# and this key is the one billed to the `fleet` workspace.
FLEET_KEYCHAIN_SERVICE = "claude-workspaces-secret.fleet-anthropic-api-key"
FLEET_KEYCHAIN_ACCOUNT = "claude-workspaces"
API_URL = "https://api.anthropic.com/v1/messages"
API_TIMEOUT_SEC = 30
# Approx chars-to-tokens (Anthropic English ~3.5 chars/token; be conservative at 4).
# 80K tokens of diff caps out a Haiku call comfortably.
MAX_DIFF_CHARS = 80_000 * 4

SYSTEM_PROMPT = """You are a sensitive-content scanner. You will be shown a git diff that's about to be pushed to a public GitHub repository. Your job is to spot anything that would leak private information once that push lands.

**What counts as a leak (flag it):**
- Real personal names — but see the repo's own author below, who is never a leak here
- Email addresses, phone numbers, postal addresses, SSNs, financial account numbers
- Specific dollar amounts in personal context (taxes, donations, balances, salaries)
- Tax-document names tied to a specific person (Form 8606, Schedule D, kiddie tax, IRA backdoor, capital loss carryover, etc.)
- Health/medical specifics (CGM readings, HbA1c values, medications, diagnoses, doctor visits)
- Specific travel destinations + dates in personal context (e.g., "Berlin trip in June")
- Names of OTHER private projects — codenames the maintainer hasn't already published elsewhere
- API keys, OAuth tokens, bot tokens, Discord user IDs, webhook secrets
- Private internal URLs (Linear/Notion/Asana IDs that aren't already shared publicly)
- Quoted chat conversations or first-person snippets that identify a private person
- Filesystem paths containing a real username (e.g., `/Users/realname/...`)

**What does NOT count as a leak (don't flag):**
- The repo's own name in self-references (a repo's README / CLAUDE.md / package metadata legitimately names itself)
- **The repo's own author, named in the AUTHOR line below, anywhere in the diff.**
  Not only in metadata fields — in prose, in a learning that quotes them, in a
  changelog. They own this repo, they sign every commit in it, and their name is
  already throughout its published history. Flagging it blocks their own work on
  their own public repo and teaches everyone to bypass the gate.
- Public technical references (Anthropic, Claude, GitHub URLs to known public repos, well-known libraries)
- Generic placeholders: <user>, <your-tailnet>, your-username/example, my-project, the user
- Function/variable/class names, programming jargon, code comments about the code itself
- Standard package descriptions ("a Python module that does X")
- **Anything on a line starting with `-`.** Those lines are being REMOVED by
  this push. A commit that deletes a leak is the fix, not the leak; flagging it
  blocks the one change that improves the situation. Judge only added lines
  (`+`) and, for context, unchanged ones. If a name appears on a `-` line and
  not on any `+` line, that is a removal — say nothing.

**Output format — respond in EXACTLY this shape:**

If clean:
VERDICT: CLEAN

If leaks found:
VERDICT: LEAKS_FOUND
LEAKS:
- <file> — "<the exact leaking text, copied verbatim from a + line>" — <what it leaks>
- <file> — "<the exact leaking text, copied verbatim from a + line>" — <what it leaks>

**Quote, do not cite a line number.** You are reading a diff, so you cannot know
a file's real line numbers, and a number you invent sends the reader to a line
that says something else — which reads as the gate hallucinating and gets it
switched off. The quoted span is checkable: the reader greps for it and either
finds it or knows you were wrong. If you cannot quote the text from a `+` line,
you do not have a finding.

Be conservative — when borderline, flag it. The human can override with SCRUB_SKIP=1 after reviewing your reasoning."""


def read_keychain(service: str) -> Optional[str]:
    """Read a generic-password entry from the macOS Keychain.

    The Keychain is preferred over an exported env var because every Claude
    Code session on this machine runs as the same user and inherits the same
    environment — an exported key is readable by every agent in the fleet, and
    this one is billed. So the key is stored where it isn't exported, which
    means a scanner that only reads env vars finds nothing and skips.

    Returns None (never raises) on any failure: a missing entry, a locked
    Keychain, or a non-Darwin machine all mean "fall through to the env vars",
    and a scrub layer must never be the reason a push dies.
    """
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-a", os.environ.get("USER", ""),
             "-s", service, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def usable_api_key(key: str) -> bool:
    """Whether a string is shaped like a whole Anthropic API key.

    This exists because a TRUNCATED key is the failure that actually happened:
    the secret card cut every value at 96 characters, and the result decoded
    cleanly, looked like a key, and earned a 401. A 401 reads as revoked, so
    the gate would have reported "scan did not run" while the real cause was a
    string that had simply been cut in half.

    Deliberately a shape check and not a length equality. Pinning the exact
    length of today's keys would turn a future format change into a gate that
    refuses a perfectly good credential, which is the more expensive mistake —
    a scrub layer must never be the reason a push dies. A truncation big enough
    to matter is caught by the floor.
    """
    return key.startswith("sk-ant-") and len(key) >= 100


def read_fleet_keychain() -> Optional[str]:
    """Read the fleet key out of the Workspaces secret store.

    Separate from `read_keychain` for two reasons, both of which silently
    return the WRONG STRING rather than failing if they are got wrong: the
    account is a fixed constant, not `$USER`, and the stored value is base64
    so that a secret carrying a newline can go down `security -i` as one
    command line. A raw read succeeds and hands back base64 that the API then
    rejects as a bad key — which is a 401, not a "no key" skip, so it would
    look like a revoked key rather than a decode nobody did.

    Returns None (never raises) on anything at all: absent entry, locked
    Keychain, non-Darwin, or a value that is not valid base64. Every one of
    those means "fall through", because a scrub layer must never be the reason
    a push dies.
    """
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-a", FLEET_KEYCHAIN_ACCOUNT,
             "-s", FLEET_KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout.strip()
    if not raw:
        return None
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8").strip()
    except (ValueError, UnicodeDecodeError):
        return None
    if not decoded:
        return None
    if not usable_api_key(decoded):
        # Present but unusable. Say so — do NOT fall through quietly. A key
        # that is there and wrong is a different state from no key at all: the
        # API answers 401, which reads as revoked rather than as truncated,
        # and the gate would report "scan did not run" with no hint why.
        print(
            f"[scrub-haiku] the fleet key in `{FLEET_KEYCHAIN_SERVICE}` is not a "
            "usable Anthropic key — it is the wrong shape or too short to be "
            "whole. Falling back to the hand-written key. Mint a fresh one and "
            "re-save it on the secret card; the truncated value cannot be "
            "repaired and the Console will not show the original again.",
            file=sys.stderr,
        )
        return None
    return decoded


def spend_log_path() -> pathlib.Path:
    """The ledger. Outside any repo, and shared across every repo the gate runs in.

    Shared on purpose: the cap is a property of the KEY, not of a checkout. This
    gate runs in two repos and eight worktrees of one of them, and a per-repo
    ledger would let each of them spend the full cap independently — ten times
    the budget, with every individual log looking obedient.
    """
    override = os.environ.get("SCRUB_HAIKU_SPEND_LOG")
    if override:
        return pathlib.Path(override).expanduser()
    return pathlib.Path.home() / ".local" / "state" / "scrub-haiku" / "spend.jsonl"


def estimate_usd(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000 * PRICE_IN_PER_MTOK
            + output_tokens / 1_000_000 * PRICE_OUT_PER_MTOK)


def spent_today() -> tuple[float, int, Optional[str]]:
    """Today's spend so far: (usd, calls, error).

    Three states, not two. A missing ledger is a genuine zero — first run on
    this machine. An unreadable or corrupt one is NOT zero, and returns an error
    string instead, because "I could not look" reported as "$0.00 spent" is how
    a budget check becomes decorative. The caller must say which it got.
    """
    path = spend_log_path()
    if not path.exists():
        return 0.0, 0, None

    today = datetime.datetime.now().astimezone().strftime("%Y-%m-%d")
    usd = 0.0
    calls = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    # One bad line is not a reason to distrust the whole ledger,
                    # but it is a reason not to claim a precise total.
                    continue
                if entry.get("date") != today:
                    continue
                usd += float(entry.get("estimated_usd") or 0.0)
                calls += 1
    except OSError as e:
        return 0.0, 0, str(e)

    return usd, calls, None


def record_spend(entry: dict) -> Optional[str]:
    """Append one line to the ledger. Returns an error string on failure.

    A failure here must never block a push — but it must be reported, or the
    ledger silently stops growing and the cap silently stops binding.
    """
    path = spend_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError as e:
        return str(e)
    return None


def daily_cap_usd() -> float:
    raw = os.environ.get("SCRUB_HAIKU_DAILY_USD")
    if not raw:
        return DEFAULT_DAILY_USD
    try:
        return float(raw)
    except ValueError:
        print(
            f"[scrub-haiku] SCRUB_HAIKU_DAILY_USD={raw!r} is not a number — "
            f"using the default ${DEFAULT_DAILY_USD:.2f}.",
            file=sys.stderr,
        )
        return DEFAULT_DAILY_USD


def repo_author() -> Optional[str]:
    """The name this repo's commits are signed with.

    Resolved at runtime, never written down here: this file is itself pushed to
    the public repo, so hardcoding the maintainer's name would put it in the one
    place the scanner exists to keep names out of. `git config user.name` is the
    configured signer; the most frequent author in the log is the fallback for a
    machine where that is unset.

    Returns None on any failure — the scan then runs with no author exception,
    which is the conservative direction.
    """
    try:
        proc = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()

        proc = subprocess.run(
            ["git", "log", "--format=%an", "-n", "200"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return None
        names = [n for n in proc.stdout.split("\n") if n.strip()]
        if not names:
            return None
        return max(set(names), key=names.count)
    except (OSError, subprocess.SubprocessError):
        return None


def build_system_prompt() -> str:
    """SYSTEM_PROMPT plus the AUTHOR line its exception refers to."""
    author = repo_author()
    if not author:
        return SYSTEM_PROMPT + (
            "\n\nAUTHOR: unknown — this repo's author could not be resolved, so "
            "apply the personal-name rule with no author exception."
        )
    return SYSTEM_PROMPT + (
        f"\n\nAUTHOR: {author} — the owner of this repo, who signs its commits. "
        "Their name appearing in this diff is not a leak, wherever it appears."
    )


def call_haiku(diff_content: str, range_spec: str = "-") -> int:
    # Keychain first, then the env vars. SCRUB_HAIKU_API_KEY is preferred over
    # ANTHROPIC_API_KEY so this layer can use a key separate from
    # general-purpose Anthropic usage (better audit + isolated billing); the
    # env forms stay supported for CI and one-off runs.
    # Which SOURCE won, never the value. Four sources fall through to each
    # other silently, so a successful scan cannot otherwise tell "the fleet key
    # is live" from "the fleet key is missing and we quietly used the old one".
    # Those need opposite actions during a key migration, and the whole point of
    # moving keys is that the spend lands on the new one — an unobservable
    # fallback means the migration can look done for weeks without having
    # happened.
    api_key, key_source = None, None
    for source, getter in (
        (f"fleet key ({FLEET_KEYCHAIN_SERVICE})", read_fleet_keychain),
        (f"hand-written Keychain entry ({KEYCHAIN_SERVICE})",
         lambda: read_keychain(KEYCHAIN_SERVICE)),
        ("SCRUB_HAIKU_API_KEY", lambda: os.environ.get("SCRUB_HAIKU_API_KEY")),
        ("ANTHROPIC_API_KEY", lambda: os.environ.get("ANTHROPIC_API_KEY")),
    ):
        api_key = getter()
        if api_key:
            key_source = source
            break
    if api_key:
        print(f"[scrub-haiku] key: {key_source}", file=sys.stderr)
    if not api_key:
        print(
            f"[scrub-haiku] no API key (Keychain `{FLEET_KEYCHAIN_SERVICE}` or "
            f"`{KEYCHAIN_SERVICE}`, SCRUB_HAIKU_API_KEY, or ANTHROPIC_API_KEY) "
            "— skipping Haiku check.",
            file=sys.stderr,
        )
        return 2

    body = json.dumps({
        "model": MODEL,
        "max_tokens": 1024,
        "system": build_system_prompt(),
        "messages": [{
            "role": "user",
            "content": f"Scan this diff for leaks:\n\n```diff\n{diff_content}\n```",
        }],
    }).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=API_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[scrub-haiku] HTTP {e.code} from Anthropic API: {body[:200]}", file=sys.stderr)
        return 2
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"[scrub-haiku] API call failed: {e}", file=sys.stderr)
        return 2

    # The call happened, so it is billable whatever the verdict turns out to be.
    # Record it before interpreting the response — an unparseable answer costs
    # exactly as much as a clean one, and a ledger that only counts successes
    # under-reports precisely when something is going wrong.
    usage = data.get("usage") or {}
    in_tok = int(usage.get("input_tokens") or 0)
    out_tok = int(usage.get("output_tokens") or 0)
    usd = estimate_usd(in_tok, out_tok)

    content = data.get("content", [])
    text = content[0].get("text", "").strip() if content else ""

    if not content:
        verdict, rc = "error-empty-response", 2
    elif "VERDICT: CLEAN" in text:
        verdict, rc = "clean", 0
    elif "VERDICT: LEAKS_FOUND" in text:
        verdict, rc = "leaks-found", 1
    else:
        verdict, rc = "error-unexpected-shape", 2

    now = datetime.datetime.now().astimezone()
    log_err = record_spend({
        "ts": now.isoformat(timespec="seconds"),
        "date": now.strftime("%Y-%m-%d"),
        "repo": os.path.basename(os.getcwd()),
        "range": range_spec,
        "model": MODEL,
        "diff_chars": len(diff_content),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "estimated_usd": round(usd, 6),
        "verdict": verdict,
    })

    prior_usd, prior_calls, _ = spent_today()
    print(
        f"[scrub-haiku] scan RAN — {verdict}; {in_tok} in / {out_tok} out tokens, "
        f"~${usd:.4f}. Today: ~${prior_usd:.4f} over {prior_calls} call(s), "
        f"cap ${daily_cap_usd():.2f}.",
        file=sys.stderr,
    )
    if log_err:
        print(
            f"[scrub-haiku] WARNING: could not write the spend ledger "
            f"({spend_log_path()}): {log_err}. The daily cap is NOT binding "
            f"until this is fixed.",
            file=sys.stderr,
        )

    if rc == 1:
        print("[scrub-haiku] Haiku flagged leaks:", file=sys.stderr)
        for line in text.split("\n"):
            print(f"  {line}", file=sys.stderr)
    elif verdict == "error-empty-response":
        print("[scrub-haiku] empty response from Haiku.", file=sys.stderr)
    elif verdict == "error-unexpected-shape":
        print("[scrub-haiku] unexpected response shape from Haiku:", file=sys.stderr)
        print(text, file=sys.stderr)

    return rc


def get_diff(range_spec: str) -> str:
    try:
        r = subprocess.run(
            ["git", "diff", range_spec],
            capture_output=True, text=True, check=True,
        )
        return r.stdout
    except subprocess.CalledProcessError:
        return ""


def main() -> int:
    if os.environ.get("SCRUB_SKIP") == "1":
        return 0
    if os.environ.get("SCRUB_SKIP_HAIKU") == "1":
        print("[scrub-haiku] SCRUB_SKIP_HAIKU=1 — bypassing Haiku check.", file=sys.stderr)
        return 0

    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
        return 0

    if "--spend-report" in args:
        usd, calls, err = spent_today()
        if err:
            print(f"[scrub-haiku] COULD NOT READ the spend ledger: {err}")
            return 2
        print(f"[scrub-haiku] ledger: {spend_log_path()}")
        print(f"[scrub-haiku] today: ~${usd:.4f} over {calls} call(s), "
              f"cap ${daily_cap_usd():.2f}")
        return 0

    # An unrecognised flag used to fall through to the stdin read below and
    # block forever with no output — the caller believes a scan is running and
    # it never is. That is the "could not look" state wearing the costume of a
    # slow success, so it is an error, not a fallback. Found 2026-09-16 when a
    # retrospective scan invoked with a non-existent --diff-file hung 35
    # minutes on a closed pipe.
    known = {"--help", "-h", "--spend-report", "--diff-range"}
    unknown = []
    i = 0
    while i < len(args):
        if args[i] == "--diff-range":
            i += 2
            continue
        if args[i] not in known:
            unknown.append(args[i])
        i += 1
    if unknown:
        print(
            f"[scrub-haiku] unknown argument(s): {' '.join(unknown)}\n"
            f"  known flags : --diff-range <range> | --spend-report | --help\n"
            f"  to scan a diff on stdin, pass no flags:  "
            f"scrub-haiku.py < some.diff\n"
            f"  NOTHING WAS SCANNED.",
            file=sys.stderr,
        )
        return 2

    range_spec = "-"
    if "--diff-range" in args:
        idx = args.index("--diff-range")
        if idx + 1 >= len(args):
            print("[scrub-haiku] --diff-range needs a value", file=sys.stderr)
            return 2
        range_spec = args[idx + 1]
        diff = get_diff(range_spec)
    else:
        diff = sys.stdin.read()

    if not diff.strip():
        return 0

    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS]
        print(
            f"[scrub-haiku] diff truncated to ~{MAX_DIFF_CHARS // 4} tokens for Haiku call.",
            file=sys.stderr,
        )

    # Budget gate, checked BEFORE spending anything.
    cap = daily_cap_usd()
    spent, calls, ledger_err = spent_today()
    if ledger_err:
        print(
            f"[scrub-haiku] WARNING: could not read the spend ledger "
            f"({spend_log_path()}): {ledger_err}. Proceeding WITHOUT a budget "
            f"check — this is not a $0.00 reading, it is an absent one.",
            file=sys.stderr,
        )
    elif spent >= cap:
        blocking = os.environ.get("SCRUB_HAIKU_BUDGET_BLOCK") == "1"
        print("", file=sys.stderr)
        print("=" * 72, file=sys.stderr)
        print("[scrub-haiku] SCAN DID NOT RUN — daily budget reached.", file=sys.stderr)
        print(f"  spent today : ~${spent:.4f} over {calls} call(s)", file=sys.stderr)
        print(f"  daily cap   : ${cap:.2f}  (SCRUB_HAIKU_DAILY_USD)", file=sys.stderr)
        print(f"  ledger      : {spend_log_path()}", file=sys.stderr)
        print("", file=sys.stderr)
        print("  This diff was NOT scanned by Haiku. The regex check still ran,", file=sys.stderr)
        print("  but the context-aware pass is the one that catches names the", file=sys.stderr)
        print("  patterns do not know about. Raise the cap for this push with", file=sys.stderr)
        print("  SCRUB_HAIKU_DAILY_USD=<n>, or review the diff yourself.", file=sys.stderr)
        print("=" * 72, file=sys.stderr)
        print("", file=sys.stderr)
        return 1 if blocking else 0

    rc = call_haiku(diff, range_spec)
    if rc == 2:
        # Setup / API error — don't block the push. Regex check already passed.
        print(
            "[scrub-haiku] SCAN DID NOT RUN — Haiku check unavailable; "
            "relying on regex check only. This is not a clean verdict.",
            file=sys.stderr,
        )
        return 0
    return rc


if __name__ == "__main__":
    sys.exit(main())
