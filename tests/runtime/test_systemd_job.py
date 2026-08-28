from __future__ import annotations

import sys

import pytest

from psammophis.runtime import systemd_job as sj


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_default_unit_name():
    assert sj.default_unit_name("transcode") == "psammophis-transcode"


def test_normalize_unit_strips_suffix_and_rejects_paths():
    assert sj.normalize_unit_name("psammophis-transcode.service") == "psammophis-transcode"
    with pytest.raises(sj.SystemdJobError, match="invalid systemd unit name"):
        sj.normalize_unit_name("../evil")
    with pytest.raises(sj.SystemdJobError, match="invalid systemd unit name"):
        sj.normalize_unit_name("has space")


def test_build_systemd_run_argv_is_simple_with_infinite_start_timeout(tmp_path):
    argv = sj.build_systemd_run_argv(
        unit="psammophis-transcode",
        description="psammophis transcode",
        exec_argv=["/venv/bin/python", "-m", "psammophis", "transcode", "run", "--yes"],
        working_directory=tmp_path,
        environ={"PATH": "/bin", "PYTHONUNBUFFERED": "1"},
    )
    assert argv[:3] == ["systemd-run", "--user", "--collect"]
    assert "--service-type=simple" in argv
    assert "--property=TimeoutStartSec=infinity" in argv
    assert "--property=Restart=no" in argv
    assert "--property=KillMode=mixed" in argv
    assert "--service-type=oneshot" not in argv
    assert not any("RuntimeMaxSec" in part for part in argv)
    assert f"--working-directory={tmp_path}" in argv
    assert "--setenv=PATH=/bin" in argv
    dash = argv.index("--")
    assert argv[dash + 1 :] == [
        "/venv/bin/python",
        "-m",
        "psammophis",
        "transcode",
        "run",
        "--yes",
    ]


def test_child_argv_defaults_to_jsonl():
    argv = sj.child_psammophis_argv(["transcode", "run", "--yes"])
    assert argv[:4] == [sys.executable, "-m", "psammophis", "--reporter"]
    assert argv[4] == "jsonl"
    assert argv[-3:] == ["transcode", "run", "--yes"]


def test_child_argv_keeps_explicit_reporter():
    argv = sj.child_psammophis_argv(["--reporter", "quiet", "transcode", "run"])
    assert argv.count("--reporter") == 1
    assert argv[argv.index("--reporter") + 1] == "quiet"


def test_strip_systemd_flags():
    stripped, unit, force = sj.strip_systemd_flags(
        [
            "--systemd",
            "--systemd-unit",
            "psammophis-batch",
            "--systemd-force",
            "--reporter",
            "jsonl",
            "transcode",
            "run",
            "--yes",
        ]
    )
    assert stripped == ["--reporter", "jsonl", "transcode", "run", "--yes"]
    assert unit == "psammophis-batch"
    assert force is True


def test_passthrough_env_filters_and_unbuffers():
    env = sj.passthrough_environment(
        {
            "PATH": "/bin",
            "HOME": "/home/geist",
            "TRANSCODE_ROOT": "/media",
            "PS1": "nope",
            "SECRET_NEWLINE": "a\nb",
            "PYTHONUNBUFFERED": "0",
        }
    )
    assert env["PATH"] == "/bin"
    assert env["TRANSCODE_ROOT"] == "/media"
    assert "PS1" not in env
    assert "SECRET_NEWLINE" not in env
    assert env["PYTHONUNBUFFERED"] == "1"


def test_is_transcode_worker_ignores_submit_and_probe():
    assert sj._is_transcode_worker(["python", "-m", "psammophis", "transcode", "run", "--yes"])
    assert not sj._is_transcode_worker(
        ["python", "-m", "psammophis", "--systemd", "transcode", "run", "--yes"]
    )
    assert not sj._is_transcode_worker(["python", "-m", "psammophis", "transcode", "probe"])
    assert not sj._is_transcode_worker(["python", "-m", "psammophis", "runs", "list"])


def test_conflict_scan_reads_proc(tmp_path):
    worker = tmp_path / "4242"
    worker.mkdir()
    (worker / "cmdline").write_bytes(b"python\0-m\0psammophis\0transcode\0run\0--yes\0")
    submitter = tmp_path / "7"
    submitter.mkdir()
    (submitter / "cmdline").write_bytes(
        b"python\0-m\0psammophis\0--systemd\0transcode\0run\0--yes\0"
    )
    assert sj.iter_conflicting_transcode_pids(proc_root=tmp_path, self_pid=1) == [4242]


def test_submit_refuses_existing_transcode(tmp_path):
    worker = tmp_path / "99"
    worker.mkdir()
    (worker / "cmdline").write_bytes(b"python\0-m\0psammophis\0transcode\0run\0--yes\0")
    with pytest.raises(sj.SystemdJobError, match="already running"):
        sj.submit_user_service(
            command="transcode",
            stripped_argv=["transcode", "run", "--yes"],
            force=False,
            working_directory=tmp_path,
            environ={"PATH": "/bin"},
            runner=lambda *a, **k: _Proc(),
            proc_root=tmp_path,
        )


def test_submit_builds_and_invokes(tmp_path):
    seen: list[list[str]] = []

    def runner(argv, **_kwargs):
        seen.append(list(argv))
        if argv and argv[0] == "loginctl":
            return _Proc(stdout="yes\n")
        return _Proc(stdout="Running as unit: psammophis-transcode.service\n")

    result = sj.submit_user_service(
        command="transcode",
        stripped_argv=["transcode", "run", "--yes"],
        working_directory=tmp_path,
        environ={"PATH": "/bin", "HOME": "/home/geist"},
        runner=runner,
        proc_root=tmp_path,
    )
    assert result.unit == "psammophis-transcode"
    assert result.linger is True
    run_argv = next(item for item in seen if item and item[0] == "systemd-run")
    assert "--service-type=simple" in run_argv
    assert "--property=TimeoutStartSec=infinity" in run_argv
    message = sj.format_submit_message(result)
    assert "started user unit psammophis-transcode.service" in message
    assert "systemctl --user stop" in message
