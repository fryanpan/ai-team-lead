"""The session-limit scan streams transcripts and still finds a rejection.

It used to read each transcript whole, which peaked near 1.5GB on an hourly
run. The streamed version must still find a rejection line that straddles a
1MB chunk boundary and one on a final line with no trailing newline.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "fleet_healthcheck_rl", os.path.join(ROOT, "scripts", "fleet_healthcheck.py"))
hc = importlib.util.module_from_spec(_spec)
sys.modules["fleet_healthcheck_rl"] = hc
_spec.loader.exec_module(hc)

BUN = shutil.which("bun") or os.path.expanduser(
    "~/Library/Application Support/claude-workspaces/bin/bun")
pytestmark = pytest.mark.skipif(not os.path.exists(BUN), reason="no bun")


def rejection(ts_ms):
    iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts_ms / 1000))
    return json.dumps({"timestamp": iso, "message": {"quotaLimits": {
        "rateLimitType": "five_hour", "status": "rejected", "resetsAt": 1}}},
        separators=(",", ":"))


def scan(root, since_ms):
    r = subprocess.run([BUN, "-e", hc._RATE_LIMIT_JS, "--", str(root), str(since_ms)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_finds_hits_across_chunk_boundary_and_unterminated_tail(tmp_path):
    now = int(time.time() * 1000)
    proj = tmp_path / "projects" / "-some-project"
    proj.mkdir(parents=True)
    filler = json.dumps({"x": "é" * 700_000})       # > 1MB of UTF-8 on one line
    lines = [filler, rejection(now - 1000), json.dumps({"ok": 1}), rejection(now - 500)]
    (proj / "a.jsonl").write_text("\n".join(lines), encoding="utf-8")   # no trailing \n
    hits = scan(tmp_path / "projects", now - 3600_000)
    assert len(hits) == 2
    assert all(h["project"] == "-some-project" for h in hits)


def test_ignores_old_and_non_rejected(tmp_path):
    now = int(time.time() * 1000)
    proj = tmp_path / "projects" / "p"
    proj.mkdir(parents=True)
    allowed = json.dumps({"timestamp": "2026-10-14T00:00:00Z", "message": {"quotaLimits": {
        "rateLimitType": "five_hour", "status": "allowed"}}}, separators=(",", ":"))
    (proj / "b.jsonl").write_text(rejection(now - 7200_000) + "\n" + allowed + "\n")
    assert scan(tmp_path / "projects", now - 3600_000) == []
