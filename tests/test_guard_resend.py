"""When fleet_guard sends an alert, and when it stays quiet.

Each alert wakes an agent session that pays its whole context to read it. On
2026-11-13 nine alerts arrived in 33 minutes while swap wobbled around its
critical line and page-ins (with no page-outs) wobbled around theirs; free
memory was 31-40% throughout and none of the nine needed action. These tests
replay those readings and pin the rules that keep a wobble quiet without
hiding a real escalation. Pure functions and canned readings only.
"""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "guard", os.path.join(os.path.dirname(__file__), "..", "scripts",
                          "fleet_guard.py"))
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

T0 = 1_000_000.0
MIN = 60.0
PAGE = 16384
SENDS = ("escalated", "changed", "unchanged-resend")


class Machine:
    """Feeds readings through guard.assess the way main() does, carrying the
    saved state from one run to the next."""

    def __init__(self):
        self.state = {}
        self.ins = self.outs = 0
        self.at = None

    def run(self, minute, swap=3.0, trend="ok", rise=0.0, pin=0.0, pout=0.0,
            load=0.4, free=35, chrome=None, runaways=(), paging_known=True,
            causes_known=True):
        now = T0 + minute * MIN
        dt = 120.0 if self.at is None else now - self.at
        self.ins += int(pin * 1024 ** 2 * dt / PAGE)
        self.outs += int(pout * 1024 ** 2 * dt / PAGE)
        self.at = now
        m = {"swap_gb": swap, "free_pct": free, "load_per_core": load,
             "orphan_workers": 0, "anon_gb": 5.0, "boot_free_gb": 90.0,
             "healthcheck_age_min": 5.0, "pagesize": PAGE, "epoch": now,
             "pageins": self.ins if paging_known else None,
             "pageouts": self.outs if paging_known else None}
        paging = guard.paging_rate(self.state.get("metrics"), m)
        tr = {"band": trend, "rise_gb": rise, "text": f"swap {rise:+.1f}GB"}
        v = guard.assess(self.state, m, tr, paging, chrome, list(runaways), now,
                         causes_known=causes_known)
        self.state = dict(v["memory"], bands=v["bands"], band=v["worst"],
                          metrics=m)
        self.paging = paging
        return v


def primed():
    """A machine with one quiet run behind it, so the paging rate is known."""
    mc = Machine()
    assert mc.run(-2)["reason"] is None
    return mc


# The nine alerts of 2026-11-13, as (minute after 10:00, readings). Swap and
# paging figures are the ones the alerts printed; where an alert did not print
# swap, a value between its neighbours is used. Every one of them was sent.
NINE = [
    (17, dict(swap=12.56, pin=20)),                           # swap critical
    (21, dict(swap=10.91, pin=130)),                          # paging warn
    (25, dict(swap=11.2, pin=193)),                           # paging critical
    (31, dict(swap=11.0, pin=130)),                           # paging warn
    (35, dict(swap=11.3, pin=171)),                           # paging critical
    (38, dict(swap=11.6, pin=201, pout=2, load=1.3)),         # + load warn
    (44, dict(swap=12.41, trend="warn", rise=1.6, pin=20)),   # swap critical, trend warn
    (48, dict(swap=11.93, pin=20, load=1.3)),                 # load warn
    (50, dict(swap=12.7, pin=20)),                            # swap critical
]


def replay(readings):
    mc = primed()
    sent = []
    for minute, kw in readings:
        v = mc.run(minute, **kw)
        if v["reason"] in SENDS:
            sent.append((minute, v["reason"], sorted(guard.alert_items(
                v["bands"], None, []))))
    return sent


def test_the_nine_readings_send_three_alerts():
    """Nine were sent; three are news, one per cause that had not been
    reported: swap critical (10:17), load warn (10:38), swap trend warn
    (10:44). The other six were the same causes again inside 30 minutes:
    swap stayed inside the margin of its critical line (never under 10.2GB),
    and page-ins with no page-outs are not paging pressure at all."""
    sent = replay(NINE)
    assert [(m, r) for m, r, _ in sent] == [
        (17, "escalated"), (38, "changed"), (44, "changed")]
    assert sent[0][2] == ["swap"]
    assert "load" in sent[1][2] and "swap trend" in sent[2][2]
    assert not any("paging" in items for _, _, items in sent)


def test_the_nine_readings_with_calm_runs_between_still_send_three():
    """The guard runs every 2 minutes, so there were runs between the nine.
    A run that dipped to all-ok is what re-armed the old rule each time; put
    one between every pair and the count must not move."""
    readings = []
    for minute, kw in NINE:
        readings.append((minute - 1, dict(swap=11.0)))
        readings.append((minute, kw))
    assert [m for m, _, _ in replay(readings)] == [17, 38, 44]


def test_ok_to_critical_sends_at_once():
    mc = primed()
    v = mc.run(0, swap=13.0)
    assert v["reason"] == "escalated" and v["worst"] == "critical"


def test_a_new_cause_inside_the_cap_sends_at_once():
    mc = primed()
    assert mc.run(0, swap=13.0)["reason"] == "escalated"
    assert mc.run(2, swap=13.0)["reason"] is None
    v = mc.run(4, swap=13.0, free=10)                 # memory critical: new
    assert v["reason"] == "changed"
    assert mc.run(6, swap=13.0, free=10)["reason"] is None
    v = mc.run(8, swap=13.0, free=10, chrome={"gb": 9, "renderers": 2})
    assert v["reason"] == "changed"                   # a named cause is new too
    v = mc.run(10, swap=13.0, free=10, runaways=[{"pid": 31}])
    assert v["reason"] == "changed"                   # so is a runaway pid


def test_warn_to_critical_for_the_same_cause_sends_at_once():
    mc = primed()
    assert mc.run(0, load=1.5)["reason"] == "escalated"
    assert mc.run(2, load=3.5)["reason"] == "escalated"


def test_same_cause_wobbling_is_quiet_for_30_minutes():
    """Swap swings well past the margin on both sides of its critical line,
    and all the way to ok. One alert, then nothing inside the cap."""
    mc = primed()
    reasons = [mc.run(minute, swap=13.0 if (minute // 2) % 4 == 0 else 3.0)["reason"]
               for minute in range(0, 30, 2)]
    assert reasons[0] == "escalated"
    assert reasons[1:] == [None] * 14


def test_a_slow_wobble_inside_the_margin_never_resends_early():
    """Swap sits at its critical line for over an hour, 8 minutes above and 8
    below: long enough to outlast the run hold, never clear of the margin. It
    is one standing alert, not a new crossing every half hour."""
    mc = primed()
    reasons = [mc.run(minute, swap=12.5 if (minute // 8) % 2 == 0 else 11.5)["reason"]
               for minute in range(0, 70, 2)]
    assert reasons[0] == "escalated"
    assert reasons[1:] == [None] * 34


def test_a_one_run_dip_past_the_cap_does_not_rearm():
    """Past the 30-minute window, one reading far below the line (clear of any
    margin) is not a fall: the lower band has to hold for DEESCALATE_RUNS."""
    mc = primed()
    assert mc.run(0, load=3.5)["reason"] == "escalated"
    for minute in range(2, 50, 2):
        load = 0.3 if minute == 34 else 3.5
        assert mc.run(minute, load=load)["reason"] is None, minute


def test_a_rise_after_a_real_fall_sends_again():
    """Reported critical, then below critical for long enough to be past the
    cap: the next rise is an escalation to a band not reported in the last 30
    minutes and sends at once."""
    mc = primed()
    assert mc.run(0, swap=13.0)["reason"] == "escalated"
    for minute in range(2, 40, 2):
        assert mc.run(minute, swap=8.0, trend="unknown")["reason"] is None
    assert mc.run(40, swap=13.0, trend="unknown")["reason"] == "escalated"


def test_recovery_sends_once():
    mc = primed()
    assert mc.run(0, swap=13.0)["reason"] == "escalated"
    reasons = [mc.run(minute)["reason"] for minute in range(2, 40, 2)]
    assert reasons.count("recovered") == 1
    assert set(reasons) == {None, "recovered"}
    # Not while the alert is fresh (a dip between two peaks is not recovery).
    assert reasons.index("recovered") == (30 - 2) // 2
    # And the alert after a recovery is a new one.
    assert mc.run(40, swap=13.0)["reason"] == "escalated"


def test_unknown_is_not_recovery_and_is_never_held():
    mc = primed()
    assert mc.run(0, pin=200, pout=200)["reason"] == "escalated"
    v = mc.run(2, paging_known=False)
    assert v["bands"]["paging"] == "unknown"          # not the held "critical"
    assert "unknown" in mc.paging["text"]
    for minute in range(4, 50, 2):
        assert mc.run(minute, paging_known=False)["reason"] is None
    mc.run(50)                                        # counters back; no rate yet
    assert mc.run(52)["reason"] == "recovered"


def test_trend_flipping_between_rising_and_falling_sends_nothing():
    mc = primed()
    assert mc.run(0, swap=9.0, trend="warn", rise=2.1)["reason"] == "escalated"
    for i, minute in enumerate(range(2, 30, 2)):
        rising = i % 2 == 1
        v = mc.run(minute, swap=9.0, trend="warn" if rising else "ok",
                   rise=2.1 if rising else 1.9)
        assert v["reason"] is None, minute


def test_a_larger_swap_rise_sends_only_after_the_cap():
    mc = primed()
    assert mc.run(0, swap=9.0, trend="warn", rise=2.1)["reason"] == "escalated"
    assert mc.run(10, swap=10.0, trend="warn", rise=3.2)["reason"] is None
    assert mc.run(32, swap=10.2, trend="warn", rise=3.4)["reason"] == "changed"
    assert mc.run(34, swap=10.2, trend="warn", rise=3.4)["reason"] is None


def test_unchanged_alert_is_resent_after_two_hours():
    mc = primed()
    assert mc.run(0, swap=13.0)["reason"] == "escalated"
    reasons = [mc.run(minute, swap=13.0)["reason"] for minute in range(2, 124, 2)]
    assert reasons[:-1] == [None] * 60
    assert reasons[-1] == "unchanged-resend"


def test_state_from_before_the_alert_memory_does_not_resend():
    old = {"band": "critical", "notified_at": T0 - 5 * MIN, "notified_sig": "{}",
           "bands": {"swap": "critical", "load": "ok"}}
    bands = {"swap": "critical", "load": "ok"}
    reason, memory = guard.decide_alert(old, bands, {"band": "ok"}, None, [], T0)
    assert reason is None
    assert memory["alert_open"] and "swap" in memory["reported"]


# --- the two mechanisms under the decision ---------------------------------

def test_a_band_is_left_only_past_the_margin():
    warn, crit = guard.SWAP_GB                        # 6, 12
    assert guard.band(11.9, warn, crit) == "warn"                    # no memory
    assert guard.band(11.9, warn, crit, prev="critical") == "critical"
    assert guard.band(10.3, warn, crit, prev="critical") == "critical"
    assert guard.band(10.1, warn, crit, prev="critical") == "warn"
    assert guard.band(5.5, warn, crit, prev="warn") == "warn"
    assert guard.band(5.0, warn, crit, prev="warn") == "ok"
    assert guard.band(5.5, warn, crit, prev="critical") == "warn"    # one step
    assert guard.band(11.9, warn, crit, prev="warn") == "warn"       # entering: exact
    assert guard.band(12.0, warn, crit, prev="ok") == "critical"
    assert guard.band(None, warn, crit, prev="critical") == "unknown"
    lo_warn, lo_crit = guard.FREE_PCT                 # 25, 12: lower is worse
    assert guard.band(13, lo_warn, lo_crit, False, prev="critical") == "critical"
    assert guard.band(14, lo_warn, lo_crit, False, prev="critical") == "warn"


def test_a_lower_band_must_hold_before_it_counts():
    assert (guard.DEESCALATE_RUNS, guard.LONG_BELOW_RUNS) == (3, 15)
    prev = {"bands": {"load": "critical", "paging": "warn"}}
    lower = {"load": "ok", "paging": "unknown"}
    for run in (1, 2):
        bands, pending = guard.settle_bands(lower, lower, prev)
        assert bands == {"load": "critical", "paging": "unknown"}
        assert pending == {"load": run}
        prev = {"bands": bands, "band_pending": pending}
    bands, pending = guard.settle_bands(lower, lower, prev)
    assert bands["load"] == "ok" and pending == {}
    # One worse reading in the middle starts the count again.
    prev = {"bands": {"load": "critical"}, "band_pending": {"load": 2}}
    bands, pending = guard.settle_bands({"load": "critical"},
                                        {"load": "critical"}, prev)
    assert pending == {}
    # Getting worse is never held.
    worse = {"load": "critical"}
    assert guard.settle_bands(worse, worse,
                              {"bands": {"load": "warn"}})[0]["load"] == "critical"
    # Inside the margin the band is held past 3 runs, but not for ever.
    prev = {"bands": {"swap": "critical"}}
    for run in range(1, 15):
        bands, pending = guard.settle_bands({"swap": "ok"}, {"swap": "critical"}, prev)
        assert bands == {"swap": "critical"}, run
        prev = {"bands": bands, "band_pending": pending}
    assert guard.settle_bands({"swap": "ok"}, {"swap": "critical"},
                              prev)[0] == {"swap": "ok"}


def test_paging_is_rated_on_page_outs_and_prints_both_figures():
    mc = primed()
    mc.run(0, swap=3.0, pin=195)                      # read back: 195 in, 0 out
    assert mc.paging["band"] == "ok"
    assert mc.paging["text"] == "paging 195MB/s in, 0MB/s out"
    assert guard.paging_band(201, 2) == "ok"          # the 10:38 reading
    assert guard.paging_band(400, 4.9) == "ok"
    assert guard.paging_band(195, 5) == "warn"        # ins count 20x the outs
    assert guard.paging_band(195, 8) == "critical"
    assert guard.paging_band(0, 160) == "critical"    # page-outs alone
    assert guard.paging_band(60, 60) == "warn"


# --- cases a wobble rule must not break --------------------------------------

def test_after_a_long_gap_old_bands_are_not_held():
    """Critical, then the machine sleeps for 10 hours and wakes healthy. The
    run hold counts runs, so without a freshness check it would hold critical
    and the 2-hour resend would send an alert about a healthy machine."""
    mc = primed()
    assert mc.run(0, swap=13.0)["reason"] == "escalated"
    v = mc.run(600, swap=1.0)
    assert (v["reason"], v["worst"]) == ("recovered", "ok")


def test_another_cause_sending_does_not_rearm_the_first():
    mc = primed()
    assert mc.run(0, swap=13.0)["reason"] == "escalated"
    for minute in (2, 4, 6):
        assert mc.run(minute, swap=8.0, trend="unknown")["reason"] is None
    assert mc.run(8, swap=8.0, trend="unknown", load=1.5)["reason"] == "changed"
    assert mc.run(10, swap=13.0, load=1.5)["reason"] is None


def test_a_second_episode_is_sent_when_the_cap_ends_not_two_hours_later():
    """Critical, clear for 20 minutes, critical again and staying there. The
    return is inside the 30-minute cap so it waits, but only until the cap."""
    mc = primed()
    sent = []
    for minute in range(0, 60, 2):
        swap = 3.0 if 2 <= minute <= 22 else 13.0
        if mc.run(minute, swap=swap)["reason"]:
            sent.append(minute)
    assert sent == [0, 30]


def test_the_alert_names_bands_as_read_on_this_run():
    mc = primed()
    assert mc.run(0, swap=13.0)["reason"] == "escalated"
    v = mc.run(2, swap=3.0, load=1.5)
    assert v["reason"] == "changed"
    assert v["bands"]["swap"] == "critical"           # held, for the decision
    assert v["live"]["swap"] == "ok"                  # what the alert prints
    assert guard.worst_band(v["live"]) == "warn"


def test_chrome_or_a_runaway_missing_for_one_run_does_not_resend():
    mc = primed()
    kw = dict(swap=13.0, chrome={"gb": 9, "renderers": 2}, runaways=[{"pid": 31}])
    assert mc.run(0, **kw)["reason"] == "escalated"
    for minute in range(2, 50, 2):
        if minute == 34:                              # Chrome second for one run
            v = mc.run(minute, swap=13.0)
        elif minute == 40:                            # process table unreadable
            v = mc.run(minute, swap=13.0, causes_known=False)
        else:
            v = mc.run(minute, **kw)
        assert v["reason"] is None, minute


def test_swap_resting_just_under_its_critical_line_recovers():
    """Swap touches 12.1GB once, then rests at 10.5GB with a flat trend. The
    margin alone would hold it critical for ever (resend every 2 hours, no
    recovery); the long-run rule lets it go."""
    mc = primed()
    assert mc.run(0, swap=12.1)["reason"] == "escalated"
    reasons = [mc.run(minute, swap=10.5)["reason"] for minute in range(2, 130, 2)]
    assert reasons.count("recovered") == 1 and reasons.index("recovered") <= 15
    assert set(reasons) == {None, "recovered"}


def test_the_reported_swap_rise_survives_another_alert():
    mc = primed()
    assert mc.run(0, swap=9.0, trend="warn", rise=2.1)["reason"] == "escalated"
    for minute in range(2, 12, 2):
        mc.run(minute, swap=9.0, rise=1.0)            # trend settles to ok
    assert mc.run(12, swap=9.0, rise=1.0, load=1.5)["reason"] == "changed"
    assert mc.state["reported"]["swap trend"]["bucket"] == 2


def test_state_from_before_the_alert_memory_keeps_its_named_causes():
    old = {"band": "critical", "notified_at": T0 - 5 * MIN,
           "notified_sig": '{"chrome": true, "runaways": [31]}',
           "bands": {"swap": "critical"}}
    reason, _ = guard.decide_alert(old, {"swap": "critical"}, {"band": "ok"},
                                   {"gb": 9, "renderers": 2}, [{"pid": 31}], T0)
    assert reason is None
