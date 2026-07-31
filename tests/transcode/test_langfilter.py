from __future__ import annotations

from psammophis.transcode import langfilter


def _audio(index, language, channels=2, codec_name="aac", profile=None, bit_rate=None):
    return {
        "index": index,
        "codec_name": codec_name,
        "profile": profile,
        "channels": channels,
        "bit_rate": bit_rate,
        "language": language,
    }


def _sub(index, language, title=None, hearing_impaired=False):
    return {
        "index": index,
        "codec_name": "subrip",
        "language": language,
        "title": title,
        "hearing_impaired": hearing_impaired,
    }


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


# --- filter_audio: language matching, single=False (keep every match) ---


def test_filter_audio_keeps_only_matching_language():
    streams = [_audio(1, "eng"), _audio(2, "jpn"), _audio(3, "eng")]
    kept, fallback = langfilter.filter_audio(streams, "eng", single=False)
    assert [s["index"] for s in kept] == [1, 3]
    assert fallback is False


def test_filter_audio_falls_back_to_all_when_nothing_matches():
    streams = [_audio(1, "jpn"), _audio(2, "spa")]
    kept, fallback = langfilter.filter_audio(streams, "eng", single=False)
    assert kept == streams
    assert fallback is True


def test_filter_audio_all_sentinel_keeps_everything_even_with_single_true():
    streams = [_audio(1, "eng"), _audio(2, "jpn")]
    kept, fallback = langfilter.filter_audio(streams, "all")
    assert kept == streams
    assert fallback is False


def test_filter_audio_empty_input_stays_empty_not_a_fallback():
    kept, fallback = langfilter.filter_audio([], "eng")
    assert kept == []
    assert fallback is False


# --- filter_audio: single=True (the default) reduces to one best track ---


def test_filter_audio_default_reduces_to_single_best_track():
    streams = [
        _audio(1, "eng", codec_name="truehd", profile="Dolby TrueHD + Dolby Atmos", channels=8),
        _audio(2, "eng", codec_name="eac3", channels=8, bit_rate=1_664_000),
    ]
    kept, fallback = langfilter.filter_audio(streams, "eng")
    assert len(kept) == 1
    assert kept[0]["index"] == 1  # the TrueHD Atmos track, not the E-AC3 compat copy
    assert fallback is False


def test_filter_audio_single_still_applies_within_fallback_pool():
    streams = [
        _audio(1, "jpn", codec_name="truehd", channels=8),
        _audio(2, "jpn", codec_name="ac3", channels=6),
    ]
    kept, fallback = langfilter.filter_audio(streams, "eng")  # nothing is "eng"
    assert fallback is True
    assert len(kept) == 1
    assert kept[0]["index"] == 1  # still picks the best of the fallback pool


# --- codec quality ranking ---


def test_pick_best_audio_prefers_lossless_over_lossy_regardless_of_bitrate():
    truehd = _audio(1, "eng", codec_name="truehd", channels=8, bit_rate=3_000_000)
    eac3 = _audio(2, "eng", codec_name="eac3", channels=8, bit_rate=1_664_000)
    assert langfilter.pick_best_audio([eac3, truehd]) is truehd


def test_pick_best_audio_distinguishes_dts_hd_ma_from_dts_core_via_profile():
    dts_core = _audio(1, "eng", codec_name="dts", profile="DTS", channels=6)
    dts_ma = _audio(2, "eng", codec_name="dts", profile="DTS-HD MA", channels=6)
    assert langfilter.pick_best_audio([dts_core, dts_ma]) is dts_ma


def test_pick_best_audio_breaks_ties_by_channels_then_bitrate():
    stereo = _audio(1, "eng", codec_name="ac3", channels=2, bit_rate=192_000)
    surround = _audio(2, "eng", codec_name="ac3", channels=6, bit_rate=448_000)
    assert langfilter.pick_best_audio([stereo, surround]) is surround


def test_pick_best_audio_empty_list_returns_none():
    assert langfilter.pick_best_audio([]) is None


# --- SDH detection and subtitle filtering ---


def test_is_sdh_via_hearing_impaired_disposition():
    assert langfilter.is_sdh(_sub(1, "eng", hearing_impaired=True)) is True


def test_is_sdh_via_title_text():
    assert langfilter.is_sdh(_sub(1, "eng", title="English (SDH)")) is True
    assert langfilter.is_sdh(_sub(1, "eng", title="English [Hearing Impaired]")) is True


def test_is_sdh_false_for_plain_subtitle():
    assert langfilter.is_sdh(_sub(1, "eng", title="English")) is False
    assert langfilter.is_sdh(_sub(1, "eng")) is False


def test_filter_subtitles_prefers_plain_over_sdh():
    plain = _sub(1, "eng", title="English")
    sdh = _sub(2, "eng", title="English (SDH)", hearing_impaired=True)
    kept = langfilter.filter_subtitles([plain, sdh], "eng")
    assert kept == [plain]


def test_filter_subtitles_keeps_sdh_when_its_the_only_option():
    sdh = _sub(2, "eng", hearing_impaired=True)
    kept = langfilter.filter_subtitles([sdh], "eng")
    assert kept == [sdh]


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
