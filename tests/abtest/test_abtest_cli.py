from __future__ import annotations

from psammophis.abtest.cli import build_parser, main
from psammophis.compare.probe import VideoInfo
from psammophis.medialib.svt import SvtImplementation


def _info(path: str) -> VideoInfo:
    return VideoInfo(
        path=path,
        duration=900.0,
        size=3_000_000_000,
        bit_rate=30_000_000,
        width=3840,
        height=2160,
        frame_rate=24.0,
        frame_count=21600,
        pix_fmt="yuv420p",
        color_primaries="bt709",
        color_transfer="bt709",
        color_space="bt709",
        color_range="tv",
    )


def test_parser_requires_source_and_variants():
    parser = build_parser("/media")
    args = parser.parse_args(
        [
            "--source",
            "movie.mkv",
            "--variant",
            "a=2160p-film",
            "--variant",
            "b=2160p-film,crf=24",
            "--at",
            "10,40",
        ]
    )
    assert args.source == "movie.mkv"
    assert args.yes is False
    assert len(args.variants) == 2


def test_dry_run_prints_plan_and_does_not_encode(tmp_path, monkeypatch, capsys):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"x")

    monkeypatch.setattr("psammophis.abtest.cli.probe_video", lambda _path: _info(str(source)))
    monkeypatch.setattr(
        "psammophis.abtest.cli.detect_svt_implementation",
        lambda: SvtImplementation("svt-av1-hdr", "v4.1.0", "test"),
    )

    code = main(
        [
            "--root",
            str(tmp_path),
            "--source",
            str(source),
            "--variant",
            "current=2160p-film",
            "--variant",
            "cand=2160p-film,crf=24",
            "--at",
            "55,120",
            "--output-dir",
            str(tmp_path / "ab"),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "A/B PLAN" in out
    assert "current:" in out
    assert "libsvtav1" in out
    assert not (tmp_path / "ab" / "enc").exists()


def test_single_variant_is_rejected(tmp_path, capsys):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"x")
    code = main(
        [
            "--root",
            str(tmp_path),
            "--source",
            str(source),
            "--variant",
            "only=2160p-film",
        ]
    )
    assert code == 2
    assert "at least two" in capsys.readouterr().err
