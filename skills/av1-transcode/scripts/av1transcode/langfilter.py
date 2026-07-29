"""Plain by-language keep/drop filtering for audio/subtitle tracks.

Deliberately simple -- no anime/commentary/SDH nuance, unlike
media-library's track_policy.py, which owns the full policy engine for
whole-library track selection. This exists so a basic "just keep English"
default is available directly in av1-transcode without a separate
media-library pass first; run media-library's `apply` first instead for
anything more nuanced (dropping commentary tracks, SDH subtitles, trimming
to a single audio track, and so on).
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


def filter_audio(audio_streams: list[dict], target_lang: str) -> tuple[list[dict], bool]:
    """Returns (kept, fallback_used). Never returns an empty list when
    `audio_streams` is non-empty: if nothing matches `target_lang`, falls
    back to keeping every original track rather than producing a silent
    file -- the same "don't guess your way into no audio" principle as
    media-library's track_policy fallback."""
    if target_lang.lower() == ALL or not audio_streams:
        return list(audio_streams), False
    matched = [s for s in audio_streams if track_matches(s.get("language"), target_lang)]
    if matched:
        return matched, False
    return list(audio_streams), True


def filter_subtitles(subtitle_streams: list[dict], target_lang: str) -> list[dict]:
    """Unlike audio, an empty result is fine here -- a file with no
    subtitles at all is a completely normal, safe outcome."""
    if target_lang.lower() == ALL:
        return list(subtitle_streams)
    return [s for s in subtitle_streams if track_matches(s.get("language"), target_lang)]
