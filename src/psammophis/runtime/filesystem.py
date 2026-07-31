"""Crash-resistant filesystem primitives used by mutating commands."""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
import stat
from pathlib import Path
from uuid import uuid4


class RecoveryRequired(RuntimeError):
    """A filesystem operation landed only partially and needs inspection."""


_LINK_FALLBACK_ERRNOS = frozenset(
    {
        errno.EACCES,
        errno.EMLINK,
        errno.EOPNOTSUPP,
        errno.EPERM,
        errno.EXDEV,
        getattr(errno, "ENOTSUP", errno.EOPNOTSUPP),
    }
)


def path_exists(path: Path) -> bool:
    """Like Path.exists(), but also detects broken symlinks."""
    return os.path.lexists(path)


def same_entry(left: Path, right: Path) -> bool:
    """Compare directory entries without following symlinks."""
    try:
        left_stat = left.lstat()
        right_stat = right.lstat()
    except OSError:
        return False
    return (left_stat.st_dev, left_stat.st_ino) == (right_stat.st_dev, right_stat.st_ino)


def fsync_directory(path: Path) -> None:
    """Persist directory-entry changes where the platform supports it."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with contextlib.suppress(OSError):
        fd = os.open(path, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _fsync_file(path: Path) -> None:
    with open(path, "rb") as handle:
        os.fsync(handle.fileno())


def _new_temporary_path(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing_mode: int | None = None
    with contextlib.suppress(OSError):
        existing_mode = stat.S_IMODE(destination.stat().st_mode)
    for _ in range(100):
        temporary = destination.parent / f".{destination.name}.{uuid4().hex}.tmp"
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        except FileExistsError:
            continue
        try:
            if existing_mode is not None:
                os.fchmod(fd, existing_mode)
        finally:
            os.close(fd)
        return temporary
    raise FileExistsError(f"could not allocate a temporary file beside {destination}")


def install_no_replace(source: Path, destination: Path) -> None:
    """Atomically install a same-filesystem file without overwriting."""
    if path_exists(destination):
        raise FileExistsError(f"destination already exists: {destination}")
    linked = False
    try:
        os.link(source, destination)
        linked = True
        source.unlink()
        fsync_directory(destination.parent)
    except BaseException as exc:
        if linked and path_exists(source):
            try:
                if same_entry(source, destination):
                    destination.unlink()
                    fsync_directory(destination.parent)
            except OSError as cleanup_error:
                raise RecoveryRequired(
                    f"install failed ({exc}); partial destination cleanup also failed "
                    f"({cleanup_error}): {destination}"
                ) from exc
        raise


def atomic_write_bytes(destination: Path, data: bytes) -> None:
    temporary = _new_temporary_path(destination)
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(destination: Path, text: str, *, encoding: str = "utf-8") -> None:
    atomic_write_bytes(destination, text.encode(encoding))


def copy_to_temporary(source: Path, destination: Path) -> Path:
    """Create and fsync a complete copy beside its eventual destination."""
    temporary = _new_temporary_path(destination)
    try:
        shutil.copy2(source, temporary)
        if temporary.stat().st_size != source.stat().st_size:
            raise OSError(f"incomplete copy from {source} to {temporary}")
        _fsync_file(temporary)
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_copy(source: Path, destination: Path, *, overwrite: bool) -> None:
    """Copy completely to a sibling temp before exposing the destination."""
    if not overwrite and path_exists(destination):
        raise FileExistsError(f"destination already exists: {destination}")
    temporary = copy_to_temporary(source, destination)
    try:
        if overwrite:
            os.replace(temporary, destination)
            fsync_directory(destination.parent)
        else:
            install_no_replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def stage_backup(source: Path, destination: Path, *, allow_hardlink: bool = True) -> None:
    """Create a complete backup while leaving the source entry untouched."""
    if path_exists(destination):
        raise FileExistsError(f"backup already exists, refusing to overwrite: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not allow_hardlink:
        atomic_copy(source, destination, overwrite=False)
        return
    linked = False
    try:
        try:
            os.link(source, destination, follow_symlinks=False)
            linked = True
        except OSError as exc:
            if exc.errno not in _LINK_FALLBACK_ERRNOS:
                raise
            _copy_exclusive(source, destination)
            return
        fsync_directory(destination.parent)
    except BaseException:
        if linked and same_entry(source, destination):
            destination.unlink(missing_ok=True)
            fsync_directory(destination.parent)
        raise


def _copy_exclusive(source: Path, destination: Path) -> None:
    """Cross-filesystem backup fallback that can never overwrite a peer."""
    source_mode = stat.S_IMODE(source.stat().st_mode)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, source_mode)
    try:
        with open(source, "rb") as input_handle, os.fdopen(fd, "wb") as output_handle:
            shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        shutil.copystat(source, destination)
        if destination.stat().st_size != source.stat().st_size:
            raise OSError(f"incomplete backup copy from {source} to {destination}")
        fsync_directory(destination.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(fd)
        destination.unlink(missing_ok=True)
        fsync_directory(destination.parent)
        raise


def discard_staged_backup(destination: Path) -> None:
    try:
        destination.unlink()
        fsync_directory(destination.parent)
    except OSError as exc:
        raise RecoveryRequired(
            f"the original is still in place, but staged backup cleanup failed: "
            f"{destination}: {exc}"
        ) from exc


def install_verified(source: Path, temporary: Path, final_path: Path) -> None:
    """Install verified output without ever leaving no authoritative media path."""
    if final_path == source:
        os.replace(temporary, source)
        fsync_directory(source.parent)
        return
    if path_exists(final_path):
        raise FileExistsError(f"{final_path} already exists, refusing to overwrite")

    linked = False
    try:
        os.link(temporary, final_path)
        linked = True
        source.unlink()
        temporary.unlink()
        fsync_directory(final_path.parent)
        if source.parent != final_path.parent:
            fsync_directory(source.parent)
    except BaseException as exc:
        if path_exists(source):
            if linked and path_exists(final_path):
                try:
                    if not same_entry(temporary, final_path):
                        raise RecoveryRequired(
                            f"install failed ({exc}); unexpected destination now exists: "
                            f"{final_path}"
                        ) from exc
                    final_path.unlink()
                    fsync_directory(final_path.parent)
                except RecoveryRequired:
                    raise
                except OSError as cleanup_error:
                    raise RecoveryRequired(
                        f"install failed ({exc}); partial destination cleanup also failed "
                        f"({cleanup_error}); original remains at {source} and verified output "
                        f"at {temporary}"
                    ) from exc
            raise

        if not path_exists(final_path):
            raise RecoveryRequired(
                f"install failed ({exc}); source disappeared and no destination exists; "
                f"verified output may remain at {temporary}"
            ) from exc
        if path_exists(temporary):
            try:
                if not same_entry(temporary, final_path):
                    raise RecoveryRequired(
                        f"install completed at {final_path}, but unexpected temporary output "
                        f"remains at {temporary}"
                    ) from exc
                temporary.unlink()
                fsync_directory(temporary.parent)
            except RecoveryRequired:
                raise
            except OSError as cleanup_error:
                raise RecoveryRequired(
                    f"install completed at {final_path}, but temporary cleanup failed "
                    f"({cleanup_error}): {temporary}"
                ) from exc
        raise


def installation_completed(source: Path, temporary: Path, final_path: Path) -> bool:
    if path_exists(temporary):
        return False
    if final_path == source:
        return path_exists(source)
    return not path_exists(source) and path_exists(final_path)


def restore_from_backup(backup: Path, destination: Path) -> None:
    """Restore via a complete temporary copy and one atomic replacement."""
    atomic_copy(backup, destination, overwrite=True)
