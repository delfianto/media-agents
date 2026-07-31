import math
import statistics

from .probe import VideoInfo


def percentile(values: list[float], percent: float) -> float:
    if not values:
        raise ValueError("cannot compute percentile of empty values")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def summarize(values: list[float]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "min": min(values),
        "p1": percentile(values, 1),
        "p5": percentile(values, 5),
        "median": statistics.median(values),
        "mean": statistics.fmean(values),
        "max": max(values),
    }


def build_result(
    reference: VideoInfo,
    distorted: VideoInfo,
    mode: str,
    model_name: str,
    clip_ranges: list[tuple[float, float]],
    vmaf_frames: list[dict[str, float]],
    ssimulacra_frames: list[dict[str, float]],
    ssimulacra_status: str,
) -> dict:
    metric_names = ("vmaf", "psnr_y", "float_ssim", "float_ms_ssim")
    summaries = {
        name: summarize([frame[name] for frame in vmaf_frames if name in frame])
        for name in metric_names
    }
    if ssimulacra_frames:
        summaries["ssimulacra2"] = summarize([frame["ssimulacra2"] for frame in ssimulacra_frames])
    worst_vmaf = sorted(vmaf_frames, key=lambda frame: frame["vmaf"])[:10]
    worst_ssimulacra = sorted(ssimulacra_frames, key=lambda frame: frame["ssimulacra2"])[:10]
    return {
        "reference": reference.to_dict(),
        "distorted": distorted.to_dict(),
        "size_reduction_percent": (1 - distorted.size / reference.size) * 100,
        "mode": mode,
        "vmaf_model": model_name,
        "hdr_native_vmaf_not_subjectively_calibrated": reference.is_hdr,
        "ssimulacra2_status": ssimulacra_status,
        "ssimulacra2_hdr_preprocessing": (
            "identical BT.709 Mobius tone map" if reference.is_hdr and ssimulacra_frames else None
        ),
        "clips": [{"start": start, "duration": duration} for start, duration in clip_ranges],
        "summary": summaries,
        "worst_vmaf_frames": worst_vmaf,
        "worst_ssimulacra2_frames": worst_ssimulacra,
        "vmaf_frames": vmaf_frames,
        "ssimulacra2_frames": ssimulacra_frames,
    }


def _timecode(seconds: float) -> str:
    whole = max(0, round(seconds))
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _metric_line(label: str, values: dict, digits: int) -> str:
    return (
        f"{label:<13} mean {values['mean']:.{digits}f}  "
        f"p5 {values['p5']:.{digits}f}  min {values['min']:.{digits}f}  "
        f"median {values['median']:.{digits}f}  n={values['count']}"
    )


def format_report(result: dict) -> str:
    reference = result["reference"]
    distorted = result["distorted"]
    summary = result["summary"]
    lines = [
        "QUALITY COMPARISON",
        f"Reference:  {reference['path']}",
        f"Distorted:  {distorted['path']}",
        (
            f"Media:      {reference['width']}x{reference['height']} "
            f"{reference['frame_rate']:.3f} fps, {reference['duration']:.2f}s"
        ),
        (
            f"Size:       {reference['size'] / 2**30:.2f} -> "
            f"{distorted['size'] / 2**30:.2f} GiB "
            f"({result['size_reduction_percent']:.1f}% smaller)"
        ),
        (
            f"Sampling:   {result['mode']}, {len(result['clips'])} clip(s), "
            f"{summary['vmaf']['count']} measured video frames"
        ),
        f"VMAF model: {result['vmaf_model']}",
        "",
        _metric_line("VMAF", summary["vmaf"], 3),
        _metric_line("PSNR-Y", summary["psnr_y"], 3),
        _metric_line("SSIM", summary["float_ssim"], 6),
        _metric_line("MS-SSIM", summary["float_ms_ssim"], 6),
    ]
    if "ssimulacra2" in summary:
        lines.append(_metric_line("SSIMULACRA2", summary["ssimulacra2"], 3))
    else:
        lines.append(f"SSIMULACRA2  unavailable ({result['ssimulacra2_status']})")
    lines.extend(["", "Worst VMAF frames:"])
    lines.extend(
        f"  {_timecode(frame['timestamp'])}  VMAF {frame['vmaf']:.3f}  "
        f"SSIM {frame.get('float_ssim', float('nan')):.6f}"
        for frame in result["worst_vmaf_frames"][:5]
    )
    if result["worst_ssimulacra2_frames"]:
        lines.append("Worst SSIMULACRA2 samples:")
        lines.extend(
            f"  {_timecode(frame['timestamp'])}  {frame['ssimulacra2']:.3f}"
            for frame in result["worst_ssimulacra2_frames"][:5]
        )
    caveats = []
    if result["hdr_native_vmaf_not_subjectively_calibrated"]:
        caveats.append(
            "HDR: VMAF was measured on aligned native PQ/HLG samples; its consumer model "
            "is not subjectively calibrated for HDR."
        )
    if result["ssimulacra2_hdr_preprocessing"]:
        caveats.append(
            "HDR: SSIMULACRA2 samples used the same deterministic BT.709 tone map on both sides."
        )
    caveats.append(
        "Film-grain synthesis can score worse despite looking natural; "
        "inspect the worst timestamps."
    )
    lines.extend(["", "Caveats:", *(f"  - {item}" for item in caveats)])
    return "\n".join(lines)
