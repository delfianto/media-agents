from __future__ import annotations

from pathlib import Path

from medialib.walk import walk_media_files

EXTENSIONS = frozenset({".mkv", ".mp4"})


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def test_skips_loose_files_at_root_by_default(tmp_path):
    _touch(tmp_path / "loose.mkv")
    _touch(tmp_path / "Movies" / "A" / "a.mkv")
    found = {p.relative_to(tmp_path) for p in walk_media_files(tmp_path, EXTENSIONS)}
    assert found == {Path("Movies/A/a.mkv")}


def test_skip_root_files_false_includes_root_level_files(tmp_path):
    _touch(tmp_path / "loose.mkv")
    found = {
        p.relative_to(tmp_path)
        for p in walk_media_files(tmp_path, EXTENSIONS, skip_root_files=False)
    }
    assert found == {Path("loose.mkv")}


def test_filters_by_extension(tmp_path):
    _touch(tmp_path / "Movies" / "a.mkv")
    _touch(tmp_path / "Movies" / "a.nfo")
    _touch(tmp_path / "Movies" / "a.srt")
    found = {p.relative_to(tmp_path) for p in walk_media_files(tmp_path, EXTENSIONS)}
    assert found == {Path("Movies/a.mkv")}


def test_skips_dotfiles_and_dot_directories(tmp_path):
    _touch(tmp_path / "Movies" / ".hidden.mkv")
    _touch(tmp_path / "Movies" / ".cache" / "a.mkv")
    _touch(tmp_path / "Movies" / "real.mkv")
    found = {p.relative_to(tmp_path) for p in walk_media_files(tmp_path, EXTENSIONS)}
    assert found == {Path("Movies/real.mkv")}


def test_skips_conventional_skip_dir_names(tmp_path):
    _touch(tmp_path / "Movies" / "@eaDir" / "a.mkv")
    _touch(tmp_path / "Movies" / "#recycle" / "b.mkv")
    _touch(tmp_path / "Movies" / "real.mkv")
    found = {p.relative_to(tmp_path) for p in walk_media_files(tmp_path, EXTENSIONS)}
    assert found == {Path("Movies/real.mkv")}


def test_path_filter_matches_relative_path_case_insensitively(tmp_path):
    _touch(tmp_path / "Movies" / "Dune" / "dune.mkv")
    _touch(tmp_path / "Movies" / "Other" / "other.mkv")
    found = {
        p.relative_to(tmp_path) for p in walk_media_files(tmp_path, EXTENSIONS, path_filter="dune")
    }
    assert found == {Path("Movies/Dune/dune.mkv")}


def test_limit_stops_after_n_files(tmp_path):
    for i in range(5):
        _touch(tmp_path / "Movies" / f"{i}.mkv")
    found = list(walk_media_files(tmp_path, EXTENSIONS, limit=2))
    assert len(found) == 2


def test_exclude_dirs_prunes_subtree_written_mid_walk(tmp_path):
    """The actual incident this module exists to prevent: an output
    directory inside root that gets walked into later in the same
    invocation and has its own freshly-written output rediscovered as a
    new source. Simulated here by pre-existing files in the excluded dir --
    the guard is a pure pruning check, it doesn't care when the files
    appeared."""
    _touch(tmp_path / "Movies" / "a.mkv")
    output_dir = tmp_path / "transcode"
    _touch(output_dir / "a.mkv")

    found_unguarded = {p.relative_to(tmp_path) for p in walk_media_files(tmp_path, EXTENSIONS)}
    assert found_unguarded == {Path("Movies/a.mkv"), Path("transcode/a.mkv")}

    found_guarded = {
        p.relative_to(tmp_path)
        for p in walk_media_files(
            tmp_path, EXTENSIONS, exclude_dirs=frozenset({output_dir.resolve()})
        )
    }
    assert found_guarded == {Path("Movies/a.mkv")}


def test_exclude_dirs_matches_via_resolved_path_not_bare_name(tmp_path):
    _touch(tmp_path / "Movies" / "a.mkv")
    output_dir = tmp_path / "nested" / "transcode"
    _touch(output_dir / "a.mkv")

    found = {
        p.relative_to(tmp_path)
        for p in walk_media_files(
            tmp_path, EXTENSIONS, exclude_dirs=frozenset({output_dir.resolve()})
        )
    }
    assert found == {Path("Movies/a.mkv")}
