from __future__ import annotations

import shutil
import subprocess

from envcheck import checks
from medialib.svt import SvtImplementation


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_check_binary_not_found(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = checks.check_binary(
        "ffmpeg", "shared", "ffmpeg", required=True, install_hint="install it"
    )
    assert result.found is False
    assert result.install_hint == "install it"


def test_check_binary_found_reports_version_first_line(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _FakeCompleted(stdout="ffmpeg version n8.1.2\nmore text\n"),
    )
    result = checks.check_binary("ffmpeg", "shared", "ffmpeg", required=True, install_hint="")
    assert result.found is True
    assert result.detail == "ffmpeg version n8.1.2"


def test_check_binary_version_args_none_skips_subprocess_probe(monkeypatch):
    # Real bug this guards against: `stash-mcp --version` doesn't print a
    # version and exit -- it starts the actual MCP stdio server. A binary
    # with an unknown/unsafe CLI surface must be checked by presence alone.
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/stash-mcp")

    def _run_should_not_be_called(*a, **k):
        raise AssertionError("subprocess.run must not be invoked when version_args=None")

    monkeypatch.setattr(subprocess, "run", _run_should_not_be_called)
    result = checks.check_binary(
        "stash-mcp", "stash-app", "stash-mcp", required=False, install_hint="", version_args=None
    )
    assert result.found is True
    assert result.detail == "/usr/bin/stash-mcp"


def test_check_pip_or_uv_prefers_pip(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pip" if name == "pip" else None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompleted(stdout="pip 25.0\n"))
    result = checks.check_pip_or_uv()
    assert result.found is True
    assert "pip 25.0" in result.detail


def test_check_pip_or_uv_falls_back_to_uv(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompleted(stdout="uv 0.12.0\n"))
    result = checks.check_pip_or_uv()
    assert result.found is True
    assert "uv 0.12.0" in result.detail


def test_check_pip_or_uv_missing_both(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = checks.check_pip_or_uv()
    assert result.found is False
    assert "uv" in result.install_hint


def test_check_svt_implementation_reports_fork(monkeypatch):
    monkeypatch.setattr(
        checks,
        "detect_svt_implementation",
        lambda: SvtImplementation("svt-av1-hdr", "v4.1.0-19", "libSvtAv1Enc.so.4"),
    )
    result = checks.check_svt_implementation()
    assert result.found is True
    assert result.required is True
    assert "svt-av1-hdr v4.1.0-19" in result.detail


def test_check_env_var_found_and_missing():
    found = checks.check_env_var(
        "TMDB API key",
        "ORGANIZE_TMDB_API_KEY",
        category="x",
        required=True,
        install_hint="get one",
        env={"ORGANIZE_TMDB_API_KEY": "abc123"},
    )
    assert found.found is True
    assert found.install_hint == ""

    missing = checks.check_env_var(
        "TMDB API key",
        "ORGANIZE_TMDB_API_KEY",
        category="x",
        required=True,
        install_hint="get one",
        env={},
    )
    assert missing.found is False
    assert missing.install_hint == "get one"


def test_check_any_env_var_accepts_skill_specific_or_shared():
    found = checks.check_any_env_var(
        "TMDB API key",
        ("ORGANIZE_TMDB_API_KEY", "TMDB_API_KEY"),
        category="organize",
        required=True,
        install_hint="get one",
        env={"TMDB_API_KEY": "shared"},
    )
    assert found.found is True
    assert found.detail == "set: TMDB_API_KEY"


def test_check_python_package_found_and_missing():
    found = checks.check_python_package(
        "os", display_name="Python package: os", category="x", required=True, install_hint=""
    )
    assert found.found is True

    missing = checks.check_python_package(
        "definitely_not_a_real_package_xyz",
        display_name="Python package: fake",
        category="x",
        required=True,
        install_hint="pip install fake",
    )
    assert missing.found is False
    assert missing.install_hint == "pip install fake"
