"""Roll up per-clip compare results into a variant table. Pure."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PairScore:
    clip_id: str
    variant_id: str
    why: str
    start: float
    enc_bytes: int
    ref_bytes: int
    vmaf_mean: float
    vmaf_median: float
    vmaf_p5: float
    vmaf_min: float
    ssim_mean: float | None
    ssimulacra2_mean: float | None
    ssimulacra2_min: float | None
    frames: int


@dataclass(frozen=True)
class VariantSummary:
    variant_id: str
    label: str
    vmaf_mean: float
    vmaf_p5: float
    vmaf_min: float
    ssimulacra2_mean: float | None
    ssimulacra2_min: float | None
    enc_bytes: int
    size_vs_first: float


def _metric(summary: dict[str, Any], name: str, field: str) -> float | None:
    block = summary.get(name)
    if not isinstance(block, dict) or field not in block:
        return None
    value = block[field]
    return float(value) if value is not None else None


def score_from_compare(
    compare_result: dict[str, Any],
    *,
    clip_id: str,
    variant_id: str,
    why: str,
    start: float,
    enc_bytes: int,
    ref_bytes: int,
) -> PairScore:
    summary = compare_result["summary"]
    if not isinstance(summary, dict):
        raise ValueError("compare result is missing summary")
    vmaf_mean = _metric(summary, "vmaf", "mean")
    vmaf_median = _metric(summary, "vmaf", "median")
    vmaf_p5 = _metric(summary, "vmaf", "p5")
    vmaf_min = _metric(summary, "vmaf", "min")
    frames = _metric(summary, "vmaf", "count")
    if (
        vmaf_mean is None
        or vmaf_median is None
        or vmaf_p5 is None
        or vmaf_min is None
        or frames is None
    ):
        raise ValueError("compare result is missing VMAF summary fields")
    return PairScore(
        clip_id=clip_id,
        variant_id=variant_id,
        why=why,
        start=start,
        enc_bytes=enc_bytes,
        ref_bytes=ref_bytes,
        vmaf_mean=vmaf_mean,
        vmaf_median=vmaf_median,
        vmaf_p5=vmaf_p5,
        vmaf_min=vmaf_min,
        ssim_mean=_metric(summary, "float_ssim", "mean"),
        ssimulacra2_mean=_metric(summary, "ssimulacra2", "mean"),
        ssimulacra2_min=_metric(summary, "ssimulacra2", "min"),
        frames=int(frames),
    )


def summarize_variants(scores: list[PairScore], labels: dict[str, str]) -> list[VariantSummary]:
    if not scores:
        return []
    order: list[str] = []
    grouped: dict[str, list[PairScore]] = {}
    for score in scores:
        if score.variant_id not in grouped:
            order.append(score.variant_id)
            grouped[score.variant_id] = []
        grouped[score.variant_id].append(score)
    first_bytes = sum(item.enc_bytes for item in grouped[order[0]])
    summaries: list[VariantSummary] = []
    for variant_id in order:
        items = grouped[variant_id]
        enc_bytes = sum(item.enc_bytes for item in items)
        s2_means = [item.ssimulacra2_mean for item in items if item.ssimulacra2_mean is not None]
        s2_mins = [item.ssimulacra2_min for item in items if item.ssimulacra2_min is not None]
        summaries.append(
            VariantSummary(
                variant_id=variant_id,
                label=labels.get(variant_id, variant_id),
                vmaf_mean=sum(item.vmaf_mean for item in items) / len(items),
                vmaf_p5=sum(item.vmaf_p5 for item in items) / len(items),
                vmaf_min=min(item.vmaf_min for item in items),
                ssimulacra2_mean=(sum(s2_means) / len(s2_means)) if s2_means else None,
                ssimulacra2_min=min(s2_mins) if s2_mins else None,
                enc_bytes=enc_bytes,
                size_vs_first=enc_bytes / first_bytes if first_bytes else 0.0,
            )
        )
    return summaries


def format_report(
    source: str,
    scores: list[PairScore],
    summaries: list[VariantSummary],
) -> str:
    lines = [
        "ENCODER A/B",
        f"Source:     {source}",
        f"Pairs:      {len(scores)}",
        "",
        f"{'variant':<18} {'vmaf':>7} {'p5':>7} {'min':>7} {'s2':>7} {'s2min':>7} {'rel':>6}",
    ]
    for item in summaries:
        s2 = f"{item.ssimulacra2_mean:.2f}" if item.ssimulacra2_mean is not None else "n/a"
        s2min = f"{item.ssimulacra2_min:.2f}" if item.ssimulacra2_min is not None else "n/a"
        lines.append(
            f"{item.variant_id:<18} {item.vmaf_mean:7.2f} {item.vmaf_p5:7.2f} "
            f"{item.vmaf_min:7.2f} {s2:>7} {s2min:>7} {item.size_vs_first:5.2f}x"
        )
    lines.extend(["", "Per clip (lowest VMAF first):"])
    for score in sorted(scores, key=lambda item: item.vmaf_mean):
        s2 = f"{score.ssimulacra2_mean:.2f}" if score.ssimulacra2_mean is not None else "n/a"
        lines.append(
            f"  {score.clip_id} {score.variant_id}: VMAF {score.vmaf_mean:.2f} "
            f"p5 {score.vmaf_p5:.2f} min {score.vmaf_min:.2f} S2 {s2} "
            f"({score.enc_bytes / 1e6:.1f} MB) {score.why}"
        )
    lines.extend(
        [
            "",
            "Caveats:",
            "  - Prefer p5/min and SSIMULACRA2 over VMAF mean alone.",
            "  - Film-grain synthesis can drop full-reference scores while looking fine.",
            "  - Inspect the worst timestamps on the intended display before changing presets.",
        ]
    )
    return "\n".join(lines)


def scores_to_jsonable(
    scores: list[PairScore], summaries: list[VariantSummary]
) -> dict[str, object]:
    return {
        "summaries": [asdict(item) for item in summaries],
        "pairs": [asdict(item) for item in scores],
    }
