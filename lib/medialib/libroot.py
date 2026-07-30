"""Library-root auto-detection shared by every skill's CLI.

Each skill's script lives at a fixed depth under the `.agents` checkout
(`skills/<name>/scripts/<pkg>/...`), and defaults its `--root` to that
checkout's parent directory -- i.e. wherever the user actually keeps their
media library, since `.agents` is cloned directly into it (see the
top-level CLAUDE.md).
"""

import os
import sys
from pathlib import Path


def _pwd_if_same_directory(cwd: Path) -> Path | None:
    """The shell's logical $PWD, if set and verified -- by inode via
    os.path.samefile, not by string equality -- to be the same directory
    as `cwd` (the OS's physical, symlink-resolved cwd).

    $PWD is how a symlink component a user `cd`-ed through survives at
    all: os.getcwd() (and therefore Path.cwd()/Path.absolute()) always
    return the fully resolved physical path, silently discarding it.
    """
    pwd = os.environ.get("PWD")
    if not pwd:
        return None
    pwd_path = Path(pwd)
    try:
        if pwd_path.is_dir() and os.path.samefile(pwd_path, cwd):
            return pwd_path
    except OSError:
        return None
    return None


def to_absolute_preserving_symlinks(path: Path) -> Path:
    """Like `path.absolute()`, but prefers the shell's logical $PWD over
    the OS's physical cwd when they refer to the same directory. A no-op
    if `path` is already absolute.
    """
    if path.is_absolute():
        return path
    cwd = Path.cwd()
    return (_pwd_if_same_directory(cwd) or cwd) / path


def find_own_script_path(dunder_file: str) -> Path:
    """The path of the currently-running top-level script, preserving any
    symlink component it was invoked through.

    `__file__` is *not* good enough for this: CPython absolutizes it via
    the OS's always-physical cwd before any script code runs, so a
    relative invocation typed after `cd`-ing into a symlinked `.agents`
    already has that symlink resolved away by the time this function (or
    any other code in the script) sees `__file__` -- confirmed directly,
    not a theoretical concern. `sys.argv[0]` is the raw, unresolved string
    as passed by the shell, so it's reconstructed against $PWD instead
    (falling back to the physical cwd) via `to_absolute_preserving_symlinks`.
    That result is verified with `samefile` against `dunder_file` before
    being trusted -- and simply falls back to `Path(dunder_file)` if that
    check fails (e.g. some non-standard launcher that doesn't set argv[0]
    to the script's own path), same as `.absolute()` would give anyway.

    Call as `find_own_script_path(__file__)` from the script itself --
    passed in explicitly since this module has no `__file__` of its own
    for the caller's script.
    """
    candidate = to_absolute_preserving_symlinks(Path(sys.argv[0]))
    try:
        if candidate.samefile(dunder_file):
            return candidate
    except OSError:
        pass
    return Path(dunder_file)


def find_agents_root(start: Path, marker_name: str = ".agents") -> Path:
    """The closest ancestor of `start` named `marker_name`.

    Raises rather than falling back to a hardcoded parent count on a miss:
    silently guessing the wrong directory as a default `--root` for a tool
    that can move, overwrite, or delete real media is worse than refusing
    to guess. Callers should catch this and tell the user to pass --root
    (or the skill's *_ROOT environment variable) explicitly.
    """
    start = to_absolute_preserving_symlinks(start)
    for ancestor in start.parents:
        if ancestor.name == marker_name:
            return ancestor
    raise RuntimeError(
        f"could not find an ancestor directory named {marker_name!r} above {start} "
        "to auto-detect a default library root"
    )


def find_library_root(start: Path, marker_name: str = ".agents") -> Path:
    """The media library root: the parent of wherever the `.agents` repo
    (named `marker_name`) is checked out."""
    return find_agents_root(start, marker_name).parent
