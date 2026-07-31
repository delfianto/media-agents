"""Container-agnostic policy for deciding which audio/subtitle tracks to keep.

Both the mkvmerge backend and the ffmpeg backend normalize their tool-native
track listings into the same simple shape before calling into this module, so
the keep/drop logic only has to be written -- and tested -- once.

Normalized track shape:
    {
        "index": <tool-native id: ffprobe stream index, or mkvmerge track id>,
        "codec_type": "video" | "audio" | "subtitle",
        "language": str | None,
        "forced": bool,
        "comment": bool,
        "default": bool,
        "hearing_impaired": bool,   # SDH disposition flag, when the backend exposes one
        "bytes_tag": int | None,    # container-reported track size, for the SDH size heuristic
        "title": str | None,
        "codec_name": str | None,
    }
"""

from collections import defaultdict
from dataclasses import dataclass

from . import langs

# Matches a Japanese-original release (subbed and/or dubbed anime): audio is
# Japanese alone, or Japanese + one English dub. A file with a Japanese track
# buried among a dozen+ other dub languages (e.g. a Netflix live-action show
# also dubbed into Japanese) is NOT this pattern -- see is_anime() below.
ANIME_MAX_AUDIO_TRACKS = 2

_LANG_MATCHERS = {
    "eng": langs.is_english,
    "jpn": langs.is_japanese,
}


@dataclass
class Policy:
    keep_unknown: bool = True  # keep audio/subs tagged und/unknown language
    keep_forced_subs: bool = True  # keep forced subs regardless of language
    strip_commentary: bool = False  # also strip English commentary audio tracks
    detect_anime: bool = True  # flip policy for Japanese-original releases (see is_anime)
    drop_audio_codecs: frozenset = frozenset()  # e.g. {"dts"}: drop regardless of language
    single_audio_track: bool = False  # keep only one audio track per file (see _trim_to_one_audio)
    drop_sdh_subs: bool = False  # drop SDH subtitles when a plain sibling survives (see is_sdh)

    # Default (non-anime) keep sets.
    default_audio_langs: frozenset = frozenset({"eng"})
    default_subtitle_langs: frozenset = frozenset({"eng"})
    # Anime keep sets: original Japanese audio over any dub; English AND
    # Japanese subtitles both kept (Japanese subs are rare but do occur).
    anime_audio_langs: frozenset = frozenset({"jpn"})
    anime_subtitle_langs: frozenset = frozenset({"eng", "jpn"})


def is_commentary(track):
    if track.get("comment"):
        return True
    title = (track.get("title") or "").lower()
    return "commentary" in title


# SDH ("Subtitles for the Deaf and Hard-of-hearing") tracks add speaker
# labels and bracketed sound-effect/music cues on top of the dialogue a
# plain subtitle track already has, so for the same file/language/codec they
# run measurably but moderately larger. The size heuristic below only fires
# within that narrow, bounded window (SDH_SIZE_RATIO_MIN..MAX) on exactly two
# unlabeled candidates of the *same subtitle codec* in *eng or jpn* -- each
# restriction closes a real false positive found by hand-auditing this
# library before running anything for real:
#   - same codec only: hdmv_pgs_subtitle (bitmap) tracks run 100-1000x larger
#     than subrip (text) for identical content, purely from format -- mixing
#     the two in one comparison (as an earlier version of this code did)
#     flagged plain PGS dialogue tracks as "SDH" against a much smaller SRT
#     sibling that had nothing to do with it (e.g. Alien: Covenant).
#   - eng/jpn only: subtitles with a blank/unknown language tag land in one
#     "und" bucket together regardless of what they actually are -- on this
#     library that grouped two unrelated Chinese Traditional/Simplified
#     tracks (Murderbot S01E07) and "SDH"-dropped one of them for being
#     slightly bigger than the other, which has nothing to do with SDH.
#   - title/forced exclusion: a track titled "Forced" or "Commentary" isn't
#     a plain/SDH candidate at all regardless of size (Mr. Robot has PGS
#     tracks titled exactly "Forced" with the forced *disposition* bit left
#     unset -- title text is the only signal available).
#   - exactly two candidates, bounded ratio: with 3+ unlabeled candidates
#     (Mission: Impossible - The Final Reckoning has five untitled PGS
#     English subtitle tracks) there's no reliable way to pick which one is
#     "the" plain track, so nothing is touched. The ratio is also capped
#     (not just floored) -- ratios in the hundreds-to-thousands range turned
#     up between a real dialogue track and an unrelated forced/commentary
#     track missing both its flag and a usable title (The Pacific); those
#     are a different kind of track, not an unusually verbose SDH one, and
#     asking "is it *between* 10% and 3x bigger" filters them out without
#     needing to know what they are.
# Net effect on this library: 525 SDH tracks dropped, all via explicit
# disposition flag or title -- the size heuristic didn't fire on anything at
# all once these guards were in place, meaning the ambiguous case it exists
# for doesn't currently occur here. It's kept as a real fallback for
# whatever gets added to the library next, verified not to misfire on the
# false-positive shapes actually found.
SDH_SIZE_RATIO_MIN = 1.10
SDH_SIZE_RATIO_MAX = 3.0

_SDH_ELIGIBLE_LANG_BUCKETS = {"eng", "jpn"}


def is_sdh(track):
    if track.get("hearing_impaired"):
        return True
    title = (track.get("title") or "").lower()
    return (
        "sdh" in title
        or "hearing impaired" in title
        or "hearing-impaired" in title
        or "deaf" in title
    )


def _looks_forced_or_commentary_title(track):
    title = (track.get("title") or "").lower()
    return "forced" in title or "commentary" in title


# A track can be mistagged with the wrong language code but still carry its
# real language as free text in the title -- found by hand-audit: Jack Ryan
# S01E06 has a PGS track tagged language=eng but titled "German". Grouped by
# its (wrong) language tag, it sat next to the genuine English PGS track and
# the size heuristic picked whichever of the two was smaller as "plain",
# which happened to be the German one -- backwards, and unrelated to SDH
# either way. Any track whose title names a language other than the one
# this comparison is for is excluded from SDH grouping entirely.
_OTHER_LANGUAGE_NAME_HINTS = {
    name.lower() for name in langs.NAMES.values() if name.lower() not in ("english", "japanese")
}


def _looks_mistagged_other_language(track):
    title = (track.get("title") or "").lower()
    return any(name in title for name in _OTHER_LANGUAGE_NAME_HINTS)


def _lang_bucket(lang):
    """Group tracks for SDH comparison by resolved language rather than raw
    tag, so 'en'/'eng'/'en-US' (or 'ja'/'jpn') land in the same bucket."""
    if langs.is_english(lang):
        return "eng"
    if langs.is_japanese(lang):
        return "jpn"
    if langs.is_unknown(lang):
        return "und"
    return (lang or "").lower()


def _classify_group_sdh(group):
    """Partition one same-language, same-codec group of subtitle tracks into
    (keep, sdh). Never returns an empty `keep` if `group` is non-empty --
    dropping every track for a language it actually has would be worse than
    leaving an SDH track in place (same principle as the audio zero-track/
    commentary-only guards above)."""
    if len(group) < 2:
        return list(group), []

    sdh = [t for t in group if is_sdh(t)]
    excluded = [
        t
        for t in group
        if t not in sdh
        and (_looks_forced_or_commentary_title(t) or _looks_mistagged_other_language(t))
    ]
    remaining = [t for t in group if t not in sdh and t not in excluded]

    if len(remaining) == 2 and all(t.get("bytes_tag") for t in remaining):
        small, big = sorted(remaining, key=lambda t: t["bytes_tag"])
        ratio = big["bytes_tag"] / small["bytes_tag"]
        if SDH_SIZE_RATIO_MIN <= ratio <= SDH_SIZE_RATIO_MAX:
            sdh.append(big)
            remaining = [small]

    keep = remaining + excluded
    if not keep:
        return list(group), []  # everything looked like SDH -- keep it all rather than go silent
    return keep, sdh


def resolve_language(lang, keep_set):
    """Canonicalize a track's language for muxing: known English/Japanese
    tags map to their ISO 639-2 code regardless of source form ('en'/'eng',
    'ja'/'jpn'); anything blank or otherwise unresolved falls back to the
    policy's primary language for that track type (English, unless the only
    option is Japanese -- i.e. anime audio)."""
    if langs.is_english(lang):
        return "eng"
    if langs.is_japanese(lang):
        return "jpn"
    if "eng" in keep_set:
        return "eng"
    return sorted(keep_set)[0] if keep_set else "eng"


def _annotate_resolved_language(tracks, keep_set):
    for t in tracks:
        t["resolved_lang"] = resolve_language(t.get("language"), keep_set)


ENGLISH_ONLY = frozenset({"eng"})


def is_anime(streams, policy: Policy):
    """A file counts as a Japanese-original release if it has a Japanese
    audio track and very few audio tracks overall (<=2: JP alone, or JP+EN
    dub). Shows with a Japanese dub buried among many other languages don't
    match this shape and are left on the default English-first policy."""
    if not policy.detect_anime:
        return False
    audio = [t for t in streams if t["codec_type"] == "audio"]
    if not audio or len(audio) > ANIME_MAX_AUDIO_TRACKS:
        return False
    return any(langs.is_japanese(t.get("language")) for t in audio)


def _lang_matches(lang, keep_set):
    return any(_LANG_MATCHERS[key](lang) for key in keep_set)


def decide_keep(track, policy: Policy, audio_langs, subtitle_langs):
    """Return (keep: bool, reason: str) for a single audio/subtitle track."""
    codec_type = track["codec_type"]
    lang = track.get("language")

    if codec_type == "subtitle" and policy.keep_forced_subs and track.get("forced"):
        return True, "forced"

    keep_set = audio_langs if codec_type == "audio" else subtitle_langs
    if _lang_matches(lang, keep_set):
        if codec_type == "audio" and policy.strip_commentary and is_commentary(track):
            return False, "commentary-stripped"
        if codec_type == "audio" and track.get("codec_name") in policy.drop_audio_codecs:
            return False, f"codec-dropped:{track.get('codec_name')}"
        return True, f"kept:{'+'.join(sorted(keep_set))}"

    if langs.is_unknown(lang):
        return (True, "unknown-kept") if policy.keep_unknown else (False, "unknown-stripped")

    return False, f"lang={lang or 'none'} not in keep-set:{'+'.join(sorted(keep_set))}"


def from_ffprobe_stream(s):
    """Normalize a scan.py stream dict (or a raw ffprobe stream with the same
    shape) into the canonical track dict used by plan_streams()."""
    disp = s.get("disposition", {}) or {}
    return {
        "index": s["index"],
        "codec_type": s["codec_type"],
        "language": s.get("language"),
        "forced": bool(disp.get("forced")),
        "comment": bool(disp.get("comment")),
        "default": bool(disp.get("default")),
        "hearing_impaired": bool(disp.get("hearing_impaired")),
        "bytes_tag": s.get("bytes_tag"),
        "title": s.get("title"),
        "codec_name": s.get("codec_name"),
    }


_MKVMERGE_TYPE_MAP = {"video": "video", "audio": "audio", "subtitles": "subtitle"}

# mkvmerge's `codec` field is a free-text description ("DTS-HD Master Audio",
# "AC-3 Dolby Surround EX", "TrueHD Atmos") that varies with profile/flags, so
# it can never be compared reliably against ffprobe's short codec_name (dts,
# ac3, truehd). Its `codec_id` (the Matroska CodecID) is a stable identifier,
# so audio tracks get normalized through this table instead -- this is the
# only thing that makes --drop-audio-codec behave the same on .mkv files
# (mkvmerge backend) as it does everywhere else (ffprobe backend).
_MKVMERGE_AUDIO_CODEC_ID_MAP = {
    "A_AC3": "ac3",
    "A_EAC3": "eac3",
    "A_DTS": "dts",
    "A_TRUEHD": "truehd",
    "A_AAC": "aac",
    "A_OPUS": "opus",
    "A_FLAC": "flac",
    "A_VORBIS": "vorbis",
    "A_MPEG/L3": "mp3",
    "A_MPEG/L2": "mp2",
    "A_PCM/INT/LIT": "pcm_s16le",
    "A_PCM/INT/BIG": "pcm_s16be",
}


def _to_int(v):
    try:
        return int(v) if v is not None else None
    except TypeError, ValueError:
        return None


def from_mkvmerge_track(t):
    """Normalize one track object from `mkvmerge -J` output."""
    codec_type = _MKVMERGE_TYPE_MAP.get(t.get("type"))
    if codec_type is None:
        return None
    props = t.get("properties", {}) or {}
    codec_name = t.get("codec")
    if codec_type == "audio":
        codec_name = _MKVMERGE_AUDIO_CODEC_ID_MAP.get(props.get("codec_id") or "", codec_name)
    return {
        "index": t["id"],
        "codec_type": codec_type,
        "language": props.get("language_ietf") or props.get("language"),
        "forced": bool(props.get("forced_track")),
        "comment": bool(props.get("comment_track")),
        "default": bool(props.get("default_track")),
        # mkvmerge -J doesn't expose a hearing-impaired flag distinct from
        # comment_track/forced_track, so SDH detection on .mkv files relies
        # on title text and the tag_number_of_bytes size heuristic instead.
        "hearing_impaired": False,
        "bytes_tag": _to_int(props.get("tag_number_of_bytes")),
        "title": props.get("track_name"),
        "codec_name": codec_name,
    }


def plan_streams(streams, policy: Policy):
    """Decide keep/drop for every audio and subtitle track in `streams`.

    Guarantees at least one *non-commentary* audio track survives whenever
    one exists on the file, even if none pass the language/codec policy
    (falls back to the original default/first primary track). Commentary
    tracks don't count toward this guarantee -- a file can't be considered
    "safe" just because commentary-only audio survived (see the fallback
    block below for the safety invariant this enforces).
    """
    keep_video = [t for t in streams if t["codec_type"] == "video"]
    audio = [t for t in streams if t["codec_type"] == "audio"]
    subs = [t for t in streams if t["codec_type"] == "subtitle"]

    anime = is_anime(streams, policy)
    audio_langs = policy.anime_audio_langs if anime else policy.default_audio_langs
    subtitle_langs = policy.anime_subtitle_langs if anime else policy.default_subtitle_langs

    audio_decisions = [(t, decide_keep(t, policy, audio_langs, subtitle_langs)) for t in audio]
    sub_decisions = [(t, decide_keep(t, policy, audio_langs, subtitle_langs)) for t in subs]

    keep_audio = [t for t, (keep, _) in audio_decisions if keep]
    drop_audio = [(t, reason) for t, (keep, reason) in audio_decisions if not keep]

    fallback_used = False
    if audio and not any(not is_commentary(t) for t in keep_audio):
        # Either zero audio survives, or every surviving track is commentary
        # -- neither is acceptable, and the two look the same to a naive
        # "is keep_audio empty?" check. Prefer restoring a non-commentary
        # track (the original default, or the first one) over whatever is
        # already kept; only fall back to commentary if nothing else exists.
        primary_candidates = [t for t in audio if not is_commentary(t)]
        pool = primary_candidates or audio
        fallback = next((t for t in pool if t.get("default")), pool[0])
        if fallback not in keep_audio:
            keep_audio.append(fallback)
            drop_audio = [(t, reason) for t, reason in drop_audio if t is not fallback]
        fallback_used = True

    if policy.single_audio_track and len(keep_audio) > 1:
        # Same non-commentary-first preference as the fallback above, so a
        # commentary track is only picked if nothing else survived policy.
        non_commentary = [t for t in keep_audio if not is_commentary(t)]
        pool = non_commentary or keep_audio
        primary = next((t for t in pool if t.get("default")), pool[0])
        drop_audio = drop_audio + [(t, "single-track-trim") for t in keep_audio if t is not primary]
        keep_audio = [primary]

    keep_subtitle = [t for t, (keep, _) in sub_decisions if keep]
    drop_subtitle = [(t, reason) for t, (keep, reason) in sub_decisions if not keep]

    if policy.drop_sdh_subs:
        # Only forced-flagged tracks and eng/jpn subtitles of the same
        # codec ever get compared against each other -- see the long
        # comment above _classify_group_sdh for exactly which real false
        # positives each restriction here was closing.
        groups = defaultdict(list)
        passthrough = []
        for t in keep_subtitle:
            bucket = _lang_bucket(t.get("language"))
            if t.get("forced") or bucket not in _SDH_ELIGIBLE_LANG_BUCKETS:
                passthrough.append(t)
            else:
                groups[(bucket, t.get("codec_name"))].append(t)
        new_keep_subtitle = passthrough
        for group in groups.values():
            kept_group, sdh_group = _classify_group_sdh(group)
            new_keep_subtitle.extend(kept_group)
            drop_subtitle = drop_subtitle + [(t, "sdh-dropped") for t in sdh_group]
        keep_subtitle = new_keep_subtitle

    _annotate_resolved_language(keep_audio, audio_langs)
    _annotate_resolved_language(keep_subtitle, subtitle_langs)

    return {
        "keep_video": keep_video,
        "keep_audio": keep_audio,
        "drop_audio": drop_audio,
        "keep_subtitle": keep_subtitle,
        "drop_subtitle": drop_subtitle,
        "fallback_audio_used": fallback_used,
        "is_anime": anime,
        "changed": bool(drop_audio or drop_subtitle),
    }


def ffmpeg_language_metadata_args(kept_by_type):
    """Build -metadata:s:<type>:<N> language=<code> args for ffmpeg, so
    remuxed/transcoded output carries a clean language tag on every kept
    track instead of whatever blank/inconsistent tag the source had.
    `kept_by_type` maps ffmpeg stream-specifier letters to the kept track
    lists in the same order they were passed to -map (e.g. {"a": keep_audio,
    "s": keep_subtitle}) -- shared by remux_ffmpeg.py and transcode.py."""
    args = []
    for type_code, tracks in kept_by_type.items():
        for i, t in enumerate(tracks):
            lang = t.get("resolved_lang")
            if lang:
                args += [f"-metadata:s:{type_code}:{i}", f"language={lang}"]
    return args
