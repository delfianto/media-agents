"""Parse and resolve A/B variants and clip windows. Pure."""

from __future__ import annotations

from dataclasses import dataclass

from psammophis.compare.sampling import stratified_timestamps
from psammophis.medialib import av1_presets as presets
from psammophis.medialib.svt import SvtImplementation

_VARIANT_KEYS = frozenset({"crf", "preset", "tune", "grain", "denoise"})
_PRESET_NAMES = {item.name: item for item in presets.PRESETS.values()}


@dataclass(frozen=True)
class EncodeVariant:
    id: str
    base_name: str | None
    crf: int | None
    svt_preset: int | None
    tune: int | None
    grain: int | None
    denoise: bool | None


@dataclass(frozen=True)
class ResolvedVariant:
    id: str
    crf: int
    svt_preset: int
    tune: int
    grain: int
    denoise: bool
    label: str


@dataclass(frozen=True)
class ClipWindow:
    id: str
    start: float
    duration: float
    why: str


def parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean {raw!r}")


def parse_variant(raw: str) -> EncodeVariant:
    if "=" not in raw:
        raise ValueError(
            f"variant {raw!r} must look like id=2160p-film or id=crf=26,preset=5,grain=0"
        )
    variant_id, rest = raw.split("=", 1)
    variant_id = variant_id.strip()
    if not variant_id or "/" in variant_id or " " in variant_id:
        raise ValueError(f"invalid variant id {variant_id!r}")
    base_name: str | None = None
    crf: int | None = None
    svt_preset: int | None = None
    tune: int | None = None
    grain: int | None = None
    denoise: bool | None = None
    parts = [part.strip() for part in rest.split(",") if part.strip()]
    if not parts:
        raise ValueError(f"variant {variant_id!r} has no settings")
    for index, part in enumerate(parts):
        if "=" not in part:
            if index == 0 and part in _PRESET_NAMES:
                base_name = part
                continue
            raise ValueError(f"unknown variant token {part!r} (expected key=value)")
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key not in _VARIANT_KEYS:
            known = ", ".join(sorted(_VARIANT_KEYS))
            raise ValueError(f"unknown variant key {key!r}, expected one of {known}")
        if key == "crf":
            crf = int(value)
        elif key == "preset":
            svt_preset = int(value)
        elif key == "tune":
            tune = int(value)
        elif key == "grain":
            grain = int(value)
        else:
            denoise = parse_bool(value)
    return EncodeVariant(variant_id, base_name, crf, svt_preset, tune, grain, denoise)


def resolve_variant(
    variant: EncodeVariant,
    height: int,
    profile: str,
    implementation: SvtImplementation,
) -> ResolvedVariant:
    if variant.base_name is not None:
        base = _PRESET_NAMES[variant.base_name]
    else:
        base = presets.select_preset(height, profile, hdr=False)
    crf = variant.crf if variant.crf is not None else presets.svt_crf(base, implementation)
    if crf is None:
        raise ValueError("cannot resolve CRF: SVT implementation is unknown")
    svt_preset = variant.svt_preset if variant.svt_preset is not None else base.svt_preset
    tune = variant.tune if variant.tune is not None else base.svt_tune
    grain = variant.grain if variant.grain is not None else base.film_grain
    denoise = variant.denoise if variant.denoise is not None else base.film_grain_denoise
    if crf < 0 or svt_preset < -3 or tune < 0 or grain < 0:
        raise ValueError(f"variant {variant.id!r} has a negative encoder setting")
    label = f"crf{crf} preset{svt_preset} tune{tune} grain{grain} denoise{int(denoise)}"
    return ResolvedVariant(variant.id, crf, svt_preset, tune, grain, denoise, label)


def parse_starts(raw: str) -> list[float]:
    starts: list[float] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        start = float(token)
        if start < 0:
            raise ValueError(f"clip start {start} is negative")
        starts.append(start)
    if not starts:
        raise ValueError("no clip starts provided")
    return starts


def plan_windows(
    source_duration: float,
    *,
    starts: list[float] | None,
    clip_count: int,
    clip_duration: float,
) -> list[ClipWindow]:
    if source_duration <= 0:
        raise ValueError("source duration must be positive")
    if clip_duration <= 0:
        raise ValueError("clip duration must be positive")
    if clip_duration >= source_duration:
        return [ClipWindow("c01_full", 0.0, source_duration, "entire source (shorter than clip)")]
    if starts:
        windows: list[ClipWindow] = []
        for index, start in enumerate(starts, start=1):
            if start >= source_duration:
                raise ValueError(
                    f"clip start {start:.3f}s is past source duration {source_duration:.3f}s"
                )
            span = min(clip_duration, source_duration - start)
            clip_id = f"c{index:02d}_t{int(start):03d}"
            windows.append(ClipWindow(clip_id, start, span, f"explicit t={start:.3f}s"))
        return windows
    if clip_count <= 0:
        raise ValueError("clip count must be positive")
    centers = stratified_timestamps(source_duration, clip_count, margin=clip_duration / 2)
    windows = []
    for index, center in enumerate(centers, start=1):
        start = max(0.0, min(source_duration - clip_duration, center - clip_duration / 2))
        windows.append(
            ClipWindow(
                f"c{index:02d}_t{int(start):03d}",
                start,
                clip_duration,
                "stratified",
            )
        )
    return windows
