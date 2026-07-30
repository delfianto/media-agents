"""Individual prerequisite checks. Each `check_*` function does real I/O
(subprocess/shutil.which/import) and returns a `CheckResult` -- the pure
logic (grouping, pass/fail summary) lives in `report.py` instead, so that
part stays unit-testable without mocking subprocess.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass

from medialib.gpu import detect_av1_nvenc_gpu, list_gpu_indices


@dataclass(frozen=True)
class CheckResult:
    name: str
    category: str
    required: bool
    found: bool
    detail: str = ""
    install_hint: str = ""


def _run(*args: str, timeout: float = 10.0) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except OSError, subprocess.TimeoutExpired:
        return None


def _first_line(text: str) -> str:
    text = (text or "").strip()
    return text.splitlines()[0] if text else ""


def check_python_version() -> CheckResult:
    ok = sys.version_info >= (3, 14)
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return CheckResult(
        name="Python",
        category="runtime",
        required=True,
        found=ok,
        detail=version,
        install_hint=(
            ""
            if ok
            else (
                "this repo requires Python 3.14+ (pyproject.toml target-version=py314; "
                "ruff format emits 3.14-only bare-comma except-clause syntax, and at least "
                "one skill's PEP 723 script block pins requires-python>=3.14) -- "
                "install a newer interpreter"
            )
        ),
    )


def check_binary(
    name: str,
    category: str,
    binary: str,
    *,
    required: bool,
    install_hint: str,
    version_args: tuple[str, ...] | None = ("--version",),
) -> CheckResult:
    """`version_args=None` skips the subprocess probe entirely and only
    reports the resolved path -- for a binary whose CLI surface isn't known
    to be a harmless no-op (confirmed the hard way: `stash-mcp --version`
    doesn't print a version and exit, it starts the real MCP stdio server,
    which only happened to return here because this probe's stdin isn't a
    live client connection for it to serve)."""
    path = shutil.which(binary)
    if path is None:
        return CheckResult(name, category, required, False, install_hint=install_hint)
    if version_args is None:
        return CheckResult(name, category, required, True, detail=path)
    proc = _run(binary, *version_args)
    detail = (_first_line(proc.stdout) or _first_line(proc.stderr)) if proc else ""
    return CheckResult(name, category, required, True, detail=detail or path)


def check_pip_or_uv() -> CheckResult:
    name = "pip (or uv)"
    pip_path = shutil.which("pip") or shutil.which("pip3")
    if pip_path:
        proc = _run(pip_path, "--version")
        return CheckResult(
            name, "runtime", True, True, detail=_first_line(proc.stdout) if proc else pip_path
        )
    uv_path = shutil.which("uv")
    if uv_path:
        proc = _run(uv_path, "--version")
        detail = _first_line(proc.stdout) if proc else uv_path
        return CheckResult(name, "runtime", True, True, detail=f"uv: {detail}")
    return CheckResult(
        name,
        "runtime",
        True,
        False,
        install_hint=(
            "neither pip nor uv found -- at least one machine this repo runs on has no "
            "system pip (see AGENTS.md); install uv (https://astral.sh/uv) as the "
            "documented fallback"
        ),
    )


def check_ffmpeg_encoder(encoder: str, *, required: bool, install_hint: str) -> CheckResult:
    name = f"ffmpeg encoder: {encoder}"
    if shutil.which("ffmpeg") is None:
        return CheckResult(
            name, "av1-transcode", required, False, install_hint="install ffmpeg first"
        )
    proc = _run("ffmpeg", "-hide_banner", "-h", f"encoder={encoder}")
    found = bool(proc and proc.returncode == 0 and "Encoder" in (proc.stdout or ""))
    return CheckResult(
        name, "av1-transcode", required, found, install_hint="" if found else install_hint
    )


def check_gpu() -> CheckResult:
    name = "AV1-capable NVIDIA GPU"
    if shutil.which("nvidia-smi") is None:
        return CheckResult(
            name,
            "av1-transcode",
            False,
            False,
            detail="no nvidia-smi on PATH",
            install_hint=(
                "only needed for the faster GPU backend -- auto falls back to "
                "cpu/libsvtav1 without it, so this is informational, not a blocker"
            ),
        )
    indices = list_gpu_indices()
    if not indices:
        return CheckResult(name, "av1-transcode", False, False, detail="nvidia-smi reports no GPUs")
    gpu_index = detect_av1_nvenc_gpu()
    if gpu_index is None:
        return CheckResult(
            name,
            "av1-transcode",
            False,
            False,
            detail=(
                f"{len(indices)} NVIDIA GPU(s) present, none accepted a real av1_nvenc "
                "encode (pre-Ada/RTX-40-series GPUs can't)"
            ),
        )
    return CheckResult(name, "av1-transcode", False, True, detail=f"GPU index {gpu_index}")


def check_python_package(
    module: str, *, display_name: str, category: str, required: bool, install_hint: str
) -> CheckResult:
    try:
        __import__(module)
    except ImportError:
        return CheckResult(display_name, category, required, False, install_hint=install_hint)
    return CheckResult(display_name, category, required, True)


def check_env_var(
    display_name: str,
    var_name: str,
    *,
    category: str,
    required: bool,
    install_hint: str,
    env: Mapping[str, str] | None = None,
) -> CheckResult:
    env = env if env is not None else os.environ
    found = bool(env.get(var_name))
    return CheckResult(
        display_name, category, required, found, install_hint="" if found else install_hint
    )


def all_checks() -> list[CheckResult]:
    return [
        check_python_version(),
        check_pip_or_uv(),
        check_binary(
            "ruff",
            "runtime",
            "ruff",
            required=False,
            install_hint="pip/uv/pipx install ruff -- code quality gate, see AGENTS.md",
        ),
        check_binary(
            "basedpyright",
            "runtime",
            "basedpyright",
            required=False,
            install_hint="pip/uv install basedpyright -- code quality gate, see AGENTS.md",
        ),
        check_binary(
            "pytest",
            "runtime",
            "pytest",
            required=False,
            install_hint="pip install -r requirements-dev.txt",
        ),
        check_binary(
            "ffmpeg",
            "shared",
            "ffmpeg",
            required=True,
            install_hint=(
                "install ffmpeg (with libsvtav1 + NVENC support if this machine has an "
                "NVIDIA GPU) -- required by media-library and av1-transcode"
            ),
        ),
        check_binary(
            "ffprobe",
            "shared",
            "ffprobe",
            required=True,
            install_hint="ffprobe ships alongside ffmpeg -- reinstall/repair the ffmpeg package",
        ),
        check_binary(
            "mkvmerge",
            "media-library",
            "mkvmerge",
            required=True,
            install_hint=(
                "install mkvtoolnix (provides mkvmerge/mkvpropedit) -- media-library's "
                "apply/transcode shell out to mkvmerge directly for track selection"
            ),
        ),
        check_ffmpeg_encoder(
            "libsvtav1",
            required=True,
            install_hint=(
                "this ffmpeg build has no libsvtav1 -- reinstall/rebuild ffmpeg with "
                "SVT-AV1 support (av1-transcode's CPU backend, and the only backend that "
                "can re-inject Dolby Vision RPU)"
            ),
        ),
        check_ffmpeg_encoder(
            "av1_nvenc",
            required=False,
            install_hint=(
                "this ffmpeg build has no av1_nvenc -- av1-transcode's GPU backend won't "
                "be offered (auto falls back to cpu)"
            ),
        ),
        check_gpu(),
        check_binary(
            "nvencc",
            "av1-transcode",
            "nvencc",
            required=False,
            install_hint=(
                "install rigaya's NVEnc (package `nvenc` on Arch/CachyOS) -- only needed "
                "for Dolby Vision/HDR10+ on the GPU backend; DV without it falls back to "
                "cpu automatically rather than dropping metadata"
            ),
        ),
        check_binary(
            "dovi_tool",
            "av1-transcode",
            "dovi_tool",
            required=False,
            install_hint=(
                "optional -- Dolby Vision RPU extract/inspect helper "
                "(github.com/quietvoid/dovi_tool)"
            ),
        ),
        check_binary(
            "hdr10plus_tool",
            "av1-transcode",
            "hdr10plus_tool",
            required=False,
            install_hint=(
                "optional -- HDR10+ metadata extract/inspect helper "
                "(github.com/quietvoid/hdr10plus_tool)"
            ),
        ),
        check_python_package(
            "guessit",
            display_name="Python package: guessit",
            category="media-organizer",
            required=True,
            install_hint=(
                "pip install -r requirements.txt (or run via uv, which reads its PEP 723 block)"
            ),
        ),
        check_env_var(
            "TMDB API key",
            "MEDIAORGANIZER_TMDB_API_KEY",
            category="media-organizer",
            required=True,
            install_hint=(
                "required -- get a free key at themoviedb.org (Settings -> API), then set "
                "MEDIAORGANIZER_TMDB_API_KEY or add TMDB_API_KEY= to media-organizer's .env"
            ),
        ),
        check_env_var(
            "OpenSubtitles API key",
            "MEDIAORGANIZER_OPENSUBTITLES_API_KEY",
            category="media-organizer",
            required=False,
            install_hint=(
                "optional -- without it, subtitle fetching is skipped (renaming/organizing "
                "still works); register at opensubtitles.com/api"
            ),
        ),
        check_binary(
            "stash-mcp",
            "stash-app",
            "stash-mcp",
            required=False,
            install_hint=(
                "optional -- only needed if the stash-app skill's MCP server "
                "(see mcp_config.json) is actually configured for this session"
            ),
            version_args=None,  # --version starts the real server -- see check_binary docstring
        ),
    ]
