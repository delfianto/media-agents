"""Submit long-running jobs as systemd --user services.

Chat-agent tool calls have a 10-hour kill deadline. An encode does not, and
does not get "stuck" the way an HTTP request does. A user unit with
``Type=simple`` and ``TimeoutStartSec=infinity`` is the supervisor: no
wall-clock fuse, SIGTERM on ``systemctl --user stop`` goes to the main
psammophis process (``KillMode=mixed``) so ``ProcessSupervisor`` can cancel
ffmpeg, and linger keeps the job alive after the chat session dies.

``systemd-run`` defaults to ``Type=oneshot``, whose start timeout (often 90s)
would murder the same job we are trying to protect. This module never uses
oneshot.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

UNIT_PREFIX = "psammophis"
DEFAULT_STOP_TIMEOUT_SEC = 120
_UNIT_TOKEN = re.compile(r"^[A-Za-z0-9:_.\\-]+$")

_PASSTHROUGH_ENV = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "TERM",
        "XDG_RUNTIME_DIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "MEDIALIB_ROOT",
        "VIRTUAL_ENV",
        "CUDA_VISIBLE_DEVICES",
        "NVIDIA_VISIBLE_DEVICES",
        "NVIDIA_DRIVER_CAPABILITIES",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "TMPDIR",
    }
)
_PASSTHROUGH_PREFIXES = (
    "TRANSCODE_",
    "TRACKSTRIP_",
    "PSAMMOPHIS_",
    "MEDIALIB_",
    "LC_",
    "UV_",
)


class _RunResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[..., _RunResult]


class SystemdJobError(RuntimeError):
    """User-facing submit/stop failure. ``exit_code`` is what the CLI returns."""

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class SubmitResult:
    unit: str
    argv: list[str]
    linger: bool | None


def default_unit_name(command: str) -> str:
    return f"{UNIT_PREFIX}-{_sanitize_token(command)}"


def normalize_unit_name(name: str) -> str:
    token = name.removesuffix(".service")
    if not token or not _UNIT_TOKEN.fullmatch(token):
        raise SystemdJobError(
            f"invalid systemd unit name {name!r} "
            "(use letters, digits, and :_.\\\\- only; no path components)"
        )
    return token


def _sanitize_token(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9:_.\\-]+", "-", value).strip("-")
    if not cleaned or not _UNIT_TOKEN.fullmatch(cleaned):
        raise SystemdJobError(f"cannot derive a systemd unit name from {value!r}")
    return cleaned


def passthrough_environment(env: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if env is None else env
    out: dict[str, str] = {}
    for key, value in source.items():
        if "\n" in value or "\0" in value:
            continue
        if key in _PASSTHROUGH_ENV or key.startswith(_PASSTHROUGH_PREFIXES):
            out[key] = value
    out["PYTHONUNBUFFERED"] = "1"
    return out


def build_systemd_run_argv(
    *,
    unit: str,
    description: str,
    exec_argv: Sequence[str],
    working_directory: Path,
    environ: Mapping[str, str],
    stop_timeout_sec: int = DEFAULT_STOP_TIMEOUT_SEC,
) -> list[str]:
    if not exec_argv:
        raise SystemdJobError("systemd-run exec argv is empty")
    argv: list[str] = [
        "systemd-run",
        "--user",
        "--collect",
        f"--unit={unit}",
        f"--description={description}",
        "--service-type=simple",
        f"--working-directory={working_directory}",
        "--property=TimeoutStartSec=infinity",
        f"--property=TimeoutStopSec={stop_timeout_sec}",
        "--property=Restart=no",
        "--property=KillMode=mixed",
    ]
    for key, value in sorted(environ.items()):
        argv.append(f"--setenv={key}={value}")
    argv.append("--")
    argv.extend(exec_argv)
    return argv


def child_psammophis_argv(stripped: Sequence[str], *, default_reporter: str = "jsonl") -> list[str]:
    """Rebuild argv for the unit's ExecStart, forcing jsonl unless already set."""
    has_reporter = any(
        token == "--reporter" or token.startswith("--reporter=") for token in stripped
    )
    argv = [sys.executable, "-m", "psammophis"]
    if not has_reporter:
        argv.extend(["--reporter", default_reporter])
    argv.extend(stripped)
    return argv


def strip_systemd_flags(argv: Sequence[str]) -> tuple[list[str], str | None, bool]:
    """Return (argv without systemd flags, explicit unit or None, force)."""
    out: list[str] = []
    unit: str | None = None
    force = False
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--systemd":
            i += 1
            continue
        if token == "--systemd-force":
            force = True
            i += 1
            continue
        if token == "--systemd-unit":
            if i + 1 >= len(argv):
                raise SystemdJobError("--systemd-unit requires a name")
            unit = argv[i + 1]
            i += 2
            continue
        if token.startswith("--systemd-unit="):
            unit = token.split("=", 1)[1]
            i += 1
            continue
        out.append(token)
        i += 1
    return out, unit, force


def systemd_run_available() -> bool:
    return shutil.which("systemd-run") is not None


def user_linger_enabled(*, runner: Runner | None = None) -> bool | None:
    """Return True/False when loginctl answers, else None (unknown)."""
    run = runner or subprocess.run
    user = os.environ.get("USER") or os.environ.get("LOGNAME")
    if not user:
        return None
    try:
        proc = run(
            ["loginctl", "show-user", user, "-p", "Linger", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    value = (proc.stdout or "").strip().lower()
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


def unit_active(unit: str, *, runner: Runner | None = None) -> bool:
    run = runner or subprocess.run
    try:
        proc = run(
            ["systemctl", "--user", "is-active", "--quiet", f"{unit}.service"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemdJobError(f"systemctl --user is-active failed: {exc}") from exc
    return proc.returncode == 0


def iter_conflicting_transcode_pids(
    *,
    proc_root: Path | None = None,
    self_pid: int | None = None,
) -> list[int]:
    """PIDs of already-running ``psammophis transcode`` workers (not a --systemd submit)."""
    root = Path("/proc") if proc_root is None else proc_root
    mine = os.getpid() if self_pid is None else self_pid
    found: list[int] = []
    try:
        children = list(root.iterdir())
    except OSError:
        return found
    for entry in children:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == mine:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        parts = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
        if _is_transcode_worker(parts):
            found.append(pid)
    return found


def _is_transcode_worker(parts: Sequence[str]) -> bool:
    if not any("psammophis" in part for part in parts):
        return False
    if "--systemd" in parts:
        return False
    if "transcode" not in parts:
        return False
    # ``psammophis runs …`` / help / probe-only tooling must not match.
    try:
        index = parts.index("transcode")
    except ValueError:
        return False
    following = parts[index + 1 :]
    if not following:
        return True
    return following[0] not in {"-h", "--help", "probe", "list-presets", "purge-backups"}


def submit_user_service(
    *,
    command: str,
    stripped_argv: Sequence[str],
    unit: str | None = None,
    force: bool = False,
    working_directory: Path | None = None,
    environ: Mapping[str, str] | None = None,
    runner: Runner | None = None,
    proc_root: Path | None = None,
) -> SubmitResult:
    if not systemd_run_available() and runner is None:
        raise SystemdJobError("systemd-run not on PATH")
    resolved_unit = normalize_unit_name(unit or default_unit_name(command))
    if command == "transcode" and not force:
        others = iter_conflicting_transcode_pids(proc_root=proc_root)
        if others:
            pids = ", ".join(str(pid) for pid in others)
            raise SystemdJobError(
                f"a psammophis transcode process is already running (pid {pids}); "
                "do not start a second GPU/CPU encode. Stop it first or pass "
                "--systemd-force if you are certain"
            )
    if runner is None and unit_active(resolved_unit):
        raise SystemdJobError(
            f"user unit {resolved_unit}.service is already active; "
            f"watch it with: systemctl --user status {resolved_unit}.service"
        )
    cwd = Path.cwd() if working_directory is None else working_directory
    exec_argv = child_psammophis_argv(stripped_argv)
    argv = build_systemd_run_argv(
        unit=resolved_unit,
        description=f"psammophis {command}",
        exec_argv=exec_argv,
        working_directory=cwd,
        environ=passthrough_environment(environ),
    )
    run = runner or subprocess.run
    try:
        proc = run(argv, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise SystemdJobError(f"failed to invoke systemd-run: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise SystemdJobError(f"systemd-run failed: {detail}")
    return SubmitResult(
        unit=resolved_unit,
        argv=argv,
        linger=user_linger_enabled(runner=run),
    )


def format_submit_message(result: SubmitResult) -> str:
    unit = f"{result.unit}.service"
    lines = [
        f"started user unit {unit}",
        f"  systemctl --user status {unit}",
        f"  journalctl --user -u {unit} -f",
        f"  systemctl --user stop {unit}",
        "  psammophis runs list --active",
    ]
    if result.linger is False:
        lines.append(
            "warning: lingering is off for this user; the unit dies if the "
            "login session goes away. enable with: loginctl enable-linger"
        )
    return "\n".join(lines)


def stop_user_service(unit: str, *, runner: Runner | None = None) -> None:
    resolved = normalize_unit_name(unit)
    run = runner or subprocess.run
    try:
        proc = run(
            ["systemctl", "--user", "stop", f"{resolved}.service"],
            capture_output=True,
            text=True,
            timeout=DEFAULT_STOP_TIMEOUT_SEC + 30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemdJobError(f"systemctl --user stop failed: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise SystemdJobError(f"systemctl --user stop {resolved}.service failed: {detail}")


def unit_status_text(unit: str, *, runner: Runner | None = None) -> str:
    resolved = normalize_unit_name(unit)
    run = runner or subprocess.run
    try:
        proc = run(
            ["systemctl", "--user", "status", "--no-pager", f"{resolved}.service"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemdJobError(f"systemctl --user status failed: {exc}") from exc
    text = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if text:
        return text
    if err:
        return err
    return f"{resolved}.service: systemctl exit {proc.returncode}"
