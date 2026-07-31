# Metric and preprocessing notes

## How `compare` works

`compare` is a read-only, full-reference check: the source is the reference and the encoded file is the distorted input. It probes both files, rejects incompatible dimensions/timelines, samples corresponding frames at evenly stratified points (or uses every frame with `--mode full`), resets both PTS values from frame index, and runs the selected metrics. Reports include the sample count, global score distribution, and worst samples rather than hiding everything behind one average. Sampling makes a quick regression practical; full mode is the final check when runtime permits.

For video, start with VMAF plus PSNR, SSIM, and MS-SSIM. For still-image conversion, compare decoded, orientation-corrected, color-managed pixels with SSIMULACRA2 as the perceptual metric and ImageMagick SSIM/PSNR/RMSE as diagnostic signals. Metrics do not test audio, subtitle retention, Dolby Vision RPU, HDR10+ metadata, or whether a particular Plex/Jellyfin client can decode the result; those need `ffprobe`/playback checks as well.

### What values to check

- **VMAF:** higher is better. For this project's high-quality remux target, look for a high median (typically around 95+ for difficult 4K scenes) and, more importantly, no severe low-percentile or worst-frame collapse. Compare the same title and model; do not treat a single universal pass/fail number as scientific truth.
- **PSNR:** higher is better and is useful for detecting gross errors, but it is not perceptual and can dislike legitimate grain synthesis.
- **SSIM/MS-SSIM:** closer to 1 is better. Use them as regression signals, not as proof that texture and motion look natural.
- **SSIMULACRA2:** higher is better, with roughly 30/50/70/90 corresponding to low/medium/high/very-high quality in its image examples. Those bands are orientation only, not video acceptance thresholds.

Inspect the worst frames visually. A good encode has stable scores across bright, dark, grainy, animated, and fast-motion samples; a high average that hides one broken scene is not acceptable. HDR comparisons are repeatable but not fully calibrated because published VMAF consumer models are not HDR specific. Grain denoising and film-grain synthesis can lower pixel metrics while looking better, so pair the numbers with visual inspection.

## VMAF

VMAF is a full-reference video metric: distorted and reference frames must be spatially and temporally aligned. Netflix's FFmpeg guidance explicitly resets PTS and passes distorted input to `libvmaf` before reference input. For the matching-CFR inputs this skill accepts, both timelines are rebuilt from frame index (`setpts=N/(fps*TB)`) rather than merely subtracting the first PTS: independent Matroska muxes round 24000/1001 timestamps to milliseconds and otherwise produce periodic off-by-one comparisons with catastrophically bogus worst-frame scores. The standard model targets 1080p television viewing; the 4K model targets a 4K display at a closer viewing distance. Prefer the installed 4K JSON model for native 2160p comparisons.

The implementation requests VMAF, PSNR, float SSIM, and float MS-SSIM from one libvmaf pass. Report global per-frame percentiles rather than averaging already-averaged clip scores.

VMAF's published consumer models are not HDR-specific. Native PQ comparison is still useful as a repeatable regression signal because both sides share the same transfer function, but its mapping to subjective quality is not calibrated for HDR. Reports must say so.

## SSIMULACRA2

SSIMULACRA2 is an image metric from the JPEG XL reference implementation. Its official scale is unbounded below through 100; approximately 30 is low, 50 medium, 70 high, and 90 very high quality for the image-compression examples used to describe it. Those labels are orientation, not universal pass/fail limits for video.

For SDR, extract matched RGB frames. For PQ/HLG HDR, identically convert both frames to linear light, apply the same Mobius tone map, convert to BT.709 sRGB, and only then invoke SSIMULACRA2. Record that transformation because a tone-mapped still metric cannot validate the HDR presentation itself.

Do not silently replace the official executable with a similarly named Rust or GPU implementation: small implementation differences make historical scores incomparable. Add another backend only as an explicit, named metric.

## Still images

Use SSIMULACRA2 as the principal perceptual still-image metric. Do not use VMAF: its model includes motion features and was trained for video viewing conditions.

Apply EXIF orientation, honor embedded ICC profiles through LittleCMS, and normalize both files to 16-bit sRGB RGBA before comparing. This makes JPEG-to-AVIF and PNG-to-AVIF comparisons use the same displayed pixels while retaining transparency. Reject dimension mismatches rather than scaling one side, because resampling changes the artifact under measurement.

Compute ImageMagick SSIM, PSNR, and normalized RMSE on the normalized images. These remain useful regression signals, especially for lossless conversions, but they are not substitutes for a perceptual metric. An untagged image has no authoritative source color space; disclose that color-management results depend on the decoder's format defaults.

## Known limitations

- Metrics cannot assess audio, subtitle, Dolby Vision RPU correctness, or playback compatibility.
- Independent seeking is safe for CFR source/encode pairs with matching timeline structure; edited, variable-frame-rate, frame-interpolated, or offset files require external alignment before comparison.
- Grain synthesis and denoising may lower pixel-correspondence scores despite good subjective results.
- A stratified sample is representative, not exhaustive. Use `--mode full` when validating a final preset and runtime is acceptable.
- Image comparison evaluates decoded, color-normalized pixels. It does not validate metadata preservation, encoder compatibility, animation timing, or how different applications implement HDR gain maps.
