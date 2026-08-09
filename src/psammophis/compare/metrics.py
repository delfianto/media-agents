import json
import math
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from psammophis.runtime.process import ProcessResult, ProcessSupervisor

from .probe import VideoInfo

_NUMBER_LINE = re.compile(r"^\s*(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*$")


def find_vmaf_model(height: int) -> tuple[str, str]:
    if height >= 2160:
        candidates = (
            Path("/usr/share/model/vmaf_4k_v0.6.1.json"),
            Path("/usr/local/share/model/vmaf_4k_v0.6.1.json"),
        )
        for path in candidates:
            if path.is_file():
                return str(path), "vmaf_4k_v0.6.1"
    return "version=vmaf_v0.6.1", "vmaf_v0.6.1"


def check_libvmaf() -> None:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30
    )
    if proc.returncode != 0 or "libvmaf" not in proc.stdout:
        raise RuntimeError("FFmpeg is missing the required libvmaf filter")


def _model_option(model: str) -> str:
    if model.startswith("version="):
        return model
    return f"path={model}"


def run_vmaf_clip(
    reference: Path,
    distorted: Path,
    start: float,
    duration: float,
    model: str,
    threads: int,
    log_path: Path,
    frame_rate: float,
    width: int,
    height: int,
    on_heartbeat: Callable[[], None] | None = None,
) -> list[dict[str, float]]:
    # Matroska commonly rounds independently encoded 24000/1001 timestamps
    # to milliseconds.  PTS-STARTPTS therefore pairs the wrong frame at
    # periodic boundaries even when preflight proved both streams are CFR
    # with the same rate/count.  Rebuild both timelines from frame index so
    # frame N is always compared with frame N.
    #
    # Some anime Blu-ray encodes crop cinematic-aspect scenes down to a
    # smaller coded picture height instead of baking letterbox bars into the
    # bitstream, to save bits -- the container-level width/height (checked at
    # preflight) stays constant, but individual frames decode smaller than
    # that for the duration of such a scene. libvmaf refuses mismatched
    # per-frame dimensions outright, so pad both streams up to the clip's
    # nominal size before scoring; this is a no-op whenever a stream's frames
    # already match it.
    frame_pts = f"setpts=N/({frame_rate:.12f}*TB)"
    pad = f"pad=w={width}:h={height}:x=(ow-iw)/2:y=(oh-ih)/2:color=black"
    graph = (
        f"[0:v:0]{frame_pts},format=yuv420p10le,{pad}[ref];"
        f"[1:v:0]{frame_pts},format=yuv420p10le,{pad}[dist];"
        f"[dist][ref]libvmaf=log_fmt=json:log_path={log_path}:"
        f"model='{_model_option(model)}':"
        "feature='name=psnr|name=float_ssim|name=float_ms_ssim':"
        f"n_threads={threads}:shortest=1"
    )
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error"]
    for path in (reference, distorted):
        cmd.extend(["-ss", f"{start:.6f}", "-t", f"{duration:.6f}", "-i", str(path)])
    cmd.extend(["-filter_complex", graph, "-an", "-sn", "-f", "null", "-"])
    result = _run_vmaf_process(cmd, on_heartbeat=on_heartbeat)
    if result.returncode != 0:
        raise RuntimeError(
            f"libvmaf failed for clip at {start:.3f}s ({result.returncode}): {result.tail[-1000:]}"
        )
    data = json.loads(log_path.read_text())
    frames: list[dict[str, float]] = []
    for item in data.get("frames", []):
        metrics = item.get("metrics") or {}
        frames.append(
            {
                "timestamp": start + float(item["frameNum"]) / frame_rate,
                "frame": float(item["frameNum"]),
                **{
                    name: float(metrics[name])
                    for name in ("vmaf", "psnr_y", "float_ssim", "float_ms_ssim")
                    if name in metrics and math.isfinite(float(metrics[name]))
                },
            }
        )
    if not frames:
        raise RuntimeError(f"libvmaf returned no frames for clip at {start:.3f}s")
    return frames


def _run_vmaf_process(
    cmd: list[str],
    *,
    on_heartbeat: Callable[[], None] | None,
) -> ProcessResult:
    return ProcessSupervisor(cmd, on_heartbeat=on_heartbeat).run()


def _frame_filter(info: VideoInfo) -> str:
    # Some anime BD encodes crop cinematic-aspect scenes to a smaller coded
    # picture height instead of baking in letterbox bars (see run_vmaf_clip),
    # which otherwise makes ssimulacra2 refuse a still-frame pair pulled from
    # such a scene with "Image size mismatch". Pad up to the stream's nominal
    # size; a no-op whenever the frame already matches it.
    pad = f"pad=w={info.width}:h={info.height}:x=(ow-iw)/2:y=(oh-ih)/2:color=black"
    if info.is_hdr:
        return (
            "zscale=transfer=linear:npl=100,"
            "format=gbrpf32le,"
            "tonemap=mobius:param=0.3:desat=0,"
            "zscale=primaries=bt709:transfer=iec61966-2-1:"
            "matrix=bt709:range=full,"
            f"format=rgb24,{pad}"
        )
    return f"scale=in_range=auto:out_range=full,format=rgb24,{pad}"


def extract_frame(
    path: Path,
    timestamp: float,
    output: Path,
    info: VideoInfo,
    on_heartbeat: Callable[[], None] | None = None,
) -> None:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-ss",
        f"{timestamp:.6f}",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-vf",
        _frame_filter(info),
        "-y",
        str(output),
    ]
    result = ProcessSupervisor(cmd, on_heartbeat=on_heartbeat, timeout=300).run()
    if result.returncode != 0:
        raise RuntimeError(
            f"frame extraction failed at {timestamp:.3f}s for {path}: {result.stderr[-1000:]}"
        )


def find_ssimulacra2() -> str | None:
    return shutil.which("ssimulacra2")


def run_ssimulacra2(
    binary: str,
    reference: Path,
    distorted: Path,
    on_heartbeat: Callable[[], None] | None = None,
) -> float:
    result = ProcessSupervisor(
        [binary, str(reference), str(distorted)],
        on_heartbeat=on_heartbeat,
        timeout=300,
    ).run()
    if result.returncode != 0:
        raise RuntimeError(
            f"ssimulacra2 failed ({result.returncode}): {result.stderr.strip()[:500]}"
        )
    for line in reversed(result.stdout.splitlines()):
        match = _NUMBER_LINE.match(line)
        if match:
            return float(match.group(1))
    raise RuntimeError(f"could not parse ssimulacra2 output: {result.stdout.strip()[:500]}")
