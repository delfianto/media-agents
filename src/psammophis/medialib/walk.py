"""Shared media-file directory walker used by every skill's CLI.

`os.walk` is a live generator: a subdirectory is only read once the walk
actually descends into it. That means anything a long-running command
writes mid-run into a not-yet-visited, non-hidden subdirectory of the tree
it's walking gets picked up as a "new" input file in the very same
invocation. `exclude_dirs` prunes such subtrees (given as resolved absolute
paths) before the walk ever reaches them -- needed whenever a plain
(non-dot-prefixed) output/backup/log directory points inside the tree being
walked. Confirmed for real: an transcode `--output-dir transcode` run
inside its own `--root` produced a doubly-compressed
`transcode/transcode/...` copy of the very file `transcode/...` had just
finished writing (see skills/transcode/reference/incidents.md). That fix
originally landed only in transcode's own walker; track-strip's
`apply`/`transcode --backup-dir` used a separate, independently-written
walker with no such guard at all, so the same incident was still fully
reproducible there. This module exists so the guard only has to be written
--and gets exercised by tests -- once.
"""

import os
from collections.abc import Iterator
from pathlib import Path

DEFAULT_SKIP_DIR_NAMES = frozenset({"@eaDir", "#recycle"})


def walk_media_files(
    root: Path,
    extensions: frozenset[str],
    *,
    path_filter: str | None = None,
    limit: int | None = None,
    exclude_dirs: frozenset[Path] = frozenset(),
    skip_dir_names: frozenset[str] = DEFAULT_SKIP_DIR_NAMES,
    skip_root_files: bool = True,
) -> Iterator[Path]:
    """Yield media files under `root` matching `extensions`.

    Dot-prefixed directories/files and `skip_dir_names` are always excluded.
    `skip_root_files` (default True) additionally skips loose files sitting
    directly at `root` itself -- e.g. a freshly-downloaded file awaiting
    sorting into Movies/TV Shows shouldn't be touched by an automated pass.
    Pass `skip_root_files=False` when `root` itself is the thing meant to
    hold loose files (organize's inbox).
    """
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in skip_dir_names
            and not d.startswith(".")
            and (not exclude_dirs or (Path(dirpath) / d).resolve() not in exclude_dirs)
        )
        if skip_root_files and Path(dirpath) == root:
            continue
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            if Path(name).suffix.lower() not in extensions:
                continue
            abs_path = Path(dirpath) / name
            rel = str(abs_path.relative_to(root))
            if path_filter and path_filter.lower() not in rel.lower():
                continue
            yield abs_path
            count += 1
            if limit and count >= limit:
                return
