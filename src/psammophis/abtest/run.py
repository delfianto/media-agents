"""Execute an A/B experiment: lossless refs, variant encodes, full-reference scores."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from psammophis.compare.probe import probe_video
from psammophis.compare.video import compare_video_files
from psammophis.medialib.svt import SvtImplementation
from psammophis.runtime.filesystem import atomic_write_text
from psammophis.runtime.process import ProcessSupervisor
from psammophis.runtime.signals import CancellationRequested

from .encode import encode_command, extract_command
from .report import (
    PairScore,
    VariantSummary,
    format_report,
    score_from_compare,
    scores_to_jsonable,
    summarize_variants,
)
from .spec import ClipWindow, ResolvedVariant

Heartbeat = Callable[[str], None]


def _run_ffmpeg(cmd: list[str], log_path: Path, heartbeat: Heartbeat | None, phase: str) -> None:
    result = ProcessSupervisor(
        cmd,
        log_path=log_path,
        on_heartbeat=(lambda: heartbeat(phase)) if heartbeat is not None else None,
    ).run()
    if result.cancelled:
        raise CancellationRequested(0)
    if result.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({result.returncode}): {result.tail[-800:]}")


def ref_path(output_dir: Path, window: ClipWindow) -> Path:
    return output_dir / "refs" / f"{window.id}.mkv"


def enc_path(output_dir: Path, window: ClipWindow, variant: ResolvedVariant) -> Path:
    return output_dir / "enc" / f"{window.id}__{variant.id}.mkv"


def report_path(output_dir: Path, window: ClipWindow, variant: ResolvedVariant) -> Path:
    return output_dir / "reports" / f"{window.id}__{variant.id}.json"


def write_manifest(
    output_dir: Path,
    *,
    source: Path,
    windows: list[ClipWindow],
    variants: list[ResolvedVariant],
    compare_mode: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": str(source),
        "compare_mode": compare_mode,
        "windows": [asdict(window) for window in windows],
        "variants": [asdict(variant) for variant in variants],
    }
    atomic_write_text(output_dir / "manifest.json", json.dumps(payload, indent=2) + "\n")


def format_plan(
    source: Path,
    output_dir: Path,
    windows: list[ClipWindow],
    variants: list[ResolvedVariant],
    compare_mode: str,
    implementation: SvtImplementation,
    color_transfer: str | None,
) -> str:
    lines = [
        "A/B PLAN (dry-run, nothing written)",
        f"Source:     {source}",
        f"Output:     {output_dir}",
        f"SVT:        {implementation.label}",
        f"Compare:    {compare_mode}",
        f"Windows:    {len(windows)}",
        f"Variants:   {len(variants)}",
        "",
    ]
    for window in windows:
        lines.append(
            f"  {window.id}  t={window.start:.3f}s  dur={window.duration:.3f}s  {window.why}"
        )
    lines.append("")
    for variant in variants:
        lines.append(f"  {variant.id}: {variant.label}")
        dest = enc_path(output_dir, windows[0], variant)
        cmd = encode_command(
            ref_path(output_dir, windows[0]),
            dest,
            variant,
            implementation,
            color_transfer,
        )
        lines.append("    " + " ".join(cmd))
    lines.append("")
    lines.append("Re-run with --yes to extract refs, encode, and score.")
    return "\n".join(lines)


def run_experiment(
    source: Path,
    output_dir: Path,
    windows: list[ClipWindow],
    variants: list[ResolvedVariant],
    implementation: SvtImplementation,
    color_transfer: str | None,
    *,
    compare_mode: str,
    threads: int,
    skip_ssimulacra2: bool,
    heartbeat: Heartbeat | None = None,
) -> tuple[list[PairScore], list[VariantSummary]]:
    write_manifest(
        output_dir,
        source=source,
        windows=windows,
        variants=variants,
        compare_mode=compare_mode,
    )
    logs = output_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (output_dir / "refs").mkdir(exist_ok=True)
    (output_dir / "enc").mkdir(exist_ok=True)
    (output_dir / "reports").mkdir(exist_ok=True)
    source_info = probe_video(source)
    for window in windows:
        dest = ref_path(output_dir, window)
        if dest.is_file() and dest.stat().st_size > 0:
            continue
        _run_ffmpeg(
            extract_command(source, dest, window),
            logs / f"extract-{window.id}.log",
            heartbeat,
            "extract",
        )
    for window in windows:
        reference = ref_path(output_dir, window)
        for variant in variants:
            dest = enc_path(output_dir, window, variant)
            if dest.is_file() and dest.stat().st_size > 0:
                continue
            _run_ffmpeg(
                encode_command(reference, dest, variant, implementation, color_transfer),
                logs / f"enc-{window.id}__{variant.id}.log",
                heartbeat,
                "encode",
            )
    scores: list[PairScore] = []
    for window in windows:
        reference = ref_path(output_dir, window)
        for variant in variants:
            distorted = enc_path(output_dir, window, variant)
            json_out = report_path(output_dir, window, variant)
            if json_out.is_file():
                compare_result = json.loads(json_out.read_text())
            else:
                work = output_dir / "tmp" / f"{window.id}__{variant.id}"
                compare_result = compare_video_files(
                    reference,
                    distorted,
                    work,
                    mode=compare_mode,
                    threads=threads,
                    skip_ssimulacra2=skip_ssimulacra2,
                    on_heartbeat=(lambda: heartbeat("compare")) if heartbeat else None,
                )
                atomic_write_text(json_out, json.dumps(compare_result, indent=2) + "\n")
            if not isinstance(compare_result, dict):
                raise ValueError(f"invalid compare JSON: {json_out}")
            scores.append(
                score_from_compare(
                    compare_result,
                    clip_id=window.id,
                    variant_id=variant.id,
                    why=window.why,
                    start=window.start,
                    enc_bytes=distorted.stat().st_size,
                    ref_bytes=reference.stat().st_size,
                )
            )
    labels = {variant.id: variant.label for variant in variants}
    summaries = summarize_variants(scores, labels)
    rollup = {
        "source": str(source),
        "source_duration": source_info.duration,
        "compare_mode": compare_mode,
        **scores_to_jsonable(scores, summaries),
    }
    atomic_write_text(output_dir / "rollup.json", json.dumps(rollup, indent=2) + "\n")
    return scores, summaries


def render_report(source: Path, scores: list[PairScore], summaries: list[VariantSummary]) -> str:
    return format_report(str(source), scores, summaries)
