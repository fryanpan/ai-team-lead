"""--resume-id resumes one named conversation instead of `--continue`.

`--continue` takes the newest transcript in the cwd's project dir, and that dir
can hold another project's conversation. --resume-id must only accept a
conversation whose transcript lives in the target cwd's own project dir.
"""
import importlib.util
import json
import os

import pytest

_spec = importlib.util.spec_from_file_location(
    "respawn", os.path.join(os.path.dirname(__file__), "..", ".claude",
                            "skills", "respawn-sessions", "respawn.py"))
respawn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(respawn)

UUID = "807f469c-d67d-4941-b2db-0f0594f10ba5"


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    reg = tmp_path / "registry.yaml"
    reg.write_text("projects: {}\n")
    monkeypatch.setattr(respawn, "REGISTRY_PATH", str(reg))
    respawn.RESUME_ID = None
    respawn.FORCE_FRESH = False
    yield
    respawn.RESUME_ID = None


def _write_transcript(tdir, uuid, cwd):
    os.makedirs(tdir, exist_ok=True)
    with open(os.path.join(tdir, uuid + ".jsonl"), "w") as f:
        f.write(json.dumps({"type": "summary"}) + "\n")
        f.write(json.dumps({"type": "user", "cwd": cwd}) + "\n")


def _project(tmp_path, with_transcript=None, name="proj"):
    proj = tmp_path / "dev" / name
    proj.mkdir(parents=True)
    tdir = respawn.transcript_dir_for(str(proj))
    os.makedirs(tdir, exist_ok=True)
    if with_transcript:
        _write_transcript(tdir, with_transcript, os.path.realpath(str(proj)))
    return str(proj)


def test_rejects_non_uuid(tmp_path):
    proj = _project(tmp_path)
    assert "not a conversation id" in respawn.resume_id_problem(proj, "latest")


def test_rejects_transcript_from_another_cwd(tmp_path):
    proj = _project(tmp_path)
    other = tmp_path / "dev" / "other"
    other.mkdir()
    _write_transcript(respawn.transcript_dir_for(str(other)), UUID,
                      os.path.realpath(str(other)))
    assert "no transcript" in respawn.resume_id_problem(proj, UUID)


def test_rejects_colliding_folder_from_another_cwd(tmp_path):
    # foo_bar and foo-bar encode to the same project dir.
    proj = _project(tmp_path, name="foo-bar")
    other = tmp_path / "dev" / "foo_bar"
    other.mkdir()
    assert respawn.transcript_dir_for(str(other)) == respawn.transcript_dir_for(proj)
    _write_transcript(respawn.transcript_dir_for(proj), UUID,
                      os.path.realpath(str(other)))
    assert "ran in" in respawn.resume_id_problem(proj, UUID)


def test_accepts_own_transcript(tmp_path):
    proj = _project(tmp_path, with_transcript=UUID)
    assert respawn.resume_id_problem(proj, UUID) is None


def test_flag_uses_resume_when_set(tmp_path):
    proj = _project(tmp_path, with_transcript=UUID)
    respawn.RESUME_ID = UUID
    assert respawn.resume_flag(proj) == f" --resume {UUID}"


def test_flag_falls_back_to_continue(tmp_path):
    proj = _project(tmp_path, with_transcript=UUID)
    assert respawn.resume_flag(proj) == " --continue"


def test_flag_empty_without_history(tmp_path):
    proj = _project(tmp_path)
    assert respawn.resume_flag(proj) == ""


def test_main_refuses_without_single_at(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["respawn.py", "--resume-id", UUID])
    with pytest.raises(SystemExit):
        respawn.main()


def test_main_aborts_on_wrong_transcript(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    monkeypatch.setattr("sys.argv", ["respawn.py", "--at", proj, "--resume-id", UUID])
    assert respawn.main() == 2
    assert respawn.RESUME_ID is None


def test_main_refuses_repeated_resume_id(tmp_path, monkeypatch):
    proj = _project(tmp_path, with_transcript=UUID)
    monkeypatch.setattr("sys.argv", ["respawn.py", "--at", proj,
                                      "--resume-id", UUID, "--resume-id", UUID])
    with pytest.raises(SystemExit):
        respawn.main()
