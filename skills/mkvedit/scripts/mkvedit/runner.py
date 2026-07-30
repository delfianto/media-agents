import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .command import Edits, build_command
from .model import Container, MetadataError, parse_container

Run = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class Result:
    path: Path
    status: str
    detail: str
    command: tuple[str, ...] = ()
    backup: Path | None = None


def inspect(path: Path, run: Run = subprocess.run) -> Container:
    process = run(
        ["mkvmerge", "-J", str(path)],
        capture_output=True,
        text=True,
        check=False,
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
) -> Result:
    try:
        command = build_command(inspect(path, run), edits)
    except MetadataError as exc:
        return Result(path, "error", str(exc))
    if not yes:
        return Result(path, "planned", "dry run", tuple(command))
    backup = backup_path(path, backup_suffix)
    if backup.exists():
        return Result(path, "error", f"backup already exists: {backup}", tuple(command))
    try:
        shutil.copy2(path, backup)
        process = run(command, capture_output=True, text=True, check=False)
        if process.returncode != 0:
            raise MetadataError(process.stderr.strip() or "mkvpropedit failed")
        inspect(path, run)
    except (OSError, MetadataError) as exc:
        try:
            if backup.exists():
                shutil.copy2(backup, path)
        except OSError as restore_exc:
            return Result(
                path,
                "error",
                f"edit failed: {exc}; restore also failed: {restore_exc}",
                tuple(command),
                backup,
            )
        return Result(
            path,
            "error",
            f"edit failed and original was restored: {exc}",
            tuple(command),
            backup,
        )
    return Result(path, "edited", "ok", tuple(command), backup)
