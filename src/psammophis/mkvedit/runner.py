import contextlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from psammophis.runtime.filesystem import (
    discard_staged_backup,
    path_exists,
    restore_from_backup,
    stage_backup,
)
from psammophis.runtime.process import ProcessSupervisor
from psammophis.runtime.signals import CancellationRequested

from .command import Edits, build_command
from .model import Container, MetadataError, parse_container

Run = Callable[..., subprocess.CompletedProcess[str]]
PhaseCallback = Callable[[str, str], None]
HeartbeatCallback = Callable[[str], None]


@dataclass(frozen=True)
class Result:
    path: Path
    status: str
    detail: str
    command: tuple[str, ...] = ()
    backup: Path | None = None


def _invoke(
    run: Run,
    command: list[str],
    *,
    timeout: float,
    on_heartbeat: Callable[[], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    if run is not subprocess.run:
        return run(command, capture_output=True, text=True, check=False)
    result = ProcessSupervisor(command, timeout=timeout, on_heartbeat=on_heartbeat).run()
    return subprocess.CompletedProcess(command, result.returncode, result.stdout, result.stderr)


def inspect(
    path: Path,
    run: Run = subprocess.run,
    on_heartbeat: Callable[[], None] | None = None,
) -> Container:
    process = _invoke(
        run,
        ["mkvmerge", "-J", str(path)],
        timeout=120,
        on_heartbeat=on_heartbeat,
    )
    if process.returncode != 0:
        raise MetadataError(process.stderr.strip() or f"mkvmerge failed for {path}")
    try:
        data = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise MetadataError(f"mkvmerge returned invalid JSON for {path}") from exc
    return parse_container(path, data)


def backup_path(path: Path, suffix: str) -> Path:
    if not suffix or "/" in suffix:
        raise MetadataError("backup suffix must be a non-empty filename suffix")
    return path.with_name(path.name + suffix)


def apply(
    path: Path,
    edits: Edits,
    *,
    yes: bool,
    backup_suffix: str = ".mkvedit.bak",
    run: Run = subprocess.run,
    on_phase: PhaseCallback | None = None,
    on_heartbeat: HeartbeatCallback | None = None,
) -> Result:
    def phase(name: str, state: str) -> None:
        if on_phase is not None:
            on_phase(name, state)

    if path.is_symlink():
        return Result(path, "error", f"refusing in-place edit through symlink: {path}")

    current_phase = "inspect"
    phase("inspect", "started")
    try:
        command = build_command(
            inspect(
                path,
                run,
                (lambda: on_heartbeat("inspect")) if on_heartbeat else None,
            ),
            edits,
        )
    except MetadataError as exc:
        phase("inspect", "failed")
        return Result(path, "error", str(exc))
    phase("inspect", "succeeded")
    current_phase = None
    if not yes:
        return Result(path, "planned", "dry run", tuple(command))
    backup = backup_path(path, backup_suffix)
    if path_exists(backup):
        return Result(path, "error", f"backup already exists: {backup}", tuple(command))
    edit_invoked = False
    try:
        current_phase = "backup"
        phase("backup", "started")
        stage_backup(path, backup, allow_hardlink=False)
        phase("backup", "succeeded")
        current_phase = "edit"
        phase("edit", "started")
        edit_invoked = True
        process = _invoke(
            run,
            command,
            timeout=3600,
            on_heartbeat=(lambda: on_heartbeat("edit")) if on_heartbeat else None,
        )
        if process.returncode != 0:
            raise MetadataError(process.stderr.strip() or "mkvpropedit failed")
        phase("edit", "succeeded")
        current_phase = "verify"
        phase("verify", "started")
        inspect(
            path,
            run,
            (lambda: on_heartbeat("verify")) if on_heartbeat else None,
        )
        phase("verify", "succeeded")
        current_phase = None
    except KeyboardInterrupt, SystemExit, CancellationRequested:
        if edit_invoked:
            try:
                restore_from_backup(backup, path)
            except Exception as restore_exc:
                raise RuntimeError(
                    f"operation was cancelled and restore failed: {restore_exc}; "
                    f"backup remains at {backup}"
                ) from restore_exc
        elif path_exists(backup):
            discard_staged_backup(backup)
        with contextlib.suppress(Exception):
            if current_phase is not None:
                phase(current_phase, "cancelled")
        raise
    except Exception as exc:
        if not edit_invoked:
            if path_exists(backup):
                discard_staged_backup(backup)
            with contextlib.suppress(Exception):
                if current_phase is not None:
                    phase(current_phase, "failed")
            return Result(
                path,
                "error",
                str(exc),
                tuple(command),
                backup if path_exists(backup) else None,
            )
        with contextlib.suppress(Exception):
            if current_phase is not None:
                phase(current_phase, "failed")
            phase("rollback", "started")
        try:
            if path_exists(backup):
                restore_from_backup(backup, path)
        except Exception as restore_exc:
            return Result(
                path,
                "error",
                f"edit failed: {exc}; restore also failed: {restore_exc}",
                tuple(command),
                backup,
            )
        with contextlib.suppress(Exception):
            phase("rollback", "succeeded")
        return Result(
            path,
            "error",
            f"edit failed and original was restored: {exc}",
            tuple(command),
            backup,
        )
    return Result(path, "edited", "ok", tuple(command), backup)
