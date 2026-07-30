from pathlib import Path

import pytest
from mkvedit.model import (
    MetadataError,
    cover_attachments,
    parse_container,
    select_track,
)


def _data():
    return {
        "tracks": [
            {
                "id": 0,
                "type": "video",
                "properties": {"uid": 100, "default_track": True},
            },
            {
                "id": 1,
                "type": "audio",
                "properties": {"uid": 200, "language": "eng", "default_track": True},
            },
            {
                "id": 2,
                "type": "audio",
                "properties": {"uid": 201, "language_ietf": "ja", "default_track": False},
            },
            {
                "id": 3,
                "type": "subtitles",
                "properties": {"uid": 300, "language": "eng"},
            },
        ],
        "attachments": [
            {
                "id": 0,
                "file_name": "cover.jpg",
                "content_type": "image/jpeg",
                "properties": {"uid": 400},
            }
        ],
    }


def test_parse_and_select_track_forms():
    container = parse_container(Path("movie.mkv"), _data())
    assert select_track(container, "uid:201").id == 2
    assert select_track(container, "id:1").uid == 200
    assert select_track(container, "audio:2").uid == 201
    assert select_track(container, "video:1").uid == 100
    assert cover_attachments(container)[0].uid == 400


@pytest.mark.parametrize("selector", ["audio:0", "audio:3", "id:99", "bogus:1", "audio:x"])
def test_invalid_or_missing_selector_raises(selector):
    container = parse_container(Path("movie.mkv"), _data())
    with pytest.raises(MetadataError):
        select_track(container, selector)
