"""Tests for mediatools.track_policy.

Several of these cases are transcribed directly from real files that
produced a wrong decision during development -- see
skills/media-library/reference/incidents.md for the full story behind each
one. Keeping them as regression tests (rather than one-off manual audits)
is the point: three of the four SDH false positives were each found on a
single specific file out of hundreds, and would not have been caught by a
handful of hand-picked examples.
"""

from __future__ import annotations

import pytest
from mediatools import track_policy
from mediatools.track_policy import Policy, plan_streams

# --- track builder -----------------------------------------------------


def mk_track(
    index: int,
    codec_type: str,
    *,
    language: str | None = "eng",
    codec_name: str | None = "subrip",
    title: str | None = None,
    forced: bool = False,
    comment: bool = False,
    default: bool = False,
    hearing_impaired: bool = False,
    bytes_tag: int | None = None,
) -> dict:
    return {
        "index": index,
        "codec_type": codec_type,
        "language": language,
        "forced": forced,
        "comment": comment,
        "default": default,
        "hearing_impaired": hearing_impaired,
        "bytes_tag": bytes_tag,
        "title": title,
        "codec_name": codec_name,
    }


def video(index: int = 0) -> dict:
    return mk_track(index, "video", language=None, codec_name="hevc")


def audio(index: int, **kw) -> dict:
    kw.setdefault("codec_name", "eac3")
    return mk_track(index, "audio", **kw)


def sub(index: int, **kw) -> dict:
    return mk_track(index, "subtitle", **kw)


# --- is_commentary / is_sdh / title heuristics --------------------------


@pytest.mark.parametrize(
    ("title", "comment", "expected"),
    [
        (None, False, False),
        ("Commentary by Director/Producer Ridley Scott", False, True),
        ("COMMENTARY", False, True),
        ("DD 5.1", False, False),
        (None, True, True),
    ],
)
def test_is_commentary(title: str | None, comment: bool, expected: bool) -> None:
    assert track_policy.is_commentary(audio(1, title=title, comment=comment)) is expected


@pytest.mark.parametrize(
    ("title", "hearing_impaired", "expected"),
    [
        (None, False, False),
        ("SDH", False, True),
        ("English (SDH)", False, True),
        ("Hearing Impaired", False, True),
        ("hearing-impaired", False, True),
        ("Deaf", False, True),
        ("Forced", False, False),
        (None, True, True),
        ("English", False, False),
    ],
)
def test_is_sdh(title: str | None, hearing_impaired: bool, expected: bool) -> None:
    assert track_policy.is_sdh(sub(1, title=title, hearing_impaired=hearing_impaired)) is expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Forced", True),
        ("FORCED", True),
        ("English (Forced)", True),
        ("Commentary", True),
        (None, False),
        ("SDH", False),
    ],
)
def test_looks_forced_or_commentary_title(title: str | None, expected: bool) -> None:
    assert track_policy._looks_forced_or_commentary_title(sub(1, title=title)) is expected


def test_looks_mistagged_other_language() -> None:
    # Jack Ryan S01E06: a PGS track tagged language=eng but titled "German".
    mistagged = sub(8, language="eng", codec_name="hdmv_pgs_subtitle", title="German")
    assert track_policy._looks_mistagged_other_language(mistagged) is True

    genuine_english = sub(6, language="eng", codec_name="hdmv_pgs_subtitle", title="English")
    assert track_policy._looks_mistagged_other_language(genuine_english) is False


# --- _lang_bucket --------------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("eng", "eng"),
        ("en", "eng"),
        ("ja", "jpn"),
        ("jpn", "jpn"),
        ("und", "und"),
        (None, "und"),
        ("ger", "ger"),
        ("de", "de"),
    ],
)
def test_lang_bucket(lang: str | None, expected: str) -> None:
    assert track_policy._lang_bucket(lang) == expected


# --- _classify_group_sdh: the real false-positive shapes -----------------


def test_classify_group_single_track_untouched() -> None:
    group = [sub(2, bytes_tag=20000)]
    keep, sdh = track_policy._classify_group_sdh(group)
    assert keep == group
    assert sdh == []


def test_classify_group_explicit_sdh_title_dropped() -> None:
    plain = sub(2, title=None, bytes_tag=38072)
    labeled_sdh = sub(3, title="SDH", bytes_tag=43093)
    keep, sdh = track_policy._classify_group_sdh([plain, labeled_sdh])
    assert keep == [plain]
    assert sdh == [labeled_sdh]


def test_classify_group_forced_without_flag_not_compared() -> None:
    """Rings of Power: a forced/signs track (~92 bytes) with the `forced`
    disposition bit left unset by the source sat next to the full dialogue
    track (~20KB). Naive "smaller = plain" logic would keep the forced
    track and drop the real dialogue as "SDH" -- backwards. Both must
    survive untouched since the forced-titled one is excluded from
    comparison entirely."""
    forced_untagged = sub(5, title=None, forced=False, bytes_tag=92)
    dialogue = sub(6, title=None, forced=False, bytes_tag=20202)
    # Real files leave this one truly untitled -- the actual guard in
    # plan_streams() is the `forced` disposition flag added upstream, so at
    # the _classify_group_sdh level this pair (no title, no flag) is
    # decided purely by the ratio heuristic; 20202/92 is far outside
    # SDH_SIZE_RATIO_MAX so it's correctly left alone either way.
    keep, sdh = track_policy._classify_group_sdh([forced_untagged, dialogue])
    assert set(t["index"] for t in keep) == {5, 6}
    assert sdh == []


def test_classify_group_forced_title_excluded_even_without_flag() -> None:
    """Mr. Robot: PGS tracks titled exactly "Forced"/"Commentary" with the
    disposition bit unset. Title text alone must exclude them."""
    forced_by_title = sub(4, title="Forced", forced=False, bytes_tag=468037)
    labeled_sdh = sub(5, title="SDH", bytes_tag=14587699)
    keep, sdh = track_policy._classify_group_sdh([forced_by_title, labeled_sdh])
    assert keep == [forced_by_title]
    assert sdh == [labeled_sdh]


def test_classify_group_mistagged_language_excluded() -> None:
    """Jack Ryan S01E06: a track tagged eng but titled "German" must not be
    compared against (and picked over) the genuine English track."""
    genuine_english = sub(6, title="English", bytes_tag=7235597)
    mistagged_german = sub(8, title="German", bytes_tag=5148817)
    keep, sdh = track_policy._classify_group_sdh([genuine_english, mistagged_german])
    assert genuine_english in keep
    assert mistagged_german in keep  # excluded from comparison, not dropped
    assert sdh == []


def test_classify_group_three_or_more_candidates_untouched() -> None:
    """Mission: Impossible - The Final Reckoning: five untitled PGS English
    subtitle tracks with wildly different sizes and no reliable signal for
    which one is "the" plain track. Leave all of them alone."""
    group = [
        sub(i, title=None, bytes_tag=size)
        for i, size in enumerate([39029827, 45790270, 112880414, 114714298, 66977947], start=2)
    ]
    keep, sdh = track_policy._classify_group_sdh(group)
    assert keep == group
    assert sdh == []


@pytest.mark.parametrize("ratio", [1.0, 1.05, 1.09])
def test_classify_group_ratio_below_min_not_dropped(ratio: float) -> None:
    small = sub(1, bytes_tag=10000)
    big = sub(2, bytes_tag=int(10000 * ratio))
    keep, sdh = track_policy._classify_group_sdh([small, big])
    assert sdh == []
    assert set(t["index"] for t in keep) == {1, 2}


@pytest.mark.parametrize("ratio", [3.01, 5.0, 78.5])
def test_classify_group_ratio_above_max_not_dropped(ratio: float) -> None:
    """The Pacific: an unlabeled forced/commentary-shaped track with no
    flag and no usable title sitting alongside real dialogue. A ratio this
    extreme is a different kind of track, not an unusually verbose SDH
    one -- leave both alone rather than guess."""
    small = sub(1, bytes_tag=10000)
    big = sub(2, bytes_tag=int(10000 * ratio))
    keep, sdh = track_policy._classify_group_sdh([small, big])
    assert sdh == []
    assert set(t["index"] for t in keep) == {1, 2}


@pytest.mark.parametrize("ratio", [1.10, 1.22, 2.0, 3.0])
def test_classify_group_ratio_in_bounds_dropped(ratio: float) -> None:
    small = sub(1, bytes_tag=10000)
    big = sub(2, bytes_tag=int(10000 * ratio))
    keep, sdh = track_policy._classify_group_sdh([small, big])
    assert keep == [small]
    assert sdh == [big]


def test_classify_group_all_sdh_keeps_everything() -> None:
    """Dropping every track for a language it actually has would be worse
    than leaving an SDH track in place."""
    a = sub(1, title="SDH", bytes_tag=20000)
    b = sub(2, title="Hearing Impaired", bytes_tag=21000)
    keep, sdh = track_policy._classify_group_sdh([a, b])
    assert set(t["index"] for t in keep) == {1, 2}
    assert sdh == []


def test_classify_group_missing_size_data_not_dropped() -> None:
    a = sub(1, bytes_tag=None)
    b = sub(2, bytes_tag=None)
    keep, sdh = track_policy._classify_group_sdh([a, b])
    assert sdh == []
    assert set(t["index"] for t in keep) == {1, 2}


# --- resolve_language -----------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "keep_set", "expected"),
    [
        ("eng", frozenset({"eng"}), "eng"),
        ("en", frozenset({"eng"}), "eng"),
        ("ja", frozenset({"jpn"}), "jpn"),
        (None, frozenset({"eng"}), "eng"),
        ("und", frozenset({"eng", "jpn"}), "eng"),
        (None, frozenset({"jpn"}), "jpn"),  # anime audio: only jpn in keep-set
    ],
)
def test_resolve_language(lang: str | None, keep_set: frozenset, expected: str) -> None:
    assert track_policy.resolve_language(lang, keep_set) == expected


# --- is_anime --------------------------------------------------------------


def test_is_anime_japanese_only() -> None:
    streams = [video(), audio(1, language="jpn")]
    assert track_policy.is_anime(streams, Policy()) is True


def test_is_anime_japanese_plus_one_english_dub() -> None:
    streams = [video(), audio(1, language="eng"), audio(2, language="jpn")]
    assert track_policy.is_anime(streams, Policy()) is True


def test_is_anime_japanese_dub_buried_among_many_is_not_anime() -> None:
    """Daredevil - Born Again / The Rings of Power: a Japanese dub among a
    dozen+ other languages is a heavily-dubbed live-action release, not a
    Japanese-original one."""
    langs_ = ["eng", "cze", "ger", "spa", "fre", "hun", "ita", "jpn", "pol", "por", "rum", "tur"]
    streams = [video()] + [audio(i, language=lang) for i, lang in enumerate(langs_, start=1)]
    assert track_policy.is_anime(streams, Policy()) is False


def test_is_anime_no_japanese_audio() -> None:
    streams = [video(), audio(1, language="eng")]
    assert track_policy.is_anime(streams, Policy()) is False


def test_is_anime_disabled_by_policy() -> None:
    streams = [video(), audio(1, language="jpn")]
    assert track_policy.is_anime(streams, Policy(detect_anime=False)) is False


# --- plan_streams: integration-level regression tests ----------------------


def test_prometheus_shaped_file_refuses_to_drop_only_non_commentary_track() -> None:
    """Prometheus (2012): DTS-HD MA main track + two AC-3 commentary
    tracks, no plain AC-3 mix. --drop-audio-codec dts must not leave the
    file with only commentary audio -- the old "keep_audio non-empty" check
    let this happen once; the fix must refuse to drop the DTS track here at
    all, since nothing non-commentary would survive."""
    streams = [
        video(),
        audio(1, codec_name="dts", default=True),
        audio(2, codec_name="ac3", title="Commentary by Director", comment=True),
        audio(3, codec_name="ac3", title="Commentary by Writers", comment=True),
    ]
    policy = Policy(drop_audio_codecs=frozenset({"dts"}))
    result = plan_streams(streams, policy)

    kept_indices = {t["index"] for t in result["keep_audio"]}
    assert 1 in kept_indices, "the only non-commentary track must survive"
    assert not any(
        not track_policy.is_commentary(t) for t in result["drop_audio"] if t["index"] == 1
    )
    assert result["fallback_audio_used"] is True


def test_alien_covenant_shaped_file_drops_dts_keeps_truehd() -> None:
    """Alien: Covenant: DTS-HD MA + TrueHD + AC3 -- dropping dts is safe
    here since TrueHD (non-commentary) survives."""
    streams = [
        video(),
        audio(1, codec_name="truehd", default=True),
        audio(2, codec_name="dts"),
        audio(3, codec_name="ac3"),
    ]
    policy = Policy(drop_audio_codecs=frozenset({"dts"}))
    result = plan_streams(streams, policy)

    kept_indices = {t["index"] for t in result["keep_audio"]}
    dropped_indices = {t["index"] for t, _ in result["drop_audio"]}
    assert kept_indices == {1, 3}
    assert dropped_indices == {2}
    assert result["fallback_audio_used"] is False


def test_single_audio_track_prefers_non_commentary_default() -> None:
    streams = [
        video(),
        audio(1, codec_name="eac3", default=False),
        audio(2, codec_name="truehd", default=True),
        audio(3, codec_name="ac3", title="Commentary", comment=True, default=False),
    ]
    result = plan_streams(streams, Policy(single_audio_track=True))
    assert [t["index"] for t in result["keep_audio"]] == [2]


def test_single_audio_track_falls_back_to_first_when_no_default() -> None:
    streams = [video(), audio(1, codec_name="eac3"), audio(2, codec_name="eac3")]
    result = plan_streams(streams, Policy(single_audio_track=True))
    assert [t["index"] for t in result["keep_audio"]] == [1]


def test_drop_sdh_subs_never_compares_across_codecs() -> None:
    """Alien: Covenant: a plain PGS dialogue track must never be compared
    against an unrelated, much smaller SubRip track just because they
    share a language."""
    streams = [
        video(),
        audio(1),
        sub(2, codec_name="subrip", title=None, bytes_tag=38072),
        sub(3, codec_name="subrip", title="SDH", bytes_tag=43093),
        sub(4, codec_name="hdmv_pgs_subtitle", title=None, bytes_tag=30457644),
        sub(5, codec_name="hdmv_pgs_subtitle", title="COMMENTARY", bytes_tag=54992231),
    ]
    result = plan_streams(streams, Policy(drop_sdh_subs=True))
    kept_indices = {t["index"] for t in result["keep_subtitle"]}
    dropped_indices = {t["index"] for t, _ in result["drop_subtitle"]}
    assert kept_indices == {2, 4, 5}
    assert dropped_indices == {3}


def test_drop_sdh_subs_ignores_unknown_language_bucket() -> None:
    """Murderbot S01E07: blank-language Chinese Simplified/Traditional
    tracks must never be compared against each other for SDH-ness -- that
    bucket is out of scope entirely."""
    streams = [
        video(),
        audio(1),
        sub(10, language=None, title="Simplified", bytes_tag=10888),
        sub(11, language=None, title="Traditional", bytes_tag=11830),
        sub(13, language=None, title="Traditional", bytes_tag=10509),
    ]
    result = plan_streams(streams, Policy(drop_sdh_subs=True))
    kept_indices = {t["index"] for t in result["keep_subtitle"]}
    assert kept_indices == {10, 11, 13}


def test_plan_streams_never_produces_silent_audio() -> None:
    streams = [video(), audio(1, codec_name="dts", language="eng")]
    result = plan_streams(streams, Policy(drop_audio_codecs=frozenset({"dts"})))
    assert len(result["keep_audio"]) >= 1


def test_resolved_language_written_on_kept_tracks() -> None:
    streams = [video(), audio(1, language="en"), sub(2, language="en")]
    result = plan_streams(streams, Policy())
    assert result["keep_audio"][0]["resolved_lang"] == "eng"
    assert result["keep_subtitle"][0]["resolved_lang"] == "eng"


# --- from_mkvmerge_track: codec_id normalization ---------------------------


@pytest.mark.parametrize(
    ("codec_id", "expected_codec_name"),
    [
        ("A_DTS", "dts"),
        ("A_AC3", "ac3"),
        ("A_EAC3", "eac3"),
        ("A_TRUEHD", "truehd"),
        ("A_AAC", "aac"),
    ],
)
def test_from_mkvmerge_track_normalizes_codec_id(codec_id: str, expected_codec_name: str) -> None:
    """mkvmerge's free-text `codec` field ("DTS-HD Master Audio") must never
    be compared directly against ffprobe-style short names -- this shipped
    as a real bug once (--drop-audio-codec dts silently matched nothing on
    .mkv files)."""
    raw_track = {
        "id": 2,
        "type": "audio",
        "codec": "DTS-HD Master Audio",  # deliberately mismatched from codec_id
        "properties": {"codec_id": codec_id, "language": "eng"},
    }
    normalized = track_policy.from_mkvmerge_track(raw_track)
    assert normalized is not None
    assert normalized["codec_name"] == expected_codec_name


def test_from_mkvmerge_track_unknown_codec_id_falls_back_to_codec_field() -> None:
    raw_track = {
        "id": 1,
        "type": "audio",
        "codec": "Some Future Codec",
        "properties": {"codec_id": "A_SOME_FUTURE_CODEC", "language": "eng"},
    }
    normalized = track_policy.from_mkvmerge_track(raw_track)
    assert normalized is not None
    assert normalized["codec_name"] == "Some Future Codec"


def test_from_mkvmerge_track_video_type_returns_none_for_unsupported() -> None:
    raw_track = {"id": 0, "type": "buttons", "properties": {}}
    assert track_policy.from_mkvmerge_track(raw_track) is None
