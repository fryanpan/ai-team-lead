#!/usr/bin/env python3
"""True metered API spend, in real dollars, from the Admin API.

The other half of the spend story. `scrub-haiku.py` records what the leak gate
ESTIMATES each of its own calls cost, which is the right instrument for capping
one script and the wrong one for answering "what did the fleet actually spend".
It cannot see any other key, and an estimate is not a bill.

This reads the org's cost report instead: real dollars, as Anthropic charges
them, across every key in the organization.

WHY IT IS BY WORKSPACE AND NOT BY KEY
-------------------------------------
Per-key dollars do not exist. The usage report breaks tokens down by API key;
the cost report gives money and can only group by `workspace_id` or
`description`. Multiplying per-key tokens by published rates gets you back to
an estimate, which is what we already had.

So the fleet's keys live in their own Console workspace, and the workspace is
the unit of attribution. That is a real constraint of the API, not a design
preference: put a fleet key in the default workspace and its spend is
permanently indistinguishable from everything else there.

THE THREE STATES
----------------
A spend check that cannot tell "looked, found nothing" from "could not look"
will eventually be read as the first when it meant the second — the failure
that let a real name reach a public repo through an exhausted leak gate, and
the reason a day's runaway spend on one key goes unnoticed until the invoice.

  exit 0  RAN, under the line
  exit 1  RAN, over the line — a real finding
  exit 2  COULD NOT LOOK — no key, bad key, network, API error

Never exits 0 on a failure to look. A dollar figure is only printed when one
was actually fetched.

THE CENTS TRAP
--------------
`amount` is "in lowest currency units (e.g. cents) as a decimal string" — the
docs' own example is "123.45" meaning $1.23. Read it as dollars and every
number is 100x too big, which in this script's case is the difference between
"fine" and "call someone". `cents_to_dollars` exists to be the single place
that conversion happens, and it is tested.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

API_ROOT = "https://api.anthropic.com/v1/organizations"
ANTHROPIC_VERSION = "2023-06-01"
HTTP_TIMEOUT_SEC = 30

# Written by the Workspaces secret card. The store namespaces every name under
# this prefix and account, and base64-encodes the value — a raw read returns
# base64, not the key, and that decodes to something the API answers 401 to.
SECRET_ACCOUNT = "claude-workspaces"
SECRET_PREFIX = "claude-workspaces-secret."
ADMIN_KEY_SERVICE = "fleet-anthropic-admin-key"

# How the fleet's workspace is recognised. A SUBSTRING, case-insensitively,
# not an exact name: the card asked for a workspace called `fleet` and the one
# that exists has a possessive prefix. An exact match silently attributed
# every charge in it to nobody and reported the fleet's spend as $0.00 while
# printing that same workspace's charges one line above — the failure this
# whole script exists to avoid, reproduced inside it on the first run.
FLEET_WORKSPACE_MATCH = "fleet"

# The fleet rule: over $50 of API spend needs explicit approval. Monthly,
# because that is the horizon the rule is written against.
DEFAULT_MONTH_USD = 50.00


class CouldNotLook(Exception):
    """Raised for every 'the check did not run' path, so none can exit 0."""


# ---------------------------------------------------------------- credentials


def read_admin_key() -> str:
    """The admin key, from the Workspaces secret store.

    Never returned to a caller that prints it, never logged, never written.
    Raises CouldNotLook rather than returning None so a missing key cannot be
    mistaken for a zero bill.
    """
    service = SECRET_PREFIX + ADMIN_KEY_SERVICE
    try:
        proc = subprocess.run(
            ["security", "find-generic-password",
             "-a", SECRET_ACCOUNT, "-s", service, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CouldNotLook(f"could not run `security`: {exc}") from exc
    if proc.returncode != 0:
        raise CouldNotLook(
            f"no admin key in the Keychain under `{service}`. File a secret "
            "card asking for one; it cannot be read from anywhere else.")
    raw = proc.stdout.strip()
    if not raw:
        raise CouldNotLook(f"the Keychain entry `{service}` is empty.")
    try:
        key = base64.b64decode(raw, validate=True).decode("utf-8").strip()
    except (ValueError, UnicodeDecodeError) as exc:
        raise CouldNotLook(
            f"the value in `{service}` is not valid base64, so it is not a key "
            "this store wrote. Re-save it through the secret card.") from exc
    if not key:
        raise CouldNotLook(f"the value in `{service}` decodes to nothing.")
    if not key.startswith("sk-ant-"):
        # Shape only. Length is deliberately not pinned — see scrub-haiku.py.
        raise CouldNotLook(
            f"the value in `{service}` does not look like an Anthropic key. "
            "Re-mint it in the Console and re-save it through the card.")
    return key


# ------------------------------------------------------------------ http


def api_get(path: str, key: str, params: Optional[dict] = None) -> dict:
    url = f"{API_ROOT}/{path}"
    if params:
        flat = []
        for k, v in params.items():
            if isinstance(v, (list, tuple)):
                flat.extend((k, item) for item in v)
            elif v is not None:
                flat.append((k, v))
        url += "?" + urllib.parse.urlencode(flat)
    req = urllib.request.Request(url, headers={
        "anthropic-version": ANTHROPIC_VERSION,
        "X-Api-Key": key,
        "accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        if exc.code == 401:
            raise CouldNotLook(
                "the admin key was rejected (401). It is an ADMIN key that is "
                "needed here, not a regular API key — a workspace key gets a "
                "401 on this endpoint. Check it in Console → Settings → Admin "
                "keys.") from exc
        if exc.code == 403:
            raise CouldNotLook(
                "the admin key is not permitted to read the cost report (403). "
                "Only an org owner's admin key can.") from exc
        raise CouldNotLook(f"HTTP {exc.code} from {path}: {body}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CouldNotLook(f"could not reach the Admin API: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CouldNotLook(f"the Admin API returned non-JSON: {exc}") from exc


# ------------------------------------------------------------------ money


def money(dollars: float) -> str:
    """Render dollars so a MEASURED near-zero never looks like no data.

    $0.0036 formatted at two decimals is "$0.00", which reads exactly like a
    workspace with no charges. Small real amounts keep enough digits to be
    visibly nonzero; a true zero stays "$0.00".
    """
    if dollars == 0:
        return "$0.00"
    if abs(dollars) < 0.01:
        return f"${dollars:.4f}"
    return f"${dollars:,.2f}"


def cents_to_dollars(amount: str) -> float:
    """The ONE place the cost report's units are interpreted.

    `amount` is a decimal string in the lowest currency unit. For USD that is
    cents, so the docs' "123.45" is $1.23. Every figure this script prints
    passes through here; read the field as dollars anywhere else and the report
    is 100x high.
    """
    return float(amount) / 100.0


# ------------------------------------------------------------------ reads


def workspace_names(key: str) -> dict:
    """{workspace_id: name}, following pagination."""
    names, after = {}, None
    while True:
        page = api_get("workspaces", key, {
            "limit": 1000, "include_archived": "true", "after_id": after})
        for ws in page.get("data", []):
            if ws.get("id"):
                names[ws["id"]] = ws.get("name") or ws["id"]
        if not page.get("has_more"):
            return names
        after = page.get("last_id")
        if not after:
            return names


def cost_by_workspace(key: str, start: datetime, end: datetime) -> dict:
    """{workspace_id or None: dollars} over the window, following pagination.

    A `workspace_id` of None is the DEFAULT workspace, not missing data — the
    field is documented as null both when you are not grouping by workspace and
    for the default one. Since we always group, None here means default.
    """
    totals, page_token, guard = {}, None, 0
    while True:
        guard += 1
        if guard > 50:
            raise CouldNotLook("cost report paginated past 50 pages; refusing "
                               "to loop. Narrow the window with --days.")
        page = api_get("cost_report", key, {
            "starting_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ending_at": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bucket_width": "1d",
            "group_by[]": ["workspace_id"],
            "limit": 31,
            "page": page_token,
        })
        for bucket in page.get("data", []):
            for item in bucket.get("results", []):
                if (item.get("currency") or "USD") != "USD":
                    raise CouldNotLook(
                        f"cost reported in {item.get('currency')}, and this "
                        "script only knows USD.")
                wid = item.get("workspace_id")
                totals[wid] = totals.get(wid, 0.0) + cents_to_dollars(
                    item.get("amount") or "0")
        if not page.get("has_more"):
            return totals
        page_token = page.get("next_page")
        if not page_token:
            return totals


# ------------------------------------------------------------------ report


def matches_fleet_workspace(label: Optional[str], want: str) -> bool:
    """Whether a workspace label is the fleet's.

    A SUBSTRING match, case-insensitively — see FLEET_WORKSPACE_MATCH for why
    exact matching silently reported the fleet's spend as $0.00.
    """
    if not label:
        return False
    return want.strip().lower() in label.lower()


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="True metered API spend from the Admin API, in dollars.")
    ap.add_argument("--days", type=int, default=30,
                    help="window ending now (default 30)")
    ap.add_argument("--limit-usd", type=float, default=DEFAULT_MONTH_USD,
                    help=f"line that makes this exit 1 (default "
                         f"{DEFAULT_MONTH_USD:.2f}, the fleet rule)")
    ap.add_argument("--fleet-workspace", default=FLEET_WORKSPACE_MATCH,
                    help="case-insensitive substring identifying the fleet's "
                         f"workspace (default {FLEET_WORKSPACE_MATCH!r})")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    args = ap.parse_args(argv)

    if args.days < 1:
        print("[spend] --days must be at least 1", file=sys.stderr)
        return 2

    end = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    start = end - timedelta(days=args.days)

    try:
        key = read_admin_key()
        names = workspace_names(key)
        totals = cost_by_workspace(key, start, end)
    except CouldNotLook as exc:
        # The whole point. This is NOT $0.00.
        print(f"[spend] COULD NOT LOOK — {exc}", file=sys.stderr)
        print("[spend] This is not a zero. Nothing was measured.",
              file=sys.stderr)
        return 2

    rows = []
    for wid, dollars in totals.items():
        label = "(default workspace)" if wid is None else names.get(wid, wid)
        rows.append((label, dollars,
                     matches_fleet_workspace(label, args.fleet_workspace)))
    rows.sort(key=lambda r: -r[1])

    total = sum(r[1] for r in rows)
    fleet = sum(r[1] for r in rows if r[2])

    if args.json:
        print(json.dumps({
            "ran": True,
            "window_days": args.days,
            "starting_at": start.isoformat(),
            "ending_at": end.isoformat(),
            "total_usd": round(total, 4),
            "fleet_usd": round(fleet, 4),
            "limit_usd": args.limit_usd,
            "over_limit": total > args.limit_usd,
            "by_workspace": [
                {"workspace": r[0], "usd": round(r[1], 4), "is_fleet": r[2]}
                for r in rows
            ],
        }, indent=2))
    else:
        print(f"[spend] RAN — real dollars from the Admin API, "
              f"{args.days}d to {end.date()}")
        if not rows:
            print("[spend]   no charges in the window (a measured zero)")
        for label, dollars, is_fleet in rows:
            mark = " <- fleet" if is_fleet else ""
            print(f"[spend]   {money(dollars):>10}  {label}{mark}")
        print(f"[spend]   {money(total):>10}  TOTAL  (fleet workspace "
              f"{money(fleet)})")
        if not any(r[2] for r in rows):
            print(f"[spend] note: nothing matched `{args.fleet_workspace}`, so "
                  "no charge here is attributed to the fleet. If the workspace "
                  "is named differently, pass --fleet-workspace.")
        print("[spend] covers keys in THIS organization only. A key belonging "
              "to another account spends where this report cannot see it.")

    if total > args.limit_usd:
        print(f"[spend] OVER THE LINE — ${total:,.2f} in {args.days}d against "
              f"a ${args.limit_usd:,.2f} limit. The fleet rule is that spend "
              f"past this needs explicit approval.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
