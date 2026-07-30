from __future__ import annotations

from pathlib import Path

import pytest
from medialib.libroot import (
    find_agents_root,
    find_library_root,
    find_own_script_path,
    to_absolute_preserving_symlinks,
)


def _make_checkout(tmp_path, marker_name=".agents"):
    agents_dir = tmp_path / "media_library" / marker_name
    script_path = agents_dir / "skills" / "some-skill" / "scripts" / "somepkg" / "cli.py"
    script_path.parent.mkdir(parents=True)
    script_path.touch()
    return script_path, agents_dir


def test_find_agents_root_locates_marker_ancestor(tmp_path):
    script_path, agents_dir = _make_checkout(tmp_path)
    assert find_agents_root(script_path) == agents_dir


def test_find_library_root_returns_marker_parent(tmp_path):
    script_path, agents_dir = _make_checkout(tmp_path)
    assert find_library_root(script_path) == agents_dir.parent


def test_find_agents_root_honors_custom_marker_name(tmp_path):
    script_path, agents_dir = _make_checkout(tmp_path, marker_name="custom-checkout")
    assert find_agents_root(script_path, marker_name="custom-checkout") == agents_dir


def test_find_agents_root_raises_when_marker_not_in_ancestry(tmp_path):
    script_path = tmp_path / "somewhere" / "else" / "cli.py"
    script_path.parent.mkdir(parents=True)
    script_path.touch()
    with pytest.raises(RuntimeError, match=r"\.agents"):
        find_agents_root(script_path)


def _make_symlinked_cwd(tmp_path):
    """A real_target/ dir and a symlinked_name/ symlink pointing at it --
    mirrors this repo's own `.agents` symlinked into a media library."""
    real_dir = tmp_path / "real_target"
    symlinked_dir = tmp_path / "symlinked_name"
    real_dir.mkdir()
    symlinked_dir.symlink_to(real_dir, target_is_directory=True)
    return real_dir, symlinked_dir


def test_to_absolute_preserving_symlinks_is_noop_for_absolute_path(tmp_path):
    absolute = tmp_path / "some" / "file.py"
    assert to_absolute_preserving_symlinks(absolute) == absolute


def test_to_absolute_preserving_symlinks_prefers_pwd_when_it_matches_cwd(tmp_path, monkeypatch):
    real_dir, symlinked_dir = _make_symlinked_cwd(tmp_path)
    monkeypatch.chdir(real_dir)
    monkeypatch.setenv("PWD", str(symlinked_dir))

    result = to_absolute_preserving_symlinks(Path("script.py"))
    assert result == symlinked_dir / "script.py"


def test_to_absolute_preserving_symlinks_falls_back_to_cwd_when_pwd_unset(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PWD", raising=False)

    result = to_absolute_preserving_symlinks(Path("script.py"))
    assert result == tmp_path / "script.py"


def test_to_absolute_preserving_symlinks_ignores_pwd_pointing_elsewhere(tmp_path, monkeypatch):
    other_dir = tmp_path / "unrelated"
    other_dir.mkdir()
    real_cwd = tmp_path / "actual_cwd"
    real_cwd.mkdir()

    monkeypatch.chdir(real_cwd)
    monkeypatch.setenv("PWD", str(other_dir))  # stale/wrong -- a different directory entirely

    result = to_absolute_preserving_symlinks(Path("script.py"))
    assert result == real_cwd / "script.py"


def test_find_own_script_path_reconstructs_symlinked_argv0(tmp_path, monkeypatch):
    real_dir, symlinked_dir = _make_symlinked_cwd(tmp_path)
    script = real_dir / "probe.py"
    script.touch()

    monkeypatch.chdir(real_dir)
    monkeypatch.setenv("PWD", str(symlinked_dir))
    monkeypatch.setattr("sys.argv", ["probe.py"])

    assert find_own_script_path(str(script)) == symlinked_dir / "probe.py"


def test_find_own_script_path_falls_back_when_argv0_does_not_match_dunder_file(
    tmp_path, monkeypatch
):
    real_script = tmp_path / "real.py"
    real_script.touch()

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.setattr("sys.argv", ["-c"])  # e.g. python3 -c "..." / an unusual launcher

    assert find_own_script_path(str(real_script)) == real_script


def test_find_own_script_path_falls_back_when_argv0_target_does_not_exist(tmp_path, monkeypatch):
    real_script = tmp_path / "real.py"
    real_script.touch()

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.setattr("sys.argv", ["nonexistent.py"])

    assert find_own_script_path(str(real_script)) == real_script
