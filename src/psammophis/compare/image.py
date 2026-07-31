import re
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from psammophis.runtime.process import ProcessSupervisor

from .metrics import run_ssimulacra2

IMAGE_EXTENSIONS = frozenset(
    {
        ".avif",
        ".bmp",
        ".gif",
        ".heic",
        ".heif",
        ".jpeg",
        ".jpg",
        ".jxl",
        ".png",
        ".tif",
        ".tiff",
        ".webp",
    }
)
_FLOAT = re.compile(r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


@dataclass(frozen=True)
class ImageInfo:
    path: str
    size: int
    width: int
    height: int
    depth: int
    colorspace: str
    channels: str

    def to_dict(self) -> dict:
        return asdict(self)


def check_imagemagick() -> str:
    binary = shutil.which("magick")
    if binary is None:
        raise RuntimeError(
            "ImageMagick 7 (`magick`) is required for color-managed image comparison"
        )
    return binary


def probe_image(
    binary: str,
    path: Path,
    on_heartbeat: Callable[[], None] | None = None,
) -> ImageInfo:
    result = ProcessSupervisor(
        [
            binary,
            "identify",
            "-ping",
            "-format",
            "%w\t%h\t%z\t%[colorspace]\t%[channels]\n",
            str(path),
        ],
        on_heartbeat=on_heartbeat,
        timeout=120,
    ).run()
    if result.returncode != 0:
        raise RuntimeError(f"ImageMagick cannot identify {path}: {result.stderr[:500]}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError(
            f"{path} contains {len(lines)} frames; animated image comparison is not supported"
        )
    fields = lines[0].split("\t")
    if len(fields) != 5:
        raise RuntimeError(f"unexpected ImageMagick identify output for {path}: {lines[0]!r}")
    width, height, depth, colorspace, channels = fields
    return ImageInfo(
        path=str(path),
        size=path.stat().st_size,
        width=int(width),
        height=int(height),
        depth=int(depth),
        colorspace=colorspace,
        channels=channels,
    )


def normalize_image(
    binary: str,
    source: Path,
    output: Path,
    on_heartbeat: Callable[[], None] | None = None,
) -> None:
    result = ProcessSupervisor(
        [
            binary,
            str(source),
            "-auto-orient",
            "-colorspace",
            "sRGB",
            "-alpha",
            "on",
            "-depth",
            "16",
            f"PNG64:{output}",
        ],
        on_heartbeat=on_heartbeat,
        timeout=300,
    ).run()
    if result.returncode != 0:
        raise RuntimeError(f"ImageMagick normalization failed for {source}: {result.stderr[:500]}")


def _parse_metric(name: str, output: str) -> float:
    values = _FLOAT.findall(output)
    if not values:
        raise RuntimeError(f"could not parse ImageMagick {name} output: {output.strip()[:500]}")
    if name == "SSIM" and len(values) >= 2:
        return 1.0 - float(values[-1])
    if name == "RMSE" and len(values) >= 2:
        return float(values[-1])
    return float(values[0])


def compare_metric(
    binary: str,
    metric: str,
    reference: Path,
    distorted: Path,
    on_heartbeat: Callable[[], None] | None = None,
) -> float:
    result = ProcessSupervisor(
        [
            binary,
            "compare",
            "-metric",
            metric,
            str(reference),
            str(distorted),
            "null:",
        ],
        on_heartbeat=on_heartbeat,
        timeout=300,
    ).run()
    if result.returncode not in {0, 1}:
        raise RuntimeError(
            f"ImageMagick {metric} comparison failed ({result.returncode}): {result.stderr[:500]}"
        )
    return _parse_metric(metric, result.stderr or result.stdout)


def compare_images(
    reference_path: Path,
    distorted_path: Path,
    temp: Path,
    ssimulacra_binary: str | None,
    ssimulacra_status: str,
    on_heartbeat: Callable[[], None] | None = None,
) -> dict:
    magick = check_imagemagick()
    source_reference = probe_image(magick, reference_path, on_heartbeat)
    source_distorted = probe_image(magick, distorted_path, on_heartbeat)
    reference_png = temp / "reference.png"
    distorted_png = temp / "distorted.png"
    normalize_image(magick, reference_path, reference_png, on_heartbeat)
    normalize_image(magick, distorted_path, distorted_png, on_heartbeat)
    normalized_reference = probe_image(magick, reference_png, on_heartbeat)
    normalized_distorted = probe_image(magick, distorted_png, on_heartbeat)
    if (normalized_reference.width, normalized_reference.height) != (
        normalized_distorted.width,
        normalized_distorted.height,
    ):
        raise ValueError(
            "displayed dimensions differ after orientation: "
            f"reference {normalized_reference.width}x{normalized_reference.height}, "
            f"distorted {normalized_distorted.width}x{normalized_distorted.height}"
        )
    metrics = {
        "ssim": compare_metric(magick, "SSIM", reference_png, distorted_png, on_heartbeat),
        "psnr": compare_metric(magick, "PSNR", reference_png, distorted_png, on_heartbeat),
        "normalized_rmse": compare_metric(
            magick,
            "RMSE",
            reference_png,
            distorted_png,
            on_heartbeat,
        ),
    }
    if ssimulacra_binary:
        metrics["ssimulacra2"] = run_ssimulacra2(
            ssimulacra_binary,
            reference_png,
            distorted_png,
            on_heartbeat,
        )
        ssimulacra_status = "measured"
    return {
        "media_type": "image",
        "reference": source_reference.to_dict(),
        "distorted": source_distorted.to_dict(),
        "displayed_width": normalized_reference.width,
        "displayed_height": normalized_reference.height,
        "size_reduction_percent": (1 - source_distorted.size / source_reference.size) * 100,
        "normalization": (
            "EXIF orientation applied; embedded profiles converted through "
            "ImageMagick/LittleCMS to 16-bit sRGB RGBA"
        ),
        "metrics": metrics,
        "ssimulacra2_status": ssimulacra_status,
        "caveats": [
            "untagged inputs rely on each format decoder's default source color space",
            "decoded pixels are compared; metadata and application compatibility are not",
        ],
    }


def format_image_report(result: dict) -> str:
    reference = result["reference"]
    distorted = result["distorted"]
    metrics = result["metrics"]
    lines = [
        "IMAGE QUALITY COMPARISON",
        f"Reference:  {reference['path']}",
        f"Distorted:  {distorted['path']}",
        f"Display:    {result['displayed_width']}x{result['displayed_height']}",
        (
            f"Size:       {reference['size'] / 1024:.1f} -> "
            f"{distorted['size'] / 1024:.1f} KiB "
            f"({result['size_reduction_percent']:.1f}% smaller)"
        ),
        f"Working:    {result['normalization']}",
        "",
        f"SSIM          {metrics['ssim']:.8f}",
        f"PSNR          {metrics['psnr']:.4f} dB",
        f"RMSE          {metrics['normalized_rmse']:.8f} normalized",
    ]
    if "ssimulacra2" in metrics:
        lines.append(f"SSIMULACRA2  {metrics['ssimulacra2']:.4f}")
    else:
        lines.append(f"SSIMULACRA2  unavailable ({result['ssimulacra2_status']})")
    lines.extend(["", "Caveats:", *(f"  - {item}" for item in result["caveats"])])
    return "\n".join(lines)
