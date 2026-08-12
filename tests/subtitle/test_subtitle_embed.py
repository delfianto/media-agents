from pathlib import Path

from psammophis.subtitle.embed import _subtitle_language_already_present, build_command


def test_build_command_clears_old_default_and_sets_new_one():
    video = Path("/Show/S01E01.mkv")
    srt = Path("/Show/S01E01.en.srt")
    out = Path("/Show/.S01E01.subtitle-embed-tmp.mkv")
    cmd = build_command(video, srt, out, "en", default_subtitle_ids=[3, 5])

    assert cmd[:3] == ["mkvmerge", "-o", str(out)]
    assert "3:0" in cmd
    assert "5:0" in cmd
    assert str(video) in cmd
    assert "--language" in cmd
    lang_idx = cmd.index("--language")
    assert cmd[lang_idx + 1] == "0:en"
    assert "0:1" in cmd
    assert cmd[-1] == str(srt)


def test_build_command_no_defaults_to_clear():
    cmd = build_command(
        Path("/v.mkv"), Path("/s.srt"), Path("/out.mkv"), "en", default_subtitle_ids=[]
    )
    assert cmd.count("--default-track-flag") == 1  # only the new track's own flag


def _subtitle_track(language=None, language_ietf=None):
    props = {}
    if language is not None:
        props["language"] = language
    if language_ietf is not None:
        props["language_ietf"] = language_ietf
    return {"type": "subtitles", "properties": props}


def test_language_already_present_matches_iso_639_2():
    tracks = [_subtitle_track(language="eng")]
    assert _subtitle_language_already_present(tracks, "en")


def test_language_already_present_matches_ietf_tag():
    tracks = [_subtitle_track(language_ietf="en")]
    assert _subtitle_language_already_present(tracks, "en")


def test_language_already_present_false_for_different_language():
    tracks = [_subtitle_track(language="fre")]
    assert not _subtitle_language_already_present(tracks, "en")


def test_language_already_present_ignores_non_subtitle_tracks():
    tracks = [{"type": "audio", "properties": {"language": "eng"}}]
    assert not _subtitle_language_already_present(tracks, "en")


def test_language_already_present_empty_tracks():
    assert not _subtitle_language_already_present([], "en")
