"""When fleet_guard sends an alert, and when it stays quiet.

Each alert wakes an agent session that pays its whole context to read it. On
2026-11-13 nine alerts arrived in 33 minutes while swap wobbled around its
critical line and page-ins (with no page-outs) wobbled around theirs; free
memory was 31-40% throughout and none of the nine needed action. These tests
replay those readings and pin the rules that keep a wobble quiet without
hiding a real escalation. Pure functions and canned readings only.
"""
import importlib.util
import json
import os

import pytest

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
            causes_known=True, trend_warming=False, healthcheck=5.0):
        now = T0 + minute * MIN
        dt = 120.0 if self.at is None else now - self.at
        self.ins += int(pin * 1024 ** 2 * dt / PAGE)
        self.outs += int(pout * 1024 ** 2 * dt / PAGE)
        self.at = now
        m = {"swap_gb": swap, "free_pct": free, "load_per_core": load,
             "orphan_workers": 0, "anon_gb": 5.0, "boot_free_gb": 90.0,
             "healthcheck_age_min": healthcheck, "pagesize": PAGE, "epoch": now,
             "pageins": self.ins if paging_known else None,
             "pageouts": self.outs if paging_known else None}
        paging = guard.paging_rate(self.state.get("metrics"), m,
                                   self.state.get("pagein_high_runs", 0))
        tr = {"band": trend, "rise_gb": rise, "text": f"swap {rise:+.1f}GB"}
        if trend_warming:
            tr = {"band": "unknown", "warming": True, "text": "swap trend unknown"}
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
    """Nine were sent; three are news: swap critical (10:17), load warn with
    paging warn (10:38), swap trend warn (10:44). The other six were the same
    causes again inside 30 minutes: swap stayed inside the margin of its
    critical line (never under 10.2GB), and page-ins alone rate nothing until
    they have held for three runs in a row. At the times the alerts carry,
    that third run is 10:38 (10:25 to 10:31 is too long a gap to be "in a
    row"), and it goes out in the same message as load warn."""
    sent = replay(NINE)
    assert [(m, r) for m, r, _ in sent] == [
        (17, "escalated"), (38, "changed"), (44, "changed")]
    assert sent[0][2] == ["swap"]
    assert sent[1][2] == ["load", "paging", "swap"]
    assert "swap trend" in sent[2][2]


def test_the_nine_readings_as_nine_runs_in_a_row_send_four():
    """The same readings two minutes apart. Now the page-ins are at or above
    100MB/s for a third run at the fourth reading, with swap at 11GB: that
    is the sustained case, and paging warn is a fourth message."""
    sent = replay([(i * 2, kw) for i, (_, kw) in enumerate(NINE)])
    assert [(m // 2, r) for m, r, _ in sent] == [
        (0, "escalated"), (3, "changed"), (5, "changed"), (6, "changed")]
    assert sent[1][2] == ["paging", "swap"]


def test_the_nine_readings_with_calm_runs_between_send_three():
    """The guard runs every 2 minutes, so there were runs between the nine.
    A run that dipped to all-ok is what re-armed the old rule each time. Put
    one between every pair and the page-ins are never high three runs in a
    row, so paging rates nothing: swap critical, load warn, swap trend warn."""
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
    assert mc.run(0, load=3.5)["reason"] == "escalated"
    for minute in range(2, 40, 2):
        assert mc.run(minute, load=1.5)["reason"] is None
    assert mc.run(40, load=3.5)["reason"] == "escalated"


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


def sends_over(mc, minutes, **kw):
    """[(minute, reason)] for every run in `minutes` that sent or recovered."""
    out = []
    for minute in minutes:
        v = mc.run(minute, **kw)
        if v["reason"]:
            out.append((minute, v["reason"]))
    return out


def test_an_unreadable_reading_is_reported_and_resent_for_six_hours():
    """Swap cannot be read and nothing else is wrong. It is said once it has
    stood for three runs, then every 2 hours, and is never a recovery."""
    mc = primed()
    v = mc.run(0, swap=None)
    assert v["reason"] is None and v["bands"]["swap"] == "unknown"
    assert mc.run(2, swap=None)["reason"] is None
    v = mc.run(4, swap=None)
    assert v["reason"] == "changed"
    assert guard.alert_title(v) == ("unknown", "swap unreadable")
    sent = sends_over(mc, range(6, 362, 2), swap=None)
    assert sent == [(126, "unchanged-resend"), (248, "unchanged-resend")]
    # Readable again: the cause stops standing after three good runs. Its
    # end is not announced, and no warn or critical ever went out to recover.
    assert sends_over(mc, range(362, 420, 2)) == []
    assert mc.state["unreadable"] == {}


def test_a_trend_or_rate_waiting_for_samples_is_not_unreadable():
    """After a long sleep or a rotated history the swap trend reads unknown
    for 40 minutes on a healthy machine. That is waiting, not a failed read."""
    mc = primed()
    assert sends_over(mc, range(0, 42, 2), trend_warming=True) == []
    # Runs 20 minutes apart: every paging rate is "previous sample stale".
    mc = primed()
    assert sends_over(mc, range(0, 400, 20)) == []
    assert "stale" in mc.paging["text"]


def test_a_reading_that_fails_most_of_the_time_is_one_standing_cause():
    """Swap reads once every 40 minutes and fails otherwise, for 4 hours. One
    good read must not announce a recovery and re-arm the alert."""
    mc = primed()
    sent = []
    for minute in range(0, 240, 2):
        v = mc.run(minute, swap=3.0 if minute % 40 == 20 else None)
        if v["reason"]:
            sent.append((minute, v["reason"]))
    assert sent == [(4, "changed"), (126, "unchanged-resend")]


def test_a_reading_that_is_never_readable_does_not_hold_back_recovery():
    """No healthcheck status file: that reading is unknown on every run. It
    is said and resent, and a swap critical that clears still recovers."""
    mc = primed()
    sent = []
    for minute in range(0, 150, 2):
        v = mc.run(minute, healthcheck=None, swap=13.0 if 20 <= minute < 30 else 3.0)
        if v["reason"]:
            sent.append((minute, v["reason"], guard.alert_title(v)[1]))
    assert sent == [
        (4, "changed", "healthcheck freshness unreadable"),
        (20, "escalated", "swap critical, healthcheck freshness unreadable"),
        (50, "recovered", "healthcheck freshness unreadable"),
        (142, "unchanged-resend", "healthcheck freshness unreadable"),
    ]


def test_a_reported_critical_that_goes_unreadable_is_not_silent_for_six_hours():
    mc = primed()
    assert mc.run(0, pin=200, pout=200)["reason"] == "escalated"
    v = mc.run(2, paging_known=False)
    assert v["bands"]["paging"] == "unknown"          # not the held "critical"
    assert "unknown" in mc.paging["text"]
    sent = sends_over(mc, range(4, 362, 2), paging_known=False)
    assert sent == [(6, "changed"), (128, "unchanged-resend"),
                    (250, "unchanged-resend")]
    mc.run(362)                                       # counters back; no rate yet
    assert sends_over(mc, range(364, 400, 2)) == [(364, "recovered")]


def test_one_or_two_unread_runs_send_nothing():
    """The paging rate is unknown for a run or two after any gap (the counters
    unread, then no previous sample); that is not news."""
    mc = primed()
    for minute in range(2, 60, 2):
        v = mc.run(minute, paging_known=minute % 10 != 0)
        assert v["reason"] is None, minute


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


def page_in_bands(swap, runs, pin=195):
    mc = primed()
    return [mc.run(i * 2, swap=swap, pin=pin) and mc.paging["band"]
            for i in range(runs)]


def test_sustained_page_ins_with_high_swap_warn_and_never_go_critical():
    """195MB/s in, 0 out. A stall once hung at 140MB/s in with 0 out, so
    page-ins alone must still be able to rate; one run of them must not."""
    assert page_in_bands(swap=9.0, runs=1) == ["ok"]
    assert page_in_bands(swap=9.0, runs=6) == ["ok", "ok"] + ["warn"] * 4
    assert page_in_bands(swap=9.0, runs=4, pin=900)[-1] == "warn"   # not critical
    assert page_in_bands(swap=4.0, runs=6) == ["ok"] * 6
    assert page_in_bands(swap=None, runs=6) == ["ok"] * 6
    assert page_in_bands(swap=9.0, runs=6, pin=99) == ["ok"] * 6
    # Three high samples 6 minutes apart are not three runs in a row.
    mc = primed()
    assert [mc.run(i * 6, swap=9.0, pin=195) and mc.paging["band"]
            for i in range(5)] == ["ok"] * 5
    # The run of three has to be unbroken.
    mc = primed()
    bands = [mc.run(i * 2, swap=9.0, pin=20 if i == 2 else 195) and mc.paging["band"]
             for i in range(5)]
    assert bands == ["ok"] * 5
    # And it is one message per 30 minutes, not one per run.
    mc = primed()
    sent = sends_over(mc, range(0, 30, 2), swap=9.0, pin=195)
    assert sent == [(4, "escalated")]


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
    assert mc.run(0, load=3.5)["reason"] == "escalated"
    for minute in (2, 4, 6):
        assert mc.run(minute, load=1.5)["reason"] is None
    assert mc.run(8, load=1.5, free=20)["reason"] == "changed"
    assert mc.run(10, load=3.5, free=20)["reason"] is None


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



# --- time going backwards, damaged state, state from the old version --------

def test_a_backwards_clock_step_does_not_freeze_the_windows():
    """Every window is measured from a stored time. After the clock steps back
    a day those times are in the future; left alone, a standing critical
    would not resend and a returning one would not send until the clock
    caught up."""
    day = 24 * 60
    mc = primed()
    assert mc.run(0, load=3.5)["reason"] == "escalated"
    assert mc.run(2, load=3.5)["reason"] is None
    sent = sends_over(mc, range(4 - day, 124 - day, 2), load=3.5)
    assert sent and sent[0][1] == "unchanged-resend"   # within 2 hours

    mc = primed()
    assert mc.run(0, load=3.5)["reason"] == "escalated"
    gone = sends_over(mc, range(2 - day, 62 - day, 2))  # clear for an hour
    assert [r for _, r in gone] == ["recovered"]
    assert mc.run(62 - day, load=3.5)["reason"] == "escalated"


def readings(swap=13.0, now=T0):
    m = {"swap_gb": swap, "free_pct": 35, "load_per_core": 0.4,
         "orphan_workers": 0, "anon_gb": 5.0, "boot_free_gb": 90.0,
         "healthcheck_age_min": 5.0, "pagesize": PAGE, "epoch": now,
         "pageins": 0, "pageouts": 0}
    return m, {"band": "ok", "rise_gb": 0.0}, {"band": "ok"}


DAMAGED = [
    None,
    [],
    {"reported": None},
    {"reported": []},
    {"reported": {"swap": {"band": "critical"}}},            # no "at"
    {"reported": {"swap": {"at": T0 - 60}}},                 # no "band"
    {"reported": {"swap": "critical"}},
    {"reported": {}, "alerted_at": None},
    {"notified_sig": 5, "bands": {"swap": "critical"}},
    {"notified_sig": "[1]", "bands": {"swap": "critical"}},
    {"notified_sig": "not json", "bands": {"swap": "critical"}},
    {"bands": ["swap"]},
    {"bands": {"swap": []}},                                 # unhashable
    {"reported": {"swap": {"band": {}, "at": T0 - 60}}},
    {"metrics": {"epoch": "yesterday"}},
    {"metrics": {"epoch": float("nan")}},
    {"metrics": {"epoch": T0 - 120, "pageins": "x", "pageouts": 0}},
    {"loops_down": [[1]]},
    {"unreadable": ["swap"]},
]


@pytest.mark.parametrize("state", DAMAGED, ids=[json.dumps(d)[:40] for d in DAMAGED])
def test_damaged_state_is_dropped_said_and_a_first_critical_still_sends(state):
    m, trend, paging = readings()
    v = guard.assess(state, m, trend, paging, None, [], T0)
    assert "state unreadable" in v["state_problem"]
    assert v["reason"] == "escalated"
    saved = dict(v["memory"], bands=v["bands"], metrics=m)
    assert guard.sanitize_state(saved) == (saved, None)      # a good state again


@pytest.mark.parametrize("state", DAMAGED, ids=[json.dumps(d)[:40] for d in DAMAGED])
def test_a_run_with_damaged_state_still_revives_reaps_alerts_and_saves(
        state, monkeypatch, capsys):
    """main() end to end with every outside call stubbed: a bad state file
    must not stop loop revival, the orphan reap, the alert or save_state."""
    m, _, _ = readings(now=1.0)
    m.update(compressed_gb=1.0, orphan_worker_gb=0.0, claude_sessions=2,
             claude_gb=1.0)
    calls = {"alerts": [], "saved": [], "reap": 0, "loops": 0}

    def loops():
        calls["loops"] += 1
        return {"some-loop": "up"}

    def reap(**kw):
        calls["reap"] += 1
        return [], []

    monkeypatch.setattr(guard.sys, "argv", ["fleet_guard.py"])
    monkeypatch.setattr(guard, "read_metrics", lambda: m)
    monkeypatch.setattr(guard, "load_state", lambda: state)
    monkeypatch.setattr(guard, "read_history", lambda: [])
    monkeypatch.setattr(guard, "read_process_table", lambda: [])
    monkeypatch.setattr(guard, "loop_status", loops)
    monkeypatch.setattr(guard, "reap_orphaned_chromes", reap)
    monkeypatch.setattr(guard, "top_holders", lambda: "holders")
    monkeypatch.setattr(guard, "notify", lambda *a: True)
    monkeypatch.setattr(guard, "hive_alert",
                        lambda title, msg: calls["alerts"].append(title) or "sent")
    monkeypatch.setattr(guard, "save_state", calls["saved"].append)
    monkeypatch.setattr(guard, "record_history", lambda s: None)
    assert guard.main() == 0
    assert (calls["loops"], calls["reap"]) == (1, 1)
    assert calls["alerts"] == ["CRITICAL — swap critical"]
    assert capsys.readouterr().out.count("state unreadable") == 1
    (saved,) = calls["saved"]
    assert guard.sanitize_state(saved) == (saved, None)
    assert saved["reported"]["swap"]["band"] == "critical"


def old_format(bands, alerted_min_ago):
    """State as the version before the alert memory wrote it."""
    bad = sorted(k for k, v in bands.items() if v in guard.BAD)
    m, _, _ = readings(now=T0 - 2 * MIN)
    return {"band": guard.worst_band(bands), "bands": bands, "metrics": m,
            "notified_at": T0 - alerted_min_ago * MIN,
            "notified_sig": json.dumps({"worst": "critical", "bands": bad,
                                        "trend": None, "chrome": False,
                                        "runaways": []})}


def test_old_format_state_through_assess():
    m, trend, paging = readings()
    calm = old_format({"swap": "ok", "load": "ok"}, alerted_min_ago=600)
    v = guard.assess(calm, m, trend, paging, None, [], T0)
    assert (v["reason"], v["state_problem"]) == ("escalated", None)

    recent = old_format({"swap": "critical", "load": "ok"}, alerted_min_ago=5)
    v = guard.assess(recent, m, trend, paging, None, [], T0)
    assert (v["reason"], v["state_problem"]) == (None, None)

    stale = old_format({"swap": "critical", "load": "ok"}, alerted_min_ago=180)
    v = guard.assess(stale, m, trend, paging, None, [], T0)
    assert (v["reason"], v["state_problem"]) == ("unchanged-resend", None)

    # A notified_at later than now (clock stepped back) reads as expired.
    future = old_format({"swap": "critical", "load": "ok"}, alerted_min_ago=-1440)
    v = guard.assess(future, m, trend, paging, None, [], T0)
    assert v["reason"] == "unchanged-resend"



def test_old_state_with_no_alert_yet_is_not_damage():
    """The version before the alert memory saved notified_sig as null until
    its first alert. That state must be kept: it holds loops_down and the
    paging counters."""
    state = {"band": "ok", "bands": {"swap": "ok"}, "notified_sig": None,
             "notified_at": 0, "loops_down": ["some-loop"]}
    assert guard.sanitize_state(state) == (state, None)


def test_a_state_file_that_does_not_parse_is_said(tmp_path, monkeypatch):
    path = tmp_path / "guard-state.json"
    monkeypatch.setattr(guard, "STATE", str(path))
    assert guard.load_state() == {}                   # no file yet: quiet
    assert guard.sanitize_state(guard.load_state()) == ({}, None)
    path.write_text('{"band": "critical", "reported": {"sw')   # cut off mid-write
    state, problem = guard.sanitize_state(guard.load_state())
    assert state == {} and "state unreadable" in problem


def test_a_raise_the_state_check_did_not_foresee_still_finishes_the_run(
        monkeypatch, capsys):
    m, _, _ = readings(now=1.0)
    m.update(compressed_gb=1.0, orphan_worker_gb=0.0, claude_sessions=2,
             claude_gb=1.0)
    saved, real = [], guard.assess

    def assess(st, *a, **kw):
        if st:
            raise RuntimeError("a shape nobody listed")
        return real(st, *a, **kw)

    monkeypatch.setattr(guard.sys, "argv", ["fleet_guard.py"])
    monkeypatch.setattr(guard, "assess", assess)
    monkeypatch.setattr(guard, "read_metrics", lambda: m)
    monkeypatch.setattr(guard, "load_state", lambda: {"band": "ok"})
    monkeypatch.setattr(guard, "read_history", lambda: [])
    monkeypatch.setattr(guard, "read_process_table", lambda: [])
    monkeypatch.setattr(guard, "loop_status", lambda: {"some-loop": "up"})
    monkeypatch.setattr(guard, "reap_orphaned_chromes", lambda **kw: ([], []))
    monkeypatch.setattr(guard, "top_holders", lambda: "holders")
    monkeypatch.setattr(guard, "notify", lambda *a: True)
    monkeypatch.setattr(guard, "hive_alert", lambda title, msg: "sent")
    monkeypatch.setattr(guard, "save_state", saved.append)
    monkeypatch.setattr(guard, "record_history", lambda s: None)
    assert guard.main() == 0
    assert "state unusable (RuntimeError" in capsys.readouterr().out
    assert saved[0]["reported"]["swap"]["band"] == "critical"
