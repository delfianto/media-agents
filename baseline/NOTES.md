# Pre-refactor baseline (Phase 0)

Date: 2026-07-31
Python: 3.14.6
uv: 0.12.0
FFmpeg: n8.1.2
NVEncC: 9.27 (r3981)

## git status
Unrelated user changes: only untracked `REFACTOR.md` (and this baseline/).

## Quality gate
- pytest: 400 passed in 0.29s
- ruff check: All checks passed
- ruff format --check: 136 files already formatted
- basedpyright: 0 errors, 0 warnings, 0 notes

## Help fixtures
Captured under `baseline/help/` via temporary media root with `.agents` symlink
to this checkout (required by current `__main__.py` bootstrap).

## Exit codes (sampled)
See baseline/exit-codes.txt for recorded samples. Notable: several batch CLIs
count errors but may still exit 0 (documented in REFACTOR plan findings).

## Progress samples
- `baseline/progress/ffmpeg-progress-sample.txt` — FFmpeg `-progress pipe:1` keys
  observed: frame, fps, stream_0_0_q, bitrate, total_size, out_time_us,
  out_time_ms, out_time, dup_frames, drop_frames, speed, progress.
- NVEncC version and encode samples under `baseline/progress/`.

## Cache/backup paths (from source; not modified on real media)
- `.cache/av1transcode/originals` — AV1 backups
- `.cache/av1transcode/logs` — AV1 encoder logs
- `.cache/trackstrip/originals` — track-strip backups
- `.cache/trackstrip/scan.json` — track scan cache

## Real media
No real media library was modified during baseline capture.
