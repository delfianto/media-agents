"""Encode presets: resolution tier x content profile -> concrete encoder
settings for both the CPU (libsvtav1) and GPU (av1_nvenc) backends.

Numbers and their sourcing are written up in full in reference/presets.md --
this module only holds the resulting table plus the pure lookup/adjustment
functions. Two axes decide a preset:

  - resolution tier: derived from the source's own pixel dimensions (an
    objective fact ffprobe reports) via resolution_tier() below.
  - profile: "film" (default) or "anime" -- NOT auto-detected. A Japanese
    audio track means a Japanese-original release, not necessarily hand-drawn
    or flat-shaded video (a live-action Japanese film would be misclassified
    by that signal), so unlike track-strip's anime-audio-policy heuristic,
    profile here is always an explicit choice by whoever invokes `run`.

HDR is a third, orthogonal condition applied on top of whichever (tier,
profile) preset was chosen -- see apply_hdr_adjustment() -- rather than a
separate set of preset entries, since it's also an objective per-file fact
(the transfer characteristic ffprobe reports) rather than a style choice.
"""

from dataclasses import dataclass, field
from itertools import pairwise

DEFAULT_PROFILE = "film"
PROFILES = ("film", "anime")

# HDR PQ/HLG content gets 2 fewer CRF/CQ points (more bits) than the SDR
# baseline for the same tier/profile -- banding in smooth gradients is far
# more visible in PQ's higher dynamic range, and this library's HDR titles
# are also almost always the highest-value archival sources (4K UHD remuxes).
HDR_QUALITY_BONUS = 2

# CRF/CQ target a *quality level*, not a *size ceiling* -- on an
# already-efficiently-encoded source (e.g. a deliberately bitrate-capped
# x264 web encode, as opposed to a wasteful Blu-ray remux master), hitting
# that quality bar can legitimately need more bits than the source itself
# used, producing an output *larger* than the file this tool exists to
# shrink. Real case that exposed this: a 3840x2160 x264 source capped at 15
# Mbps (2-pass VBR, vbv_maxrate=43.2Mbps) ballooned past its own size partway
# through the encode. Fixed with a hard ceiling tied to the source's own
# video bitrate -- SVT-AV1's "Capped CRF" (`--mbr`) and NVENC's
# `-maxrate`/`-bufsize` alongside `-cq`, both confirmed directly (see
# reference/incidents.md) to clamp output bitrate to the ceiling. This is
# purely a safety net for genuine Blu-ray remux sources (40-80+ Mbps 4K):
# CRF/CQ-driven output there normally lands far under any reasonable
# fraction of that, so the cap never binds -- it only engages exactly when
# it needs to.
MAX_BITRATE_FRACTION_OF_SOURCE = 0.85


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    svt_preset: int
    crf: int
    svt_tune: int  # 0 = VQ, 1 = PSNR, 2 = SSIM (SvtAv1EncApp --tune)
    film_grain: int  # 0 = off
    svt_extra: dict[str, str] = field(default_factory=dict)
    nvenc_preset: str = "p7"
    nvenc_tune: str = "uhq"
    nvenc_cq: float = 26.0
    nvenc_extra: dict[str, str] = field(default_factory=dict)

    def with_hdr_bonus(self) -> Preset:
        return Preset(
            name=self.name,
            description=self.description,
            svt_preset=self.svt_preset,
            crf=max(1, self.crf - HDR_QUALITY_BONUS),
            svt_tune=self.svt_tune,
            film_grain=self.film_grain,
            svt_extra=self.svt_extra,
            nvenc_preset=self.nvenc_preset,
            nvenc_tune=self.nvenc_tune,
            nvenc_cq=max(0.0, self.nvenc_cq - HDR_QUALITY_BONUS),
            nvenc_extra=self.nvenc_extra,
        )


# tune=0 (VQ) for film: the community-consensus choice for subjective quality
# on grain/texture-heavy live action (ffmpeg.party, dvaupel's and mrintrepide's
# SVT-AV1 guides all default their example commands to it). tune=1 (PSNR,
# also SvtAv1EncApp's own default) for anime instead: the JET (Jaded Encoding
# Thaumaturgy) anime-encoding guide found tune=0's psychovisual optimizer
# rings on flat-color edges and explicitly recommends staying on tune=1 for
# animation. enable-variance-boost=1 throughout -- "little to no performance
# cost when properly bitrate normalized" per the same guide, and it targets
# exactly the low-contrast-area detail loss AV1 is prone to.
_VARIANCE_BOOST = {"enable-variance-boost": "1"}
_ANIME_EXTRA = {**_VARIANCE_BOOST, "sharpness": "1"}

PRESETS: dict[tuple[str, str], Preset] = {
    ("2160p", "film"): Preset(
        name="2160p-film",
        description="4K live-action Blu-ray remux (SDR baseline; HDR bonus applies automatically)",
        svt_preset=4,
        crf=20,
        svt_tune=0,
        film_grain=10,
        svt_extra=_VARIANCE_BOOST,
        nvenc_preset="p7",
        nvenc_tune="uhq",
        nvenc_cq=22,
        nvenc_extra={"spatial-aq": "1", "temporal-aq": "1", "aq-strength": "10"},
    ),
    ("2160p", "anime"): Preset(
        name="2160p-anime",
        description="4K animation/cartoon UHD remux",
        svt_preset=4,
        crf=22,
        svt_tune=1,
        film_grain=4,
        svt_extra=_ANIME_EXTRA,
        nvenc_preset="p7",
        nvenc_tune="uhq",
        nvenc_cq=24,
        nvenc_extra={"spatial-aq": "1", "temporal-aq": "1", "aq-strength": "8"},
    ),
    ("1080p", "film"): Preset(
        name="1080p-film",
        description="1080p live-action Blu-ray remux -- the common case",
        svt_preset=4,
        crf=24,
        svt_tune=0,
        film_grain=10,
        svt_extra=_VARIANCE_BOOST,
        nvenc_preset="p7",
        nvenc_tune="uhq",
        nvenc_cq=26,
        nvenc_extra={"spatial-aq": "1", "temporal-aq": "1", "aq-strength": "10"},
    ),
    ("1080p", "anime"): Preset(
        name="1080p-anime",
        description="1080p animation/cartoon Blu-ray remux",
        svt_preset=4,
        crf=25,
        svt_tune=1,
        film_grain=4,
        svt_extra={**_ANIME_EXTRA, "tf-strength": "1"},
        nvenc_preset="p7",
        nvenc_tune="uhq",
        nvenc_cq=27,
        nvenc_extra={"spatial-aq": "1", "temporal-aq": "1", "aq-strength": "8"},
    ),
    ("720p", "film"): Preset(
        name="720p-film",
        description="720p live-action source (older/catalog titles)",
        svt_preset=5,
        crf=26,
        svt_tune=0,
        film_grain=8,
        svt_extra=_VARIANCE_BOOST,
        nvenc_preset="p7",
        nvenc_tune="hq",
        nvenc_cq=28,
        nvenc_extra={"spatial-aq": "1", "temporal-aq": "1"},
    ),
    ("720p", "anime"): Preset(
        name="720p-anime",
        description="720p animation/cartoon source",
        svt_preset=5,
        crf=27,
        svt_tune=1,
        film_grain=4,
        svt_extra=_ANIME_EXTRA,
        nvenc_preset="p7",
        nvenc_tune="hq",
        nvenc_cq=29,
        nvenc_extra={"spatial-aq": "1", "temporal-aq": "1"},
    ),
    ("sd", "film"): Preset(
        name="sd-film",
        description="Sub-720p live-action source (DVD-era catalog titles)",
        svt_preset=6,
        crf=28,
        svt_tune=0,
        film_grain=6,
        nvenc_preset="p7",
        nvenc_tune="hq",
        nvenc_cq=30,
        nvenc_extra={"spatial-aq": "1"},
    ),
    ("sd", "anime"): Preset(
        name="sd-anime",
        description="Sub-720p animation/cartoon source",
        svt_preset=6,
        crf=28,
        svt_tune=1,
        film_grain=4,
        nvenc_preset="p7",
        nvenc_tune="hq",
        nvenc_cq=31,
        nvenc_extra={"spatial-aq": "1"},
    ),
}


def resolution_tier(height: int) -> str:
    """Bucket by coded frame height. Letterboxing/pillarboxing doesn't need
    special-casing here: a 2.39:1 film in a 1920x1080 frame is still a
    genuine 1080-line frame (the bars are baked into the coded picture), and
    this library is movies/TV, never portrait video, so width is never the
    constraining dimension."""
    if height >= 1600:
        return "2160p"
    if height >= 800:
        return "1080p"
    if height >= 540:
        return "720p"
    return "sd"


def select_preset(height: int, profile: str, hdr: bool) -> Preset:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}, expected one of {PROFILES}")
    tier = resolution_tier(height)
    preset = PRESETS[(tier, profile)]
    return preset.with_hdr_bonus() if hdr else preset


def source_video_bitrate_bps(probed: dict) -> int | None:
    """Best-effort bits/sec figure for the source's own *video* stream, used
    to derive a bitrate ceiling. Prefers the video stream's own tagged
    bit_rate; falls back to the container's overall bit_rate (a slight
    overestimate, since it includes audio too -- errs toward a looser cap,
    never a tighter one), then to size/duration if neither is tagged."""
    video = probed.get("video") or {}
    if video.get("bit_rate"):
        return video["bit_rate"]
    fmt = probed.get("format") or {}
    if fmt.get("bit_rate"):
        return fmt["bit_rate"]
    duration = fmt.get("duration")
    size = fmt.get("size")
    if duration and size:
        return int(size * 8 / duration)
    return None


def max_bitrate_bps(probed: dict, fraction: float = MAX_BITRATE_FRACTION_OF_SOURCE) -> int | None:
    """None means "no cap" (unknown source bitrate, or fraction<=0 -- the
    caller's way to opt out entirely)."""
    if fraction <= 0:
        return None
    source_bps = source_video_bitrate_bps(probed)
    if source_bps is None:
        return None
    return int(source_bps * fraction)


# (channels, kbps) anchors at the layouts with an actual community-consensus
# "very high quality" figure (mono/stereo/5.1/7.1); anything in between or
# beyond is linearly interpolated/extrapolated from these rather than looked
# up in a sparse table -- a sparse table plus a separately-anchored formula
# for the gaps (tried first) produced a non-monotonic result (5 channels
# estimating *higher* than the real 6-channel/5.1 figure), caught by
# test_opus_bitrate_unlisted_channel_count_interpolates_and_is_monotonic
# before it ever ran against a real file. Generous rather than minimal
# throughout, since audio is a small fraction of total remux size next to
# 4K/1080p video.
_OPUS_BITRATE_ANCHORS_KBPS: tuple[tuple[int, int], ...] = ((1, 64), (2, 128), (6, 320), (8, 450))
_OPUS_MAX_KBPS = 510  # libopus's practical ceiling at 48kHz


def opus_bitrate_kbps(channels: int) -> int:
    """Target Opus bitrate for a source track with this many channels."""
    anchors = _OPUS_BITRATE_ANCHORS_KBPS
    if channels <= anchors[0][0]:
        return anchors[0][1]
    for (c0, b0), (c1, b1) in pairwise(anchors):
        if c0 <= channels <= c1:
            fraction = (channels - c0) / (c1 - c0)
            return round(b0 + fraction * (b1 - b0))
    (c0, b0), (c1, b1) = anchors[-2], anchors[-1]
    slope = (b1 - b0) / (c1 - c0)
    return min(_OPUS_MAX_KBPS, round(b1 + slope * (channels - c1)))
