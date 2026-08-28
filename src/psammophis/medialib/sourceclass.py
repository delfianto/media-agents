"""Classify a source by *delivery quality*, not resolution or film/anime.

The master-calibrated CRF/CQ table assumes a high-bitrate Blu-ray remux or
raw capture. Applying that table to a 21 Mb/s 4K AVC webrip, a streaming
encode, or a camcorder recording makes the encoder try to reconstruct a
quality the source never had (and often spend *more* bits than the source).
Four classes:

- ``master`` -- Blu-ray remux or raw capture (high bitrate, usually 10-bit
  HEVC/AV1). Uses the calibrated table as-is.
- ``webrip`` -- decent commercial encode that is already more compressed
  than a remux (typical 8-bit High-profile AVC). Quality ceiling is the
  source; CRF/CQ is raised so we stay transparent-to-source without
  master-sized output.
- ``stream`` -- heavily streaming-optimized compression (Baseline profile,
  or bitrate at the bottom of the tier). Do not spend master bits on it.
- ``lowest`` -- recorded sources, vintage films with dubious compression,
  legacy codecs (MPEG-2, DivX/Xvid, WMV), and similar. Spend even less.

Detection is objective (codec, profile, bitrate vs resolution, plus
portrait geometry for phone/webcam HLS captures). Override with
``--source-class`` when the heuristic is wrong. Content profile
(film/anime) stays an explicit choice; this axis is independent.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from . import av1_presets as presets

SourceClassName = Literal["master", "webrip", "stream", "lowest"]
SOURCE_CLASSES: tuple[SourceClassName, ...] = ("master", "webrip", "stream", "lowest")

# Video-bps floors/ceilings by resolution_tier. Classifiers, not quality
# targets -- tune with real files (tests/medialib/test_sourceclass.py).
MASTER_FLOOR_BPS: dict[str, int] = {
    "2160p": 35_000_000,
    "1080p": 12_000_000,
    "720p": 8_000_000,
    "sd": 4_000_000,
}
STREAM_CEILING_BPS: dict[str, int] = {
    "2160p": 8_000_000,
    "1080p": 3_500_000,
    "720p": 1_800_000,
    "sd": 1_200_000,
}
# Below this, even "stream" is generous -- phone recordings, cam rips, etc.
LOWEST_CEILING_BPS: dict[str, int] = {
    "2160p": 3_000_000,
    "1080p": 1_500_000,
    "720p": 800_000,
    "sd": 600_000,
}

# Added to the master-calibrated CRF/CQ. Higher = more compressed.
# webrip +4 CQ: 2160p-film 22→26. Uncapped CQ 22 on WowGirls 4K wanted
# 40-60 Mb/s against a 21 Mb/s source; +4 aims under the source while
# remaining transparent-to-that-AVC. stream +8 / lowest +12: VDH webcam
# ladder landed at 0.95x / 0.65x source; do not spend master bits
# reconstructing mush. See notes/source-class-audit.md.
WEBRIP_NVENC_CQ_DELTA = 4
WEBRIP_SVT_CRF_DELTA = 3
STREAM_NVENC_CQ_DELTA = 8
STREAM_SVT_CRF_DELTA = 6
LOWEST_NVENC_CQ_DELTA = 12
LOWEST_SVT_CRF_DELTA = 10

_DELTAS: dict[SourceClassName, tuple[int, int]] = {
    "master": (0, 0),
    "webrip": (WEBRIP_SVT_CRF_DELTA, WEBRIP_NVENC_CQ_DELTA),
    "stream": (STREAM_SVT_CRF_DELTA, STREAM_NVENC_CQ_DELTA),
    "lowest": (LOWEST_SVT_CRF_DELTA, LOWEST_NVENC_CQ_DELTA),
}

RECOMMENDED_CAP_FRACTION: dict[SourceClassName, float] = {
    "master": presets.MAX_BITRATE_FRACTION_OF_SOURCE,
    "webrip": presets.MAX_BITRATE_FRACTION_OF_SOURCE,
    "stream": 0.70,
    "lowest": 0.60,
}

_TEN_BIT_MARKERS = ("10", "12", "p010", "p012", "p016")
_MASTER_CODECS = frozenset({"hevc", "h265", "av1", "vc2", "ffv1", "prores", "dnxhd", "cfhd"})
# Pre-AVC consumer and disc-legacy families. A high-bitrate MPEG-2 DVD is
# still "lowest" -- the codec, not the bitrate, is the quality ceiling.
_LOWEST_CODECS = frozenset(
    {
        "mpeg1video",
        "mpeg2video",
        "mpeg4",
        "msmpeg4v1",
        "msmpeg4v2",
        "msmpeg4v3",
        "wmv1",
        "wmv2",
        "wmv3",
        "vc1",
        "flv1",
        "vp6f",
        "vp6",
        "h263",
        "h263p",
        "svq1",
        "svq3",
        "rv10",
        "rv20",
        "rv30",
        "rv40",
        "theora",
        "indeo3",
        "indeo4",
        "indeo5",
        "cinepak",
        "msvideo1",
        "dvvideo",
        "mjpeg",
    }
)


@dataclass(frozen=True)
class SourceClassification:
    name: SourceClassName
    reason: str
    video_bps: int | None
    master_floor_bps: int
    stream_ceiling_bps: int
    lowest_ceiling_bps: int
    override: bool = False
    portrait: bool = False

    @property
    def recommended_cap_fraction(self) -> float:
        return RECOMMENDED_CAP_FRACTION[self.name]


def _is_10bit(pix_fmt: str | None) -> bool:
    text = (pix_fmt or "").lower()
    return any(marker in text for marker in _TEN_BIT_MARKERS)


def _is_baseline_profile(profile: str | None) -> bool:
    text = (profile or "").lower()
    return "baseline" in text


def is_portrait_capture(width: int, height: int, codec: str, pix_fmt: str | None) -> bool:
    """Phone / webcam / VDH-style HLS: portrait 8-bit consumer encode.

    10-bit or mezzanine codecs are left on the normal bitrate path even
    when the frame is tall -- a portrait ProRes/HEVC master is rare but
    should not be forced down to stream.
    """
    if not presets.is_portrait(width, height):
        return False
    return codec not in _MASTER_CODECS and not _is_10bit(pix_fmt)


def classify_source(probed: dict, *, override: str = "auto") -> SourceClassification:
    """Return master/webrip/stream/lowest for a ``videoprobe.probe_file`` dict.

    ``override`` is ``auto`` or an explicit class name. Explicit always
    wins and is recorded on the result.
    """
    video = probed.get("video") or {}
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    tier = presets.resolution_tier_for_frame(width, height)
    master_floor = MASTER_FLOOR_BPS[tier]
    stream_ceiling = STREAM_CEILING_BPS[tier]
    lowest_ceiling = LOWEST_CEILING_BPS[tier]
    video_bps = presets.source_video_bitrate_bps(probed)
    portrait = presets.is_portrait(width, height)

    if override != "auto":
        if override not in SOURCE_CLASSES:
            raise ValueError(f"unknown source class {override!r}, expected one of {SOURCE_CLASSES}")
        return SourceClassification(
            name=override,
            reason=f"explicit --source-class {override}",
            video_bps=video_bps,
            master_floor_bps=master_floor,
            stream_ceiling_bps=stream_ceiling,
            lowest_ceiling_bps=lowest_ceiling,
            override=True,
            portrait=portrait,
        )

    codec = (video.get("codec_name") or "").lower()
    profile = video.get("profile")
    pix_fmt = video.get("pix_fmt")
    bps_label = f"{video_bps / 1e6:.1f} Mb/s" if video_bps else "unknown bitrate"

    def _result(
        name: SourceClassName, reason: str, *, override: bool = False
    ) -> SourceClassification:
        return SourceClassification(
            name=name,
            reason=reason,
            video_bps=video_bps,
            master_floor_bps=master_floor,
            stream_ceiling_bps=stream_ceiling,
            lowest_ceiling_bps=lowest_ceiling,
            override=override,
            portrait=portrait,
        )

    if codec in _LOWEST_CODECS:
        return _result(
            "lowest",
            f"{codec} is a legacy/recording codec, not a modern delivery encode",
        )
    # Portrait 8-bit phone/webcam/HLS (VDH). Height-based tiers called
    # 480x854 "1080p" and 1080x1920 "2160p"; min-side SD master floor
    # then promoted 5.6 Mb/s 396-wide captures to master. Cap at stream
    # unless the min-side bitrate is truly at the lowest ceiling.
    # See notes/source-class-audit.md.
    if is_portrait_capture(width, height, codec, pix_fmt):
        shape = f"{width}x{height} portrait {tier}"
        if video_bps is not None and video_bps <= lowest_ceiling:
            return _result(
                "lowest",
                f"{shape} capture at {bps_label} is at or below the lowest "
                f"ceiling ({lowest_ceiling / 1e6:.1f} Mb/s)",
            )
        return _result(
            "stream",
            f"{shape} 8-bit capture is webcam/HLS-style, not a remux "
            f"(VDH ladder: stream +8 CQ stayed near source size)",
        )
    if video_bps is not None and video_bps <= lowest_ceiling:
        return _result(
            "lowest",
            f"{bps_label} {tier} is at or below the lowest ceiling "
            f"({lowest_ceiling / 1e6:.1f} Mb/s)",
        )
    if _is_baseline_profile(profile):
        return _result(
            "stream",
            f"{codec or 'video'} profile {profile!r} is a streaming/compatibility encode",
        )
    if video_bps is not None and video_bps <= stream_ceiling:
        return _result(
            "stream",
            f"{bps_label} {tier} is at or below the stream ceiling "
            f"({stream_ceiling / 1e6:.1f} Mb/s)",
        )
    if video_bps is not None and video_bps >= master_floor:
        masterish = codec in _MASTER_CODECS or _is_10bit(pix_fmt)
        high_avc = video_bps >= int(master_floor * 1.5)
        if masterish or high_avc:
            why = (
                f"{codec or 'video'} {bps_label} {tier} meets the master floor "
                f"({master_floor / 1e6:.0f} Mb/s)"
            )
            if _is_10bit(pix_fmt):
                why += ", 10-bit"
            return _result("master", why)
    if video_bps is None:
        reason = "bitrate unknown; not assuming master"
    else:
        reason = (
            f"{bps_label} {tier} sits between stream "
            f"({stream_ceiling / 1e6:.1f} Mb/s) and master "
            f"({master_floor / 1e6:.0f} Mb/s)"
        )
    return _result("webrip", reason)


def apply_source_class(preset: presets.Preset, source_class: SourceClassName) -> presets.Preset:
    """Return a copy of ``preset`` with CRF/CQ raised for non-master sources.

    Master is a no-op. Deltas are starting points: webrip from WowGirls /
    MetArtX, stream/lowest bounded against VDH webcam captures -- not a
    fourth full preset table. See notes/source-class-audit.md.
    """
    if source_class not in _DELTAS:
        raise ValueError(f"unknown source class {source_class!r}")
    crf_delta, cq_delta = _DELTAS[source_class]
    if crf_delta == 0 and cq_delta == 0:
        return preset
    note = (
        f" [{source_class} source: CRF/CQ raised "
        f"(+{crf_delta} CRF / +{cq_delta} CQ) because the source is already "
        f"the quality ceiling -- master targets would overspend]"
    )
    return replace(
        preset,
        name=f"{preset.name}+{source_class}",
        description=preset.description + note,
        crf=min(63, preset.crf + crf_delta),
        svt_hdr_crf=min(63, preset.svt_hdr_crf + crf_delta),
        nvenc_cq=min(63.0, float(preset.nvenc_cq) + cq_delta),
    )
