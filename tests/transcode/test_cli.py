from __future__ import annotations

import pytest

from psammophis.transcode.cli import _cache_path, _output_collisions, build_parser


def test_default_cache_uses_transcode_namespace(tmp_path):
    assert _cache_path(tmp_path, "originals") == (tmp_path / ".cache" / "transcode" / "originals")


def test_explicit_cache_path_wins(tmp_path):
    explicit = tmp_path / "custom"
    assert _cache_path(tmp_path, "logs", str(explicit)) == explicit


def test_parser_uses_public_command_name(tmp_path):
    assert build_parser(str(tmp_path)).prog == "psammophis transcode"


def test_flat_output_collisions_are_detected_before_encoding(tmp_path):
    output = tmp_path / "converted"
    first = tmp_path / "Movies" / "Title" / "feature.mp4"
    second = tmp_path / "TV" / "Title" / "feature.mkv"

    assert _output_collisions(output, [first, second]) == {output / "feature.mkv": [first, second]}


@pytest.mark.parametrize("value", ["nan", "inf", "-0.1", "1.1"])
def test_grain_threshold_must_be_a_finite_score(tmp_path, value):
    parser = build_parser(str(tmp_path))
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--grain-threshold", value])


def test_bitrate_cap_flags_are_mutually_exclusive(tmp_path):
    parser = build_parser(str(tmp_path))
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "run",
                "--max-bitrate-fraction",
                "0.5",
                "--no-bitrate-cap",
            ]
        )
