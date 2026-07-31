from __future__ import annotations

import json

import pytest

from psammophis.trackstrip import scan as scan_mod
from psammophis.trackstrip.cli import build_parser


def test_scan_cache_cannot_overwrite_media_input(monkeypatch, tmp_path):
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"irreplaceable")
    monkeypatch.setattr(scan_mod, "walk_media_files", lambda *_args, **_kwargs: iter([media]))

    with pytest.raises(ValueError, match="overwrite a media input"):
        scan_mod.scan(tmp_path, media)

    assert media.read_bytes() == b"irreplaceable"


def test_cache_from_a_different_library_is_never_reused(monkeypatch, tmp_path):
    root = tmp_path / "new-library"
    root.mkdir()
    media = root / "movie.mkv"
    media.write_bytes(b"same-looking-file")
    stat = media.stat()
    cache_path = tmp_path / "scan.json"
    cache_path.write_text(
        json.dumps(
            {
                "version": scan_mod.CACHE_VERSION,
                "root": str(tmp_path / "old-library"),
                "generated_at": 1,
                "files": {
                    "movie.mkv": {
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "format": {},
                        "streams": [],
                    }
                },
            }
        )
    )
    calls = []
    monkeypatch.setattr(scan_mod, "walk_media_files", lambda *_args, **_kwargs: iter([media]))
    monkeypatch.setattr(
        scan_mod,
        "probe_file",
        lambda path: calls.append(path) or {"format": {}, "streams": []},
    )

    result = scan_mod.scan(root, cache_path, jobs=1)

    assert calls == [media]
    assert result["root"] == str(root.resolve())


def test_scan_jobs_must_be_positive():
    parser = build_parser("/library", "/cache.json")
    with pytest.raises(SystemExit):
        parser.parse_args(["scan", "--jobs", "0"])
