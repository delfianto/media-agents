"""Walk a media library and cache ffprobe stream/format metadata as JSON.

Read-only: this module never touches media files, only inspects them.
"""

import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from psammophis.medialib.walk import walk_media_files

DEFAULT_EXTENSIONS = frozenset({".mkv", ".mp4", ".m4v", ".avi", ".ts", ".mov", ".wmv"})
CACHE_VERSION = 1


def probe_file(path):
    """Run ffprobe on a single file and return the trimmed JSON dict."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed ({proc.returncode}): {proc.stderr.strip()[:500]}")
    data = json.loads(proc.stdout)

    fmt = data.get("format", {})
    trimmed_format = {
        "format_name": fmt.get("format_name"),
        "duration": _to_float(fmt.get("duration")),
        "size": _to_int(fmt.get("size")),
        "bit_rate": _to_int(fmt.get("bit_rate")),
    }

    streams = []
    for s in data.get("streams", []):
        tags = s.get("tags", {}) or {}
        streams.append(
            {
                "index": s.get("index"),
                "codec_type": s.get("codec_type"),
                "codec_name": s.get("codec_name"),
                "profile": s.get("profile"),
                "width": s.get("width"),
                "height": s.get("height"),
                "channels": s.get("channels"),
                "bit_rate": _to_int(s.get("bit_rate")),
                "duration": _to_float(s.get("duration")),
                "language": tags.get("language"),
                "title": tags.get("title"),
                "bps_tag": _to_int(tags.get("BPS")),
                "bytes_tag": _to_int(tags.get("NUMBER_OF_BYTES")),
                "disposition": s.get("disposition", {}) or {},
            }
        )

    return {"format": trimmed_format, "streams": streams}


def _to_float(v):
    try:
        return float(v) if v is not None else None
    except TypeError, ValueError:
        return None


def _to_int(v):
    try:
        return int(float(v)) if v is not None else None
    except TypeError, ValueError:
        return None


def load_cache(cache_path):
    cache_path = Path(cache_path)
    if not cache_path.exists():
        return {"version": CACHE_VERSION, "generated_at": None, "files": {}}
    with open(cache_path) as f:
        data = json.load(f)
    if data.get("version") != CACHE_VERSION:
        return {"version": CACHE_VERSION, "generated_at": None, "files": {}}
    return data


def save_cache(cache_path, data):
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    tmp.replace(cache_path)


def scan(root, cache_path, extensions=None, jobs=8, force=False, on_progress=None):
    """Scan root for media files, probing new/changed files and reusing the
    cache for unchanged ones (matched by size + mtime). Returns the updated
    cache dict (also written to cache_path).
    """
    root = Path(root)
    cache = load_cache(cache_path)
    files_cache = cache["files"]

    all_files = list(walk_media_files(root, extensions or DEFAULT_EXTENSIONS))
    seen_rel_paths = set()
    to_probe = []

    for path in all_files:
        rel = str(path.relative_to(root))
        seen_rel_paths.add(rel)
        st = path.stat()
        cached = files_cache.get(rel)
        if (
            not force
            and cached
            and cached.get("size") == st.st_size
            and cached.get("mtime") == st.st_mtime
            and "error" not in cached
        ):
            continue
        to_probe.append((rel, path, st))

    # Drop cache entries for files that no longer exist.
    for rel in list(files_cache.keys()):
        if rel not in seen_rel_paths:
            del files_cache[rel]

    total = len(to_probe)
    done = 0
    if total and on_progress:
        on_progress(done, total, None)

    if to_probe:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(probe_file, path): (rel, path, st) for rel, path, st in to_probe}
            for fut in as_completed(futures):
                rel, path, st = futures[fut]
                entry = {"size": st.st_size, "mtime": st.st_mtime}
                try:
                    probed = fut.result()
                    entry.update(probed)
                except Exception as exc:
                    entry["error"] = str(exc)
                files_cache[rel] = entry
                done += 1
                if on_progress:
                    on_progress(done, total, rel)

    cache["generated_at"] = time.time()
    cache["root"] = str(root)
    save_cache(cache_path, cache)
    return cache


def _default_progress(done, total, rel):
    sys.stderr.write(f"\r[scan] {done}/{total} probed{' ' * 20}")
    sys.stderr.flush()
    if done == total:
        sys.stderr.write("\n")
