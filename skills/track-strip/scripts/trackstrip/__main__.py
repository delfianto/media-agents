#!/usr/bin/env python3
"""Entrypoint: python3 trackstrip/__main__.py <scan|stats|plan|apply|transcode|purge-backups> ...

Lives inside the `trackstrip` package itself (rather than as a same-named
sibling file next to it) so nothing shadows or gets shadowed by the package
on import -- run directly by path like this, it behaves like any other
script (`__name__ == "__main__"`, never registered in `sys.modules` under
the package's own name), and it also means `python -m trackstrip` works from
`scripts/` if that's ever useful.

Best invoked via an absolute (or at least `.agents`-rooted) path. `.agents`
is commonly a symlink into the real checkout elsewhere; see _find_own_path's
docstring for why a relative invocation needs care to avoid losing that
symlink's name.
"""

import os
import sys
from pathlib import Path


def _absolute_preserving_symlinks(path: Path) -> Path:
    """Like `path.absolute()`, but prefers the shell's logical $PWD over
    the OS's always-physical cwd when they name the same directory (by
    inode, via os.path.samefile -- not by string equality), so a symlink
    component the shell `cd`-ed through survives. A no-op if `path` is
    already absolute."""
    if path.is_absolute():
        return path
    cwd = Path.cwd()
    pwd = os.environ.get("PWD")
    if pwd:
        pwd_path = Path(pwd)
        try:
            if pwd_path.is_dir() and os.path.samefile(pwd_path, cwd):
                return pwd_path / path
        except OSError:
            pass
    return cwd / path


def _find_own_path() -> Path:
    """This script's own path, preserving any symlink component it was
    invoked through -- unlike `__file__`, which CPython silently
    absolutizes via the OS's always-physical cwd before any of this code
    runs (confirmed directly: a `.agents` symlink component was already
    gone from `__file__` even though the invoking shell's own $PWD still
    had it). `sys.argv[0]` is the raw, unresolved string as passed by the
    shell -- reconstructed against $PWD instead, then verified with
    samefile against `__file__` before being trusted."""
    candidate = _absolute_preserving_symlinks(Path(sys.argv[0]))
    try:
        if candidate.samefile(__file__):
            return candidate
    except OSError:
        pass
    return Path(__file__)


def _agents_lib_dir(start: Path) -> Path:
    # Tiny, self-contained duplicate of medialib.libroot's search-by-name
    # logic -- can't import medialib to locate medialib.
    for ancestor in start.parents:
        if ancestor.name == ".agents":
            return ancestor / "lib"
    raise RuntimeError(f"could not find an ancestor directory named '.agents' above {start}")


_own_path = _find_own_path()
# __main__.py lives inside trackstrip/ itself, one level below scripts/ --
# that's the directory that needs to be on sys.path for `import trackstrip`
# to resolve (and for `.` in the package's own submodules to work).
_scripts_dir = _own_path.parent.parent
sys.path.insert(0, str(_scripts_dir))
try:
    sys.path.insert(0, str(_agents_lib_dir(_scripts_dir)))
except RuntimeError as exc:
    sys.exit(f"{exc}. Invoke this script by its .agents-rooted path (see SKILL.md).")

from trackstrip.cli import main

if __name__ == "__main__":
    main()
