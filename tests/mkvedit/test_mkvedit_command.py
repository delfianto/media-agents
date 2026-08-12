import pytest

from psammophis.mkvedit.command import Edits, build_command, is_cover_only
from psammophis.mkvedit.model import Attachment, Container, MetadataError, Track


def _container(tmp_path, covers=()):
    return Container(
        tmp_path / "Movie With Spaces.mkv",
        (
            Track(0, 100, "video", None, None, True),
            Track(1, 200, "audio", "eng", "English", True),
            Track(2, 201, "audio", "jpn", "Japanese", False),
            Track(3, 300, "subtitle", "eng", None, False),
            Track(4, 301, "subtitle", "spa", None, True),
        ),
        tuple(covers),
    )


def test_language_name_and_flags_use_stable_uid(tmp_path):
    command = build_command(
        _container(tmp_path),
        Edits(
            track_selector="id:3",
            track_name="English SDH",
            language="en-US",
            flags={"forced": True, "hearing-impaired": True},
        ),
    )
    assert command[:3] == [
        "mkvpropedit",
        str(tmp_path / "Movie With Spaces.mkv"),
        "--abort-on-warnings",
    ]
    assert "track:=300" in command
    assert "name=English SDH" in command
    assert "language=en-US" in command
    assert "flag-forced=1" in command


def test_default_subtitle_clears_other_subtitle_defaults(tmp_path):
    command = build_command(_container(tmp_path), Edits(defaults={"subtitle": "subtitle:1"}))
    first = command.index("track:=300")
    second = command.index("track:=301")
    assert command[first + 1 : first + 3] == ["--set", "flag-default=1"]
    assert command[second + 1 : second + 3] == ["--set", "flag-default=0"]


def test_default_type_mismatch_is_rejected(tmp_path):
    with pytest.raises(MetadataError, match="not requested subtitle"):
        build_command(_container(tmp_path), Edits(defaults={"subtitle": "audio:1"}))


def test_generic_default_track_infers_type_from_uid(tmp_path):
    command = build_command(_container(tmp_path), Edits(defaults={"*": "uid:201"}))
    selected = command.index("track:=201")
    assert command[selected + 1 : selected + 3] == ["--set", "flag-default=1"]


def test_cover_add_replace_and_delete(tmp_path):
    image = tmp_path / "poster.jpg"
    image.write_bytes(b"image")
    add = build_command(_container(tmp_path), Edits(cover=image))
    assert add[-2:] == ["--add-attachment", str(image)]

    cover = Attachment(0, 400, "cover.jpg", "image/jpeg")
    replace = build_command(_container(tmp_path, [cover]), Edits(cover=image))
    assert replace[-2:] == ["--replace-attachment", f"=400:{image}"]

    delete = build_command(_container(tmp_path, [cover]), Edits(delete_cover=True))
    assert delete[-2:] == ["--delete-attachment", "=400"]


def test_title_tags_and_chapters(tmp_path):
    tags = tmp_path / "tags.xml"
    chapters = tmp_path / "chapters.xml"
    tags.write_text("<Tags/>", encoding="utf-8")
    chapters.write_text("<Chapters/>", encoding="utf-8")
    command = build_command(
        _container(tmp_path),
        Edits(title="Movie", tags=tags, chapters=chapters),
    )
    assert "title=Movie" in command
    assert f"all:{tags}" in command
    assert str(chapters) in command


def test_no_mutation_is_rejected(tmp_path):
    with pytest.raises(MetadataError, match="no metadata mutation"):
        build_command(_container(tmp_path), Edits())


def test_clear_all_track_names_deletes_only_named_tracks(tmp_path):
    command = build_command(_container(tmp_path), Edits(clear_all_track_names=True))
    assert command.count("--delete") == 2
    for uid in (200, 201):
        idx = command.index(f"track:={uid}")
        assert command[idx + 1 : idx + 3] == ["--delete", "name"]
    for uid in (100, 300, 301):
        assert f"track:={uid}" not in command


def test_clear_all_track_names_combines_with_title(tmp_path):
    command = build_command(_container(tmp_path), Edits(title="Movie", clear_all_track_names=True))
    assert "title=Movie" in command
    assert command.count("--delete") == 2


def test_clear_all_track_names_conflicts_with_track_name(tmp_path):
    with pytest.raises(MetadataError, match="cannot be combined"):
        build_command(
            _container(tmp_path),
            Edits(track_selector="id:1", track_name="Foo", clear_all_track_names=True),
        )


def test_is_cover_only(tmp_path):
    image = tmp_path / "poster.jpg"
    assert is_cover_only(Edits(cover=image))
    assert is_cover_only(Edits(delete_cover=True))
    assert not is_cover_only(Edits(cover=image, title="Movie"))
    assert not is_cover_only(Edits(cover=image, track_selector="audio:1", language="en"))
    assert not is_cover_only(Edits(cover=image, defaults={"audio": "audio:1"}))
    assert not is_cover_only(Edits(cover=image, clear_all_track_names=True))
    assert not is_cover_only(Edits(cover=image, tags=tmp_path / "tags.xml"))
    assert not is_cover_only(Edits(cover=image, chapters=tmp_path / "chapters.xml"))
    assert not is_cover_only(Edits(title="Movie"))
