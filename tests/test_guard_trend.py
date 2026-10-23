"""Trend, paging, cause and de-duplication in fleet_guard.

On 2026-10-22 swap climbed ~14GB to ~16GB over three hours while load per core
stayed 0.4-0.7, so every point-in-time alert read "high swap, low load, wait"
until the machine stalled with Chrome renderers holding ~11GB. These tests feed
pure functions canned metrics; nothing here touches the machine.
"""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "guard", os.path.join(os.path.dirname(__file__), "..", "scripts",
                          "fleet_guard.py"))
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

NOW = 1_000_000.0


def history(swaps, step=600):
    """Rows ending at NOW, oldest first, one every `step` seconds."""
    n = len(swaps)
    return [{"epoch": NOW - (n - 1 - i) * step, "swap_gb": g}
            for i, g in enumerate(swaps)]


def test_rising_swap_with_low_load_raises_trend_alert():
    rows = history([14.0, 14.2, 14.5, 14.8, 15.0, 15.3, 15.6, 15.8, 16.0,
                    16.1, 16.1, 16.1], step=600)       # 14 -> 16.1 over 110 min
    m = {"swap_gb": 16.1, "free_pct": 40, "load_per_core": 0.5,
         "orphan_workers": 0, "anon_gb": 5, "boot_free_gb": 90,
         "healthcheck_age_min": 5}
    trend = guard.swap_trend(rows, 16.1, NOW)
    assert trend["band"] == "warn"
    assert "swap +2.1GB in" in trend["text"] and "rising" in trend["text"]
    _, bands = guard.grade(m, trend, {"band": "ok"}, [])
    assert bands["swap trend"] == "warn"
    assert bands["load"] == "ok"


def test_flat_or_falling_swap_is_not_a_trend_alert():
    flat = guard.swap_trend(history([16.0] * 12), 16.0, NOW)
    assert flat["band"] == "ok" and "flat" in flat["text"]
    falling = guard.swap_trend(history([20, 19, 18, 17.5, 17, 16.6, 16.2, 16, 15.5,
                                        15, 14.5, 14]), 14, NOW)
    assert falling["band"] == "ok"


def test_short_history_is_unknown_not_flat():
    t = guard.swap_trend(history([14.0, 14.1], step=300), 14.1, NOW)
    assert t["band"] == "unknown" and "history" in t["text"]
    assert guard.swap_trend([], 14.0, NOW)["band"] == "unknown"
    assert guard.swap_trend(history([14] * 12), None, NOW)["band"] == "unknown"


def test_paging_rate_high_even_when_load_is_low():
    prev = {"pageins": 1000, "pageouts": 0, "epoch": NOW - 120, "pagesize": 16384}
    cur = {"pageins": 1000 + 1_500_000, "pageouts": 0, "epoch": NOW,
           "pagesize": 16384}                 # 1.5M pages * 16K / 120s = ~195MB/s
    p = guard.paging_rate(prev, cur)
    assert p["band"] == "critical" and p["in_mb_s"] > 150


def test_paging_rate_unknown_states_are_loud():
    cur = {"pageins": 5, "pageouts": 5, "epoch": NOW, "pagesize": 16384}
    assert guard.paging_rate(None, cur)["band"] == "unknown"
    stale = dict(cur, epoch=NOW - 3600)
    assert "stale" in guard.paging_rate(stale, cur)["text"]
    older = dict(cur, pageins=99, epoch=NOW - 120)
    assert "reset" in guard.paging_rate(older, cur)["text"]
    assert "vm_stat" in guard.paging_rate(cur, {"pageins": None})["text"]


PS = """\
  10 bryan    2097152 1:00.00 10:00 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
  11 bryan    3145728 5:00.00 10:00 /x/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)
  12 bryan    3145728 5:00.00 10:00 /x/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)
  13 bryan    1048576 5:00.00 10:00 /x/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper
  20 bryan    4194304 9:00.00 10:00 /usr/local/bin/claude
  30 other    1048576 11:00:00.00 1-00:00:00 /x/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)
  31 other    1048576 11:00:00.00 20:00:00 /x/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)
  32 other    1048576 11:00:00.00 3-00:00:00 /x/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)
"""


def test_chrome_top_holder_is_named_with_total_and_renderer_count():
    rows = guard.parse_process_table(PS)
    c = guard.chrome_cause(rows, "bryan")
    assert c == {"gb": 9.0, "renderers": 2}
    text = " ".join(guard.describe_cause(c, []))
    assert "Chrome is the top memory holder: 9.0GB across 2 renderers" in text
    assert "Memory Saver" in text


def test_renderer_name_variants():
    assert guard.is_chrome_renderer("/x/Google Chrome Helper (Aperitif Renderer)")
    assert not guard.is_chrome_renderer("/x/Google Chrome Helper (GPU)")


def test_chrome_not_top_holder_names_nothing():
    rows = guard.parse_process_table(PS.replace("4194304", "40194304"))
    assert guard.chrome_cause(rows, "bryan") is None


def test_runaway_renderer_flagged_with_user_and_pid():
    rows = guard.parse_process_table(PS)
    r = guard.find_runaway_renderers(rows)
    # pid 30: 11h CPU / 24h = 46% -> below the line. pid 31: 11h / 20h = 55%.
    # pid 32: 11h / 72h = 15%. bryan's renderers have 5 minutes.
    assert [(x["pid"], x["user"]) for x in r] == [(31, "other")]
    assert "runaway tab" in guard.describe_cause(None, r)[0]
    assert "pid 31" in guard.describe_cause(None, r)[0]


def test_unchanged_alert_is_suppressed_then_resent_after_interval():
    bands = {"swap trend": "warn", "load": "ok"}
    trend = {"band": "warn", "rise_gb": 2.1}
    sig = guard.alert_signature("warn", bands, trend, None, [])
    prev = {"band": "warn", "notified_sig": sig, "notified_at": NOW - 1800}
    assert guard.should_notify(prev, "warn", sig, NOW) is None
    prev["notified_at"] = NOW - guard.RENOTIFY_UNCHANGED_SEC - 1
    assert guard.should_notify(prev, "warn", sig, NOW) == "unchanged-resend"


def test_changed_trend_cause_or_band_resends():
    bands = {"swap trend": "warn"}
    sig = guard.alert_signature("warn", bands, {"band": "warn", "rise_gb": 2.1},
                                None, [])
    prev = {"band": "warn", "notified_sig": sig, "notified_at": NOW - 60}
    worse = guard.alert_signature("warn", bands, {"band": "warn", "rise_gb": 3.2},
                                  None, [])
    assert guard.should_notify(prev, "warn", worse, NOW) == "changed"
    cause = guard.alert_signature("warn", bands, {"band": "warn", "rise_gb": 2.4},
                                  {"gb": 9, "renderers": 2}, [])
    assert guard.should_notify(prev, "warn", cause, NOW) == "changed"
    assert guard.should_notify(prev, "critical", sig, NOW) == "escalated"
    assert guard.should_notify(prev, "ok", sig, NOW) == "recovered"
    assert guard.should_notify({"band": "ok"}, "ok", sig, NOW) is None


def test_a_fact_dropping_out_does_not_resend():
    chrome = {"gb": 4.7, "renderers": 16}
    full = guard.alert_signature("warn", {"swap": "warn", "paging": "warn"},
                                 {"band": "warn", "rise_gb": 2.3}, chrome, [])
    prev = {"band": "warn", "notified_sig": full, "notified_at": NOW - 120}
    # Chrome loses first place and the paging band clears: quieter, not news.
    less = guard.alert_signature("warn", {"swap": "warn", "paging": "ok"},
                                 {"band": "warn", "rise_gb": 2.3}, None, [])
    assert guard.should_notify(prev, "warn", less, NOW) is None
    # Chrome back on top matches what was last sent: still quiet.
    assert guard.should_notify(prev, "warn", full, NOW) is None
