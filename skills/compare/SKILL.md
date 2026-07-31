---
name: compare
description: Deeply compare an encoded video or compressed image against its original/reference source with objective perceptual metrics. Use for source-versus-encode video validation, AV1/x265/x264 preset audits, JPEG/PNG-to-AVIF/WebP/JXL comparisons, image-quality checks, CRF/CQ comparisons, transparency judgments, compression-artifact investigations, or comparing candidate encodes. Runs stratified VMAF plus PSNR/SSIM/MS-SSIM for video; runs color-managed SSIM/PSNR/RMSE and optional SSIMULACRA2 for images; handles HDR consistently, reports lower-tail and worst-frame results for video, and never modifies either input.
---

# Compare

Measure what an encode lost relative to a reference instead of judging quality
from bitrate alone. Treat metrics as complementary signals: inspect the worst
samples and caveats rather than turning one mean score into a promise of
transparency.

Input type is auto-detected. Use `--media-type video|image` only to override
ambiguous extensions.

## Compare video

Invoke from the media-library root:

```bash
.agents/run.sh compare \
  --reference "ingest/source.mkv" \
  --distorted "transcode/encode.mkv"
```

The default `deep` mode measures twelve five-second clips distributed across
the common duration. It runs VMAF at every frame in those clips and
SSIMULACRA2 on 24 stratified still frames when the official `ssimulacra2`
binary is installed.

Choose the workload explicitly when needed:

```bash
# Four short clips, useful as a smoke test
.agents/run.sh compare ... --mode quick

# Every frame; potentially very slow for a feature-length 4K source
.agents/run.sh compare ... --mode full

# Persist the complete per-frame result
.agents/run.sh compare ... --json-out comparison.json
```

Use `--clips`, `--clip-duration`, or `--ssimulacra-frames` to override a
mode. Use `--skip-ssimulacra2` when only the video metrics are wanted, or
`--require-ssimulacra2` when its absence must fail the run.

## Compare images

Pass the original lossless or higher-quality image as the reference and the
compressed image as distorted:

```bash
.agents/run.sh compare \
  --reference "original.png" \
  --distorted "encoded.avif"

.agents/run.sh compare \
  --reference "original.jpg" \
  --distorted "encoded.avif" \
  --json-out image-comparison.json
```

The image path:

1. Applies EXIF orientation.
2. Uses ImageMagick/LittleCMS to convert both inputs to the same 16-bit sRGB
   RGBA working representation, including alpha.
3. Requires identical displayed dimensions after orientation.
4. Computes SSIM, PSNR, and normalized RMSE across the normalized pixels.
5. Computes SSIMULACRA2 on those same normalized images when its official
   executable is installed.

Do not run VMAF on a still image. Its video model and motion features are not
a replacement for a purpose-built image metric.

## Integrity rules

1. Confirm which file is the reference and which is distorted. Reversing them
   invalidates full-reference metrics.
2. Require matching resolution and frame rate. Refuse duration differences
   above the alignment tolerance; do not silently scale, interpolate, or
   compare unrelated frames.
3. For the preflight-validated matching-CFR pair, rebuild both sampled
   timelines from frame index before libvmaf. This avoids periodic off-by-one
   comparisons caused by independently rounded Matroska timestamps. FFmpeg/
   libvmaf expects distorted input first and reference input second.
4. Use the Netflix 4K model for 2160p sources when installed, otherwise use
   the standard model and disclose the fallback.
5. For HDR, run VMAF in the native 10-bit signal domain and label its score as
   not subjectively calibrated for HDR. Tone-map both sides with the exact
   same deterministic pipeline before SSIMULACRA2, which expects display
   images rather than PQ code values.
6. For images, normalize both inputs through exactly the same color-managed
   pipeline and compare alpha rather than silently flattening transparency.
7. Keep all work temporary unless `--json-out` is explicitly requested.
   Never alter, remux, rename, or delete either media file.

## Interpret the report

- Prefer the lower-tail VMAF values (`p1`, `p5`) and worst-frame list over the
  mean alone. A high mean can hide a few visibly damaged scenes.
- Use PSNR and SSIM/MS-SSIM as regression and signal-fidelity checks, not as
  standalone perceptual verdicts.
- SSIMULACRA2 is a per-image metric. Its sampled distribution can reveal
  texture damage VMAF misses, but it does not model temporal artifacts.
- For an image comparison, treat SSIMULACRA2 as the primary perceptual signal
  and use PSNR/SSIM/RMSE as complementary pixel-fidelity diagnostics.
- Film-grain synthesis deliberately changes pixels. Full-reference metrics
  can penalize a perceptually convincing grain reconstruction because it is
  not pixel-identical to the source.
- Always finish with human inspection of the reported worst timestamps on the
  intended HDR/SDR playback path before accepting a new archival preset.

Read [`notes/quality-comparison.md`](../../notes/quality-comparison.md) when changing model
selection, HDR preprocessing, thresholds, or report language.

## Dependencies

Video requires `ffprobe` and FFmpeg built with the `libvmaf` filter. Image
comparison requires ImageMagick with decoders for both formats and LittleCMS
for embedded color profiles. The official libjxl `ssimulacra2` executable is
optional for both. Missing optional support is printed prominently and
recorded in JSON; it is never silently substituted with another
implementation.

## Progress for long comparisons

Full-length video comparisons can run a long time. Prefer JSONL progress so the
session can be observed without ANSI noise:

```bash
.agents/run.sh --reporter jsonl --progress-interval 30 compare \
  --reference "ingest/source.mkv" \
  --distorted "transcode/encode.mkv"
```

- Continue observing the **same** process/session; do not relaunch merely because
  an interval produced no output.
- Accept success only when both process exit code and any terminal reporter event
  indicate success.
