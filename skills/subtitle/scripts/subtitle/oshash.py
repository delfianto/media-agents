"""OpenSubtitles' file-hash algorithm ("oshash"): file size plus a 64-bit
wraparound checksum of the first and last 64KB, read as little-endian
uint64 values. Originally from Media Player Classic, adopted by
OpenSubtitles as the primary way to look up subtitles for an exact file
without relying on its (often wrong/missing) filename -- this is the
`moviehash` parameter opensubtitles.py's search() passes through.

Spec and test vectors: https://opensubtitles.github.io/oshash/
"""

import struct
from pathlib import Path

CHUNK_SIZE = 65536  # 64 KiB, read from both the start and end of the file
MIN_FILE_SIZE = 131072  # 128 KiB -- files smaller than this can't be hashed
_MASK64 = 0xFFFFFFFFFFFFFFFF


def _checksum_chunk(data: bytes) -> int:
    total = 0
    values = struct.unpack(f"<{len(data) // 8}Q", data[: len(data) // 8 * 8])
    for value in values:
        total = (total + value) & _MASK64
    return total


def compute(path: str | Path) -> str | None:
    """16-digit lowercase hex oshash, or None if `path` is smaller than
    MIN_FILE_SIZE (oshash is undefined below that, same as every other
    implementation of this algorithm)."""
    path = Path(path)
    size = path.stat().st_size
    if size < MIN_FILE_SIZE:
        return None
    with open(path, "rb") as f:
        head = f.read(CHUNK_SIZE)
        f.seek(size - CHUNK_SIZE)
        tail = f.read(CHUNK_SIZE)
    total = (size + _checksum_chunk(head) + _checksum_chunk(tail)) & _MASK64
    return f"{total:016x}"
