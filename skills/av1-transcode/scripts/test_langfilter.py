from __future__ import annotations

from av1transcode import langfilter


def _audio(index, language, channels=2):
    return {"index": index, "codec_name": "aac", "channels": channels, "language": language}


def _sub(index, language):
    return {"index": index, "codec_name": "subrip", "language": language}


def test_normalize_lang_maps_2_letter_aliases():
    assert langfilter.normalize_lang("en") == "eng"
    assert langfilter.normalize_lang("EN") == "eng"
    assert langfilter.normalize_lang("ja") == "jpn"


def test_normalize_lang_passes_through_unknown_codes_unchanged():
    assert langfilter.normalize_lang("eng") == "eng"
    assert langfilter.normalize_lang("xyz") == "xyz"


def test_track_matches_handles_none_language():
    assert langfilter.track_matches(None, "eng") is False


def test_track_matches_cross_form():
    assert langfilter.track_matches("eng", "en") is True
    assert langfilter.track_matches("en", "eng") is True
    assert langfilter.track_matches("jpn", "eng") is False


def test_filter_audio_keeps_only_matching_language():
    streams = [_audio(1, "eng"), _audio(2, "jpn"), _audio(3, "eng")]
    kept, fallback = langfilter.filter_audio(streams, "eng")
    assert [s["index"] for s in kept] == [1, 3]
    assert fallback is False


def test_filter_audio_falls_back_to_all_when_nothing_matches():
    streams = [_audio(1, "jpn"), _audio(2, "spa")]
    kept, fallback = langfilter.filter_audio(streams, "eng")
    assert kept == streams
    assert fallback is True


def test_filter_audio_all_sentinel_keeps_everything():
    streams = [_audio(1, "eng"), _audio(2, "jpn")]
    kept, fallback = langfilter.filter_audio(streams, "all")
    assert kept == streams
    assert fallback is False


def test_filter_audio_empty_input_stays_empty_not_a_fallback():
    kept, fallback = langfilter.filter_audio([], "eng")
    assert kept == []
    assert fallback is False


def test_filter_subtitles_keeps_only_matching_language():
    streams = [_sub(4, "eng"), _sub(5, "fre")]
    kept = langfilter.filter_subtitles(streams, "eng")
    assert [s["index"] for s in kept] == [4]


def test_filter_subtitles_no_match_returns_empty_not_a_fallback():
    streams = [_sub(4, "jpn")]
    assert langfilter.filter_subtitles(streams, "eng") == []


def test_filter_subtitles_all_sentinel_keeps_everything():
    streams = [_sub(4, "eng"), _sub(5, "fre")]
    assert langfilter.filter_subtitles(streams, "all") == streams
