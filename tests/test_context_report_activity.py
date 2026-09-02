"""Session activity is read from the transcript, never from the pane.

`fleet_context_report.py` used to decide whether a session was working by
running `tmux capture-pane` and matching on what it rendered: "esc to
interrupt" meant busy, text on the `❯` line meant Bryan had a draft in the
box. Both are renders, not state, and both mapped to "skip".

That inverted the tool. The report exists to flag quiet giant sessions for
review, so every pane misread SUPPRESSED a notification -- the failure mode
was silence, which is indistinguishable from a healthy fleet.

Fabricated transcripts only. Never point a monitor's tests at live state.
"""
import importlib.util
import os
import time

_HERE = os.path.dirname(__file__)
_SRC = os.path.join(_HERE, "..", "scripts", "fleet_context_report.py")
_spec = importlib.util.spec_from_file_location("fcr", _SRC)
fcr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fcr)


def _transcript(path, age_s=0.0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("{}\n")
    when = time.time() - age_s
    os.utime(path, (when, when))
    return path


def test_a_session_taking_turns_reads_active(tmp_path):
    tp = _transcript(str(tmp_path / "sess.jsonl"), age_s=30)
    assert fcr.activity_state(tp) == "active"


def test_a_session_silent_past_the_window_reads_quiet(tmp_path):
    tp = _transcript(str(tmp_path / "sess.jsonl"), age_s=fcr.QUIET_AFTER_S + 60)
    assert fcr.activity_state(tp) == "quiet"


def test_a_parent_in_a_long_fanout_stays_active_through_its_subagents(tmp_path):
    """The parent appends nothing while its subagents work.

    Subagent transcripts live at `<session-id>/subagents/agent-*.jsonl`. A
    non-recursive walk would call this session quiet and flag a working
    session for review -- the same glob bug that made the burn report
    undercount by 5.7x on 2026-09-02.
    """
    tp = _transcript(str(tmp_path / "sess.jsonl"), age_s=fcr.QUIET_AFTER_S + 600)
    _transcript(str(tmp_path / "sess" / "subagents" / "agent-1.jsonl"), age_s=5)
    assert fcr.activity_state(tp) == "active"


def test_activity_never_shells_out_to_the_pane(tmp_path):
    """A transcript alone must be enough -- no tmux on the path at all."""
    tp = _transcript(str(tmp_path / "sess.jsonl"), age_s=5)

    def _explode(*a, **k):
        raise AssertionError("activity_state shelled out; it must read the transcript")

    saved_sh, saved_run = fcr.sh, fcr.subprocess.run
    fcr.sh, fcr.subprocess.run = _explode, _explode
    try:
        assert fcr.activity_state(tp) == "active"
    finally:
        fcr.sh, fcr.subprocess.run = saved_sh, saved_run


def test_the_pane_reading_states_are_gone_from_the_source():
    """`busy` and `draft` were the two skip verdicts derived from pixels.

    Guarding the source, not just the function, because the defect was that the
    inference existed at all -- a later edit reintroducing `pane_state` would
    pass every behavioural test above while restoring the bug.
    """
    import ast

    tree = ast.parse(open(_SRC).read())
    for node in ast.walk(tree):                       # drop docstrings AND comments:
        if not isinstance(node, (ast.Module, ast.FunctionDef,               # the
                                 ast.AsyncFunctionDef, ast.ClassDef)):      # docstring
            continue                                                        # below
        body = getattr(node, "body", [])                                    # names
        if (body and isinstance(body[0], ast.Expr)                          # these
                and isinstance(body[0].value, ast.Constant)                 # very
                and isinstance(body[0].value.value, str)):                  # strings
            del body[0]
    code = ast.unparse(tree)
    assert "def pane_state" not in code
    assert "esc to interrupt" not in code
    for verdict in ('"busy"', '"draft"'):
        assert verdict not in code, f"{verdict} is a pane inference, not session state"
