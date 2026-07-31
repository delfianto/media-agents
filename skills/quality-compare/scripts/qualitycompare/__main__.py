#!/usr/bin/env python3

import os
import sys
from pathlib import Path


def _absolute_preserving_symlinks(path: Path) -> Path:
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
    candidate = _absolute_preserving_symlinks(Path(sys.argv[0]))
    try:
        if candidate.samefile(__file__):
            return candidate
    except OSError:
        pass
    return Path(__file__)


def _agents_lib_dir(start: Path) -> Path:
    for ancestor in start.parents:
        if ancestor.name == ".agents":
            return ancestor / "lib"
    raise RuntimeError(f"could not find an ancestor directory named '.agents' above {start}")


_own_path = _find_own_path()
_scripts_dir = _own_path.parent.parent
sys.path.insert(0, str(_scripts_dir))
try:
    sys.path.insert(0, str(_agents_lib_dir(_scripts_dir)))
except RuntimeError as exc:
    sys.exit(f"{exc}. Invoke this script by its .agents-rooted path (see SKILL.md).")

from qualitycompare.cli import main

if __name__ == "__main__":
    main()
