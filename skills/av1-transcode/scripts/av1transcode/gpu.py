"""AV1 NVENC GPU capability detection.

Deliberately does not key off GPU name/architecture (e.g. "RTX 40xx and up
support AV1 encode") -- that table goes stale the moment a new generation
ships and doesn't account for professional/datacenter cards with unrelated
names, and this machine already demonstrates why: an RTX 4080 (Ada, AV1
encode-capable) and an RTX 3060 (Ampere, decode-only) both show up under
`nvidia-smi`, at indices 0 and 1 respectively. Instead, each candidate GPU is
handed one real (trivial, ~instant) av1_nvenc encode -- if ffmpeg accepts it,
that GPU can do the job, independent of what NVIDIA calls it this generation.
"""

import subprocess

_PROBE_ENCODE_CMD = (
    "ffmpeg",
    "-v",
    "error",
    "-f",
    "lavfi",
    "-i",
    "color=c=black:s=256x256:d=0.1",  # below ~192px, av1_nvenc rejects the
    # frame size itself regardless of GPU capability (verified directly:
    # 64x64 and 128x128 both fail with "Frame dimensions are less than the
    # minimum supported value" even on the Ada Lovelace GPU that otherwise
    # encodes AV1 fine) -- 256x256 stays safely clear of that floor so a
    # failure here means the GPU/driver, not the probe clip, lacks AV1 encode.
    "-frames:v",
    "1",
    "-c:v",
    "av1_nvenc",
    "-gpu",
    "{gpu}",
    "-f",
    "null",
    "-",
)


def list_gpu_indices() -> list[int]:
    proc = subprocess.run(
        ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        return []
    indices = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            indices.append(int(line))
    return indices


def gpu_supports_av1_nvenc(index: int) -> bool:
    cmd = [part.format(gpu=index) for part in _PROBE_ENCODE_CMD]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except OSError, subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0


def detect_av1_nvenc_gpu() -> int | None:
    """First GPU index (in `nvidia-smi` order) that can actually run
    av1_nvenc, or None if no GPU can -- e.g. no NVIDIA GPU at all, or every
    one present predates Ada Lovelace."""
    for index in list_gpu_indices():
        if gpu_supports_av1_nvenc(index):
            return index
    return None
