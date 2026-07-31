"""Resolve the media-library root without consulting source-tree location.

Precedence for root-oriented commands:

1. Explicit command ``--root`` (handled by the feature CLI after parse)
2. Feature-specific root environment variable (``TRANSCODE_ROOT``,
   ``TRACKSTRIP_ROOT``, …)
3. ``MEDIALIB_ROOT``
4. The invocation working directory

Python code must not walk ancestors looking for a directory named
``.agents``; that logic lives only in the shell launcher.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Roots that must never be used as a mutation target. Extend carefully;
# each entry needs a focused rejection test.
_DANGEROUS_ROOTS = frozenset({Path("/"), Path.home().resolve()})


class RootError(ValueError):
    """Invalid or rejected media-library root."""


@dataclass(frozen=True, slots=True)
class ResolvedRoot:
    path: Path
    source: str

    def __str__(self) -> str:
        return str(self.path)


def resolve_default_root(
    *,
    feature_env: str | None = None,
    environ: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> ResolvedRoot:
    """Resolve the default library root before argparse runs.

    ``feature_env`` is the name of an optional feature-specific override
    such as ``TRANSCODE_ROOT`` or ``TRACKSTRIP_ROOT``. Explicit
    ``--root`` is applied later by argparse defaults/overrides.
    """
    env = os.environ if environ is None else environ
    if feature_env:
        value = env.get(feature_env)
        if value:
            return ResolvedRoot(Path(value).expanduser(), feature_env)
    value = env.get("MEDIALIB_ROOT")
    if value:
        return ResolvedRoot(Path(value).expanduser(), "MEDIALIB_ROOT")
    work = Path.cwd() if cwd is None else cwd
    return ResolvedRoot(work, "cwd")


def validate_root(
    root: Path | str,
    *,
    must_exist: bool = True,
    allow_dangerous: bool = False,
) -> Path:
    """Resolve ``root`` and reject nonexistent or dangerously broad paths."""
    path = Path(root).expanduser()
    try:
        resolved = path.resolve(strict=False)
    except OSError as exc:
        raise RootError(f"cannot resolve root path {path}: {exc}") from exc

    if must_exist:
        if not resolved.exists():
            raise RootError(f"root does not exist: {resolved}")
        if not resolved.is_dir():
            raise RootError(f"root is not a directory: {resolved}")

    if not allow_dangerous and resolved in _DANGEROUS_ROOTS:
        raise RootError(
            f"refusing to operate on dangerously broad root {resolved}; "
            "pass a media library directory explicitly"
        )
    return resolved


def root_option_source(argv: list[str], default: ResolvedRoot) -> str:
    """Report whether argparse's root came from an explicit CLI option."""
    if any(token == "--root" or token.startswith("--root=") for token in argv):
        return "--root"
    return default.source


def validate_deletion_target(
    target: Path | str,
    *,
    media_root: Path | str | None = None,
) -> Path:
    """Reject broad, mounted, symlinked, or library-containing purge targets."""
    raw = Path(target).expanduser()
    absolute = raw if raw.is_absolute() else Path.cwd() / raw
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise RootError(f"refusing to recursively delete through symlink: {current}")
    resolved = raw.resolve(strict=False)
    anchor = Path(resolved.anchor)
    if resolved in _DANGEROUS_ROOTS or resolved.parent == anchor or resolved.is_mount():
        raise RootError(f"refusing to recursively delete dangerously broad path: {resolved}")
    if media_root is not None:
        library = Path(media_root).expanduser().resolve(strict=False)
        if library in _DANGEROUS_ROOTS:
            raise RootError(f"invalid dangerously broad media root: {library}")
        if resolved == library or resolved in library.parents:
            raise RootError(f"refusing to delete the media root or one of its parents: {resolved}")
        protected = (library / ".cache" / "psammophis", library / ".agents")
        for protected_path in protected:
            if (
                resolved == protected_path
                or resolved in protected_path.parents
                or protected_path in resolved.parents
            ):
                raise RootError(
                    f"refusing to delete protected application state or its parent: {resolved}"
                )
    if resolved.exists() and not resolved.is_dir():
        raise RootError(f"deletion target is not a directory: {resolved}")
    return resolved
