"""Plain by-language keep/drop filtering for audio/subtitle tracks, plus
picking a single best-quality audio track and preferring plain over SDH
subtitles once language has narrowed the candidates.

Deliberately simple -- no full anime/commentary policy engine, unlike
track-strip's track_policy.py, which owns that for whole-library track
selection. This exists so the common case (keep the best English audio
track, keep plain English subtitles, drop the rest) is available directly
in transcode without a separate track-strip pass first.
"""

# A handful of common ISO 639-1 (2-letter) -> ISO 639-2 (3-letter) aliases,
# since ffprobe/mkvmerge tag tracks with the 3-letter form but a CLI/config
# value in the 2-letter form is the more familiar one to type.
_LANG_ALIASES: dict[str, str] = {
    "en": "eng",
    "ja": "jpn",
    "es": "spa",
    "fr": "fre",
    "de": "ger",
    "it": "ita",
    "pt": "por",
    "ru": "rus",
    "zh": "chi",
    "ko": "kor",
    "ar": "ara",
    "hi": "hin",
    "nl": "dut",
    "sv": "swe",
    "pl": "pol",
}

ALL = "all"  # sentinel meaning "no filtering, keep every track"


def normalize_lang(code: str) -> str:
    code = code.strip().lower()
    return _LANG_ALIASES.get(code, code)


def track_matches(language: str | None, target: str) -> bool:
    if not language:
        return False
    return normalize_lang(language) == normalize_lang(target)


# Coarse codec-family quality tiers for picking the single best audio track
# once language filtering has narrowed the candidates down. Lossless codecs
# unconditionally outrank lossy ones regardless of bitrate -- a 640kbps AC3
# track is never "better" than a multi-Mbps TrueHD/DTS-HD MA one -- and
# within a tier, channel count then bitrate break ties. DTS needs a profile
# check since ffprobe reports both DTS-HD MA (lossless) and plain DTS core
# (lossy) under the same codec_name "dts", distinguished only by `profile`
# containing "MA".
_CODEC_FAMILY_RANK: dict[str, int] = {
    "truehd": 100,
    "flac": 95,
    "mlp": 95,
    "pcm_s32le": 95,
    "pcm_s24le": 95,
    "pcm_s16le": 90,
    "dts": 60,  # core/lossy DTS; DTS-HD MA is special-cased below
    "eac3": 55,
    "ac3": 40,
    "aac": 30,
    "opus": 25,
    "vorbis": 20,
    "mp3": 10,
}
_DTS_MA_RANK = 95  # DTS-HD Master Audio (lossless) -- codec_name "dts", profile contains "MA"


def _codec_rank(stream: dict) -> int:
    codec = (stream.get("codec_name") or "").lower()
    profile = (stream.get("profile") or "").lower()
    if codec == "dts" and "ma" in profile:
        return _DTS_MA_RANK
    return _CODEC_FAMILY_RANK.get(codec, 0)


def _quality_key(stream: dict) -> tuple[int, int, int]:
    return (_codec_rank(stream), stream.get("channels") or 0, stream.get("bit_rate") or 0)


def pick_best_audio(streams: list[dict]) -> dict | None:
    """The single highest-quality track by codec family, then channels,
    then bitrate -- e.g. picks a TrueHD Atmos 7.1 track over a same-language
    E-AC3 "compatibility" copy of the same mix, a pattern real remuxes
    carry (see notes/track-filtering.md)."""
    if not streams:
        return None
    return max(streams, key=_quality_key)


def filter_audio(
    audio_streams: list[dict], target_lang: str, single: bool = True
) -> tuple[list[dict], bool]:
    """Returns (kept, fallback_used). `target_lang="all"` keeps every track
    completely unfiltered and unreduced -- a deliberate escape hatch, since
    "keep all tracks in all languages" and "also reduce to one" are
    contradictory asks. Otherwise: never returns an empty list when
    `audio_streams` is non-empty (falls back to every original track if
    nothing matches `target_lang`, the same "don't guess your way into no
    audio" principle as track-strip's track_policy fallback), and with
    `single=True` (the default) reduces whatever survives language
    filtering down to the single best-quality track via `pick_best_audio`."""
    if not audio_streams:
        return [], False
    if target_lang.lower() == ALL:
        return list(audio_streams), False

    matched = [s for s in audio_streams if track_matches(s.get("language"), target_lang)]
    kept, fallback = (matched, False) if matched else (list(audio_streams), True)
    if single:
        best = pick_best_audio(kept)
        kept = [best] if best is not None else kept
    return kept, fallback


def is_sdh(subtitle: dict) -> bool:
    if subtitle.get("hearing_impaired"):
        return True
    title = (subtitle.get("title") or "").lower()
    return (
        "sdh" in title
        or "hearing impaired" in title
        or "hearing-impaired" in title
        or "deaf" in title
    )


def filter_subtitles(subtitle_streams: list[dict], target_lang: str) -> list[dict]:
    """Unlike audio, an empty result is fine here -- a file with no
    subtitles at all is a completely normal, safe outcome. Among tracks
    matching `target_lang`, plain (non-SDH) ones are preferred; SDH tracks
    are only kept if that language has no plain alternative at all (same
    "don't drop a language down to nothing" principle as the audio
    fallback, just softer since going to zero subtitles is genuinely fine)."""
    if target_lang.lower() == ALL:
        return list(subtitle_streams)
    matched = [s for s in subtitle_streams if track_matches(s.get("language"), target_lang)]
    plain = [s for s in matched if not is_sdh(s)]
    return plain if plain else matched
