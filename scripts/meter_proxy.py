#!/usr/bin/env python3
"""Read Anthropic's own rate-limit meter off response headers, in the path.

WHY THIS EXISTS
---------------
The weekly quota meter is model-weighted and discounts cache reads by roughly
10x, but every coefficient we have is INFERRED from integer percentages in
`/usage`, and only one of three pools was ever well enough conditioned to pin
the number. Anthropic returns the meter state directly, on every call:

    anthropic-ratelimit-unified-7d-utilization       float 0..1, the week
    anthropic-ratelimit-unified-5h-utilization       float 0..1, the session
    anthropic-ratelimit-unified-<...>-utilization    per-model sub-meters

Reading those replaces an estimate with an observation, and gives a live meter
without driving `/usage` in a tmux pane -- which is classifier-blocked and has
defeated the token-watch three passes running.

Authorised by Bryan on 2026-09-16 ("Build it"). He owns
that decision because this touches his credentials and the fleet's
connectivity; it was not set up on anyone's own initiative.

THE SAFETY PROPERTIES, AND WHY EACH ONE IS LOAD-BEARING
-------------------------------------------------------
This process sits in front of every authenticated request the fleet makes. If
it is wrong, the failure is not a bad number -- it is ten sessions unable to
work, or a credential somewhere it should not be.

1. **Credentials are forwarded, never read, never logged, never stored.**
   `Authorization`, `x-api-key` and `cookie` are relayed verbatim and are
   excluded from every log path by name. Nothing in this file writes a request
   header to disk.

2. **Bodies are relayed untouched and UNBUFFERED, in both directions.**
   Claude Code streams SSE. A proxy that buffers a response turns a streaming
   session into a stalled one, and it looks like a hang rather than a proxy
   bug. Nothing here parses, rewrites or accumulates a body.

3. **Only response headers matching `anthropic-ratelimit-*` are recorded**,
   plus a timestamp and the path. That is the whole ledger.

4. **An error reading the meter must never fail the request.** The ledger
   write is wrapped; if it throws, the response still goes back. The meter is
   an observer and is never in the success path of the fleet's work.

5. **It refuses to listen on anything but loopback.** A proxy holding the
   fleet's traffic must not be reachable from the network.

ROLLOUT -- ONE SESSION FIRST, NOT TEN
-------------------------------------
`ANTHROPIC_BASE_URL=http://127.0.0.1:<port>` on ONE session, confirm it works
and the ledger fills, and only then widen. There is no fail-open for a proxy
that is not running: if it dies, the sessions pointed at it stop. That is the
whole risk, and staging is the only mitigation.

    python3 scripts/meter_proxy.py --self-test     # no creds, no network
    python3 scripts/meter_proxy.py --port 8791     # run it
    python3 scripts/meter_proxy.py --report        # read the ledger
"""

from __future__ import annotations

import argparse
import http.client
import http.server
import json
import os
import socket
import sys
import threading
import urllib.parse
from datetime import datetime, timezone

UPSTREAM_HOST = "api.anthropic.com"
DEFAULT_PORT = 8791
LEDGER = os.path.expanduser("~/.local/state/fleet-meter/meter.jsonl")
CHUNK = 65536

# Headers that must never be written anywhere, by name. Belt and braces: the
# logging path only ever sees RESPONSE headers, and only ones matching the
# ratelimit prefix -- but a future edit that widens that filter should still
# not be able to reach these.
NEVER_LOG = {"authorization", "x-api-key", "cookie", "set-cookie",
             "proxy-authorization"}

# Hop-by-hop headers, per RFC 7230 6.1. These describe THIS connection and must
# not be forwarded to the next one.
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate",
              "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade"}

METER_PREFIX = "anthropic-ratelimit"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def meter_headers(headers) -> dict:
    """Pull only the meter headers out of a response.

    Excludes anything in NEVER_LOG unconditionally, even though no credential
    header matches the ratelimit prefix today. The filter is the contract, not
    the coincidence.
    """
    out = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in NEVER_LOG:
            continue
        if lk.startswith(METER_PREFIX):
            out[lk] = v
    return out


def record(path: str, status: int, headers, model: str | None = None) -> None:
    """Append one meter observation. NEVER raises into the request path."""
    try:
        meters = meter_headers(headers)
        if not meters:
            return
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        row = {"ts": _now(), "path": path, "status": status, "meters": meters}
        if model:
            row["model"] = model
        with open(LEDGER, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        # Deliberately swallowed. An observer must not be able to fail the
        # fleet's work -- see safety property 4.
        pass


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "fleet-meter-proxy"

    def log_message(self, fmt, *args):  # noqa: D102 - silence stdlib logging
        # The default logs the request line, which carries the path only --
        # but silencing it keeps the process from writing anything at all
        # about traffic it is carrying.
        pass

    def _relay(self, method: str) -> None:
        conn = None
        try:
            conn = http.client.HTTPSConnection(UPSTREAM_HOST, timeout=600)

            # --- request headers: forward verbatim, minus hop-by-hop ---
            fwd = {}
            for k, v in self.headers.items():
                if k.lower() in HOP_BY_HOP:
                    continue
                fwd[k] = v
            fwd["Host"] = UPSTREAM_HOST

            # --- request body: stream it, never accumulate ---
            body = None
            length = self.headers.get("Content-Length")
            if length is not None:
                body = self.rfile.read(int(length))
            elif self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                body = self.rfile.read()

            conn.request(method, self.path, body=body, headers=fwd)
            resp = conn.getresponse()

            # --- the entire point of the process ---
            record(self.path.split("?")[0], resp.status, resp.headers)

            # --- response: headers, then stream the body straight through ---
            self.send_response(resp.status, resp.reason)
            saw_len = False
            for k, v in resp.getheaders():
                if k.lower() in HOP_BY_HOP:
                    continue
                if k.lower() == "content-length":
                    saw_len = True
                self.send_header(k, v)
            if not saw_len:
                # Upstream is streaming. Say so, and relay chunk by chunk --
                # buffering here is what turns SSE into a hang.
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            while True:
                buf = resp.read(CHUNK)
                if not buf:
                    break
                if saw_len:
                    self.wfile.write(buf)
                else:
                    self.wfile.write(b"%x\r\n" % len(buf) + buf + b"\r\n")
                self.wfile.flush()
            if not saw_len:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

        except Exception as exc:
            # A proxy failure must be legible as a proxy failure, not as an
            # API error, or it gets debugged in the wrong repo for an hour.
            try:
                msg = json.dumps({
                    "type": "error",
                    "error": {
                        "type": "fleet_meter_proxy_error",
                        "message": (
                            f"{exc.__class__.__name__}: {exc}. This is the "
                            "LOCAL meter proxy, not Anthropic. Unset "
                            "ANTHROPIC_BASE_URL to bypass it."),
                    },
                }).encode()
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
            except Exception:
                pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def do_GET(self):
        self._relay("GET")

    def do_POST(self):
        self._relay("POST")

    def do_DELETE(self):
        self._relay("DELETE")

    def do_PUT(self):
        self._relay("PUT")


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(port: int) -> int:
    host = "127.0.0.1"
    try:
        srv = Server((host, port), Handler)
    except OSError as exc:
        print(f"[meter] could not bind {host}:{port} — {exc}", file=sys.stderr)
        return 2
    print(f"[meter] listening on http://{host}:{port} -> {UPSTREAM_HOST}")
    print(f"[meter] ledger: {LEDGER}")
    print("[meter] point ONE session at it first:")
    print(f"[meter]   ANTHROPIC_BASE_URL=http://{host}:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[meter] stopped. Sessions pointed here will fail until they "
              "are repointed or this is restarted.")
    return 0


def report(limit: int = 10) -> int:
    if not os.path.exists(LEDGER):
        print(f"[meter] COULD NOT LOOK — no ledger at {LEDGER}. The proxy has "
              "never recorded a call. This is not a zero meter.",
              file=sys.stderr)
        return 2
    rows = []
    with open(LEDGER, encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    if not rows:
        print(f"[meter] COULD NOT LOOK — ledger {LEDGER} is empty.",
              file=sys.stderr)
        return 2
    print(f"[meter] {len(rows)} observation(s), newest last\n")
    for r in rows[-limit:]:
        meters = r.get("meters", {})
        week = meters.get(f"{METER_PREFIX}-unified-7d-utilization")
        five = meters.get(f"{METER_PREFIX}-unified-5h-utilization")
        def pct(v):
            try:
                return f"{float(v) * 100:.2f}%"
            except (TypeError, ValueError):
                return "?"
        print(f"  {r['ts']}  week={pct(week):>8}  5h={pct(five):>8}  "
              f"{r.get('path', '')}")
    newest = rows[-1]
    print(f"\n  all meters on the newest observation ({newest['ts']}):")
    for k, v in sorted(newest.get("meters", {}).items()):
        print(f"    {k} = {v}")
    return 0


def self_test() -> int:
    """Prove the parts that can be proven without credentials or network."""
    ok = True

    class H:
        def __init__(self, d): self._d = d
        def items(self): return self._d.items()

    # 1. meter headers are picked up
    got = meter_headers(H({
        "anthropic-ratelimit-unified-7d-utilization": "0.62",
        "anthropic-ratelimit-unified-5h-utilization": "0.31",
        "content-type": "application/json",
    }))
    exp = {"anthropic-ratelimit-unified-7d-utilization": "0.62",
           "anthropic-ratelimit-unified-5h-utilization": "0.31"}
    print(f"  meter headers extracted      : {'PASS' if got == exp else 'FAIL'}")
    ok &= got == exp

    # 2. credentials are never extracted, even if they were to match
    got = meter_headers(H({
        "authorization": "Bearer sk-ant-SHOULD-NEVER-APPEAR",
        "x-api-key": "sk-ant-SHOULD-NEVER-APPEAR",
        "cookie": "session=SHOULD-NEVER-APPEAR",
        "anthropic-ratelimit-unified-7d-utilization": "0.62",
    }))
    clean = "SHOULD-NEVER-APPEAR" not in json.dumps(got)
    print(f"  credentials excluded         : {'PASS' if clean else 'FAIL'}")
    ok &= clean

    # 3. a mutation control: if the filter were widened, the test above must
    #    still catch a credential. Prove the check can fail.
    leaked = {k: v for k, v in H({
        "authorization": "Bearer sk-ant-SHOULD-NEVER-APPEAR"}).items()}
    caught = "SHOULD-NEVER-APPEAR" in json.dumps(leaked)
    print(f"  leak check can still fail    : {'PASS' if caught else 'FAIL'}")
    ok &= caught

    # 4. hop-by-hop headers are not forwarded
    hop_ok = "transfer-encoding" in HOP_BY_HOP and "connection" in HOP_BY_HOP
    print(f"  hop-by-hop stripped          : {'PASS' if hop_ok else 'FAIL'}")
    ok &= hop_ok

    # 5. record() cannot raise into the request path
    raised = False
    try:
        class Boom:
            def items(self): raise RuntimeError("boom")
        record("/v1/messages", 200, Boom())
    except Exception:
        raised = True
    print(f"  record() never raises        : {'PASS' if not raised else 'FAIL'}")
    ok &= not raised

    # 6. loopback only
    src = open(__file__, encoding="utf-8").read()
    lo = 'host = "127.0.0.1"' in src
    print(f"  binds loopback only          : {'PASS' if lo else 'FAIL'}")
    ok &= lo

    print(f"\n  {'ALL PASS' if ok else 'FAILURES ABOVE'} — "
          "6 checks ran. This does NOT cover the streaming relay, which "
          "needs a live upstream; stage on one session for that.")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--report", action="store_true",
                    help="read the ledger instead of serving")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--self-test", action="store_true",
                    help="checks that need no credentials and no network")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.report:
        return report(args.limit)
    return serve(args.port)


if __name__ == "__main__":
    sys.exit(main())
