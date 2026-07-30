---
name: analyze
description: Use when the user wants to see exactly what av1-transcode's heuristics would decide for a video before actually running it - which resolution/profile preset, which encoder settings (CRF/CQ, tune, film-grain), and which backend (cpu/libsvtav1 vs nvenc/av1_nvenc) it would pick and why, including a measured grain/noise score that routes heavily grainy or noisy sources to cpu (better film-grain synthesis) and clean digital sources to nvenc (much faster) under --backend auto. Triggers on phrases like "what would av1-transcode do with this", "how grainy is this movie", "why would this use cpu instead of gpu", "check the encode plan without running it", "analyze this video's grain level", or any request to preview/explain the encoding decision instead of taking it on faith. Read-only - never encodes, writes, or executes anything, only reports.
allowed-tools:
    - Bash
metadata:
    author: "analyze"
    focus: "Read-only, transparent report of av1-transcode's preset/backend decision, including grain measurement"
---

# analyze

`av1-transcode probe` already previews which preset and backend it would pick, but that decision was, until now, opaque on one specific axis: how much of a role a source's actual grain/noise level should play in the cpu-vs-nvenc choice. This skill exists to make that (and the rest of the decision) fully visible on its own, without needing to invoke `av1-transcode` itself -- point it at a file or a `--path` filter and it reports the exact same facts `av1-transcode`'s own heuristics would act on: resolution tier, dynamic range, the resolved preset's concrete encoder settings for both backends, a measured grain/noise score, and the resulting backend/engine decision with the reasoning spelled out.

The toolkit lives at `scripts/analyze/` (next to this file), a thin, zero-third-party-dependency Python package. It does no encoding decision-making of its own -- every fact it reports comes from `lib/medialib` (`videoprobe`, `colorinfo`, `av1_presets`, `av1_backend`, `grain`), the exact same modules `av1-transcode` itself imports, so this skill's report and `av1-transcode`'s actual behavior can never independently drift out of sync (see root `AGENTS.md`'s `lib/medialib` section for why that matters).

## Running it

```bash
python3 <path-to-this-skill>/scripts/analyze/__main__.py [options]
```

```bash
# analyze everything under the library root (can be slow -- grain measurement
# runs a few short ffmpeg samples per file; narrow with --path first)
python3 scripts/analyze/__main__.py

# analyze one title
python3 scripts/analyze/__main__.py --path "Some Movie"

# anime/cartoon sources: --profile is never auto-detected, same reasoning as av1-transcode
python3 scripts/analyze/__main__.py --path "Some Anime" --profile anime

# machine-readable output, one JSON object per file, for scripting
python3 scripts/analyze/__main__.py --path "Some Movie" --json

# skip grain measurement entirely (faster, reports the pre-grain backend decision)
python3 scripts/analyze/__main__.py --path "Some Movie" --no-grain-routing

# override the grain->cpu threshold for this run without editing code
python3 scripts/analyze/__main__.py --path "Some Movie" --grain-threshold 0.015
```

Sample text output for one file:

```
Some Movie (2001)/Some Movie (2001).mkv
    1920x1080 (1080p) hevc Main 10 HDR10  size=38.2 GiB
    preset: 1080p-film -- 1080p live-action Blu-ray remux -- the common case
    cpu:   preset=4 crf=22 tune=0 film-grain=10 extra={'enable-variance-boost': '1'}
    nvenc: preset=p7 tune=uhq cq=24 extra={'spatial-aq': '1', 'temporal-aq': '1', 'aq-strength': '10'}
    backend: cpu via ffmpeg  (gpu=0, nvencc=no)
    grain: 0.0143 (cpu preferred, threshold=0.0120, samples=[0.0131, 0.0149, 0.0148])
```

## What "grain" means here, and why it can change the backend

SVT-AV1's `--film-grain` synthesis (denoise before encoding, resynthesize a statistically matched grain pattern at decode time) handles real per-pixel noise -- film grain, or the synthetic dither some anime BD encodes add on purpose to hide gradient banding -- far more efficiently than plain CRF/CQ; `av1_nvenc` has no equivalent. A source measured as grainy/noisy enough therefore prefers `cpu`/`libsvtav1` even when a GPU is available, trading `nvenc`'s large speed advantage for `cpu`'s better bits-per-unit-of-noise efficiency.

Grain is measured by denoising a few short samples spread across the file (avoiding opening titles/credits) and comparing each to the original via SSIM -- how much a denoise filter actually changes a sample is a much better proxy for "how much noise is here" than it first seems: a naive bit-plane noise filter (ffmpeg's own `bitplanenoise`) was tried first and rejected after measuring it directly against three real files in this library -- it saturated near its ceiling for almost every compressed source regardless of actual grain, and tracked edge sharpness more than grain (a sharp-lined anime episode scored *noisier* than an actual grainy 35mm scan). See `lib/medialib/grain.py`'s module docstring for the full writeup and the real numbers behind `GRAIN_CPU_THRESHOLD`.

**This threshold is provisional** -- calibrated against only a handful of real titles so far, not a whole-library audit. `--grain-threshold` overrides it per-invocation; recalibrating the default in `medialib/grain.py` should wait until it's been checked against more of this library's actual files, the same way `track-strip`'s SDH/anime-detection thresholds were tuned only after whole-library auditing caught real false positives.

Grain measurement only runs when it could actually change the outcome (`av1_backend.grain_routing_applies`): if there's no GPU at all, or Dolby Vision without `nvencc` already forces `cpu` regardless, the extra ffmpeg sampling passes are skipped automatically -- `--no-grain-routing` skips them unconditionally instead.

## Relationship to `av1-transcode`

This skill reports; it never runs an encode, writes a file, or executes ffmpeg beyond the read-only probing/sampling described above. Once a reported plan looks right, actually run it with `av1-transcode`'s own `run` command (see that skill's `SKILL.md`) -- `analyze` and `av1-transcode probe` will always agree, since both call the same `lib/medialib` functions with the same inputs.

## Extending

`scripts/analyze/report.py` holds the pure decision-assembly and formatting logic (`build_analysis`, `format_analysis`, `analysis_to_dict`, `classify_dynamic_range`) -- no I/O, so it's fully unit-tested (`test_analyze_report.py`) without mocking ffprobe/ffmpeg. `scripts/analyze/cli.py` does the actual I/O: walking the library, probing each file, measuring grain when `grain_routing_applies`, and printing/serializing the result. Everything encoding-decision-specific (`resolution_tier`/`select_preset`/`choose_backend`/`choose_encode_engine`/`measure_grain`) is imported from `lib/medialib`, not reimplemented here -- if a report here ever looks wrong, the bug is almost certainly in one of those shared modules (and fixing it there fixes `av1-transcode` too), not in this skill's own thin wrapper.
