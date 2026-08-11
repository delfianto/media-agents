# AV1 and Opus encoding notes

`transcode` is tuned for large Blu-ray/UHD remuxes, not already-efficient web encodes. The executable preset table is in `src/psammophis/medialib/av1_presets.py`; this note explains the policy and the tradeoffs behind it.

## Presets we use

The quality target is selected by coded frame height and an explicit content profile (`film` or `anime`). HDR changes signaling and metadata preservation, not the quality target: reducing CRF just because a source is HDR wastes bits unless a comparison demonstrates a visible benefit.

| Source | SVT preset | Mainline CRF | `svt-av1-hdr` CRF | SVT tune | Film grain | NVENC CQ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2160p film | 5 | 20 | 27 | 0 (VQ) | 10 | 22 |
| 2160p film-fast | 6 | 20 | 27 | 0 (VQ) | 10 | 22 |
| 2160p anime | 4 | 22 | 29 | 1 (PSNR) | 4 | 24 |
| 1080p film | 4 | 24 | 31 | 0 (VQ) | 10 | 26 |
| 1080p anime | 4 | 25 | 32 | 1 (PSNR) | 4 | 27 |
| 720p film | 5 | 26 | 33 | 0 (VQ) | 8 | 28 |
| 720p anime | 5 | 27 | 34 | 1 (PSNR) | 4 | 29 |
| SD film/anime | 6 | 28 | 35 | 0/1 | 6/4 | 30/31 |

CPU uses SVT-AV1 preset 4 for archival work on tiers that haven't been re-validated (1080p/720p/sd, 2160p anime): presets 0–3 cost a large amount of time for diminishing gains, while 5–6 are sensible for lower resolution material. 2160p film was raised to preset 5 (with a `film-fast` option at 6) after the sweep in "Preset-speed investigation" below. Film uses tune 0 (visual quality) and animation uses tune 1 because the psychovisual optimizer can ring on flat-colored anime edges. All presets enable variance boost. Film-grain presets explicitly set `film-grain-denoise=1`, so source noise is removed before encoding and a statistically similar grain signal is synthesized at playback instead of spending bits coding every noisy pixel.

GPU uses `av1_nvenc` at preset p7 with quality-oriented tuning, multipass, spatial/temporal AQ, and `b_ref_mode=middle`. GPU is faster but generally less efficient per bit than SVT, so its CQ is lower (more generous) than the corresponding SVT target. Dolby Vision and HDR10+ use NVEncC when available so dynamic metadata can be copied; otherwise the decision falls back to CPU rather than silently losing it. GPU selection is capability-probed with a real small encode, not inferred from a GPU model-name table.

Every encode also applies a default video bitrate ceiling of 85% of the source's measured video bitrate. CRF/CQ is a quality target, not a size limit; the cap protects against an already-compressed source becoming larger. For a 40–80+ Mb/s remux this cap normally never binds, so it does not reduce the usual quality target. Use `--no-bitrate-cap` only when deliberately accepting that risk.

## Mainline SVT-AV1 versus `svt-av1-hdr`

Both libraries register the same FFmpeg encoder name, `libsvtav1`, but they do not use the same CRF scale. The HDR fork allocates substantially more bits at the mainline value. Psammophis detects the loader-resolved implementation via the fork-only `svt_hdr_get_version` symbol and selects separate CRF tables; unknown implementations are rejected rather than guessed.

The practical calibration for 2160p film is mainline CRF 20 versus fork CRF 27. On the reference UHD sample that produced approximately 31.1 versus 31.3 Mb/s with near-identical full-reference scores (VMAF about 95.5/95.3, PSNR-Y about 44.7/44.5 dB, SSIM about 0.9989/0.9990). The seven-point offset for other tiers is a useful starting point, not a universal law: validate a new library build or tuning change with `compare` across several titles.

HDR10 signaling is passed explicitly (CICP, mastering display, and content light metadata). Dolby Vision RPU is retained on the CPU path; NVEncC is used for GPU dynamic-metadata paths. Check the output metadata, not only whether the file decodes.

## Preset-speed investigation

The original preset=4 default for 2160p film was never checked against slower or
faster SVT-AV1 presets with real encodes -- it was carried over from general
guidance, not measured against this library's content. A sweep fixed crf=27
(the `svt-av1-hdr` fork's 2160p-film target) and varied only `svt_preset` across
2, 3, 4, 5, 6, 7, run against three 3-minute clips chosen to span this library's
actual grain range: a clean episode (grain score ~0.008), a moderate-grain one
right at the CPU-routing threshold (~0.011), and a heavy-grain one well above it
(~0.018) -- see `medialib/grain.py` for what that score measures. Each combination
was encoded and compared against its source with `compare --mode quick`
(VMAF + SSIMULACRA2).

Findings, consistent across all three clips:

- **Presets 2 and 3 are not worth their time.** Both landed within ~0.3-0.5 VMAF
  and ~1 SSIMULACRA2 point of preset 4's quality, for 2-2.5x the encode time.
  Diminishing returns kick in well before preset 2.
- **Preset 5 is statistically free.** Quality was indistinguishable from preset 4
  (differences at or below what `compare --mode quick`'s 4-clip sampling can
  reliably resolve) while running 7-12% faster. This became the new 2160p-film
  default.
- **Preset 6 is a real, small tradeoff.** 12-22% faster than preset 4, at a
  consistent but modest cost: roughly 0.3-0.5 VMAF and 1-2 SSIMULACRA2 points
  below preset 4/5. Added as `film-fast` (`--profile film-fast`) for batch jobs
  where wall-clock time matters more than the last fraction of quality.
- **Preset 7 is a trap.** Only ~5-6% faster than preset 6 -- a much smaller
  marginal speed gain than every prior step -- but 10-21% *larger* output for
  flat-to-worse VMAF/SSIMULACRA2 on two of the three clips. SVT-AV1's
  preset/CRF interaction is not linear here; preset 6 sits near a local optimum
  and preset 7 steps past it into worse bits-per-quality, not merely faster and
  lower quality. Deliberately not added as a preset. Don't re-add a `film-xfast`
  at 7 (or retest higher presets) without new evidence across multiple titles.

This sweep only covers 2160p film content. It was not extrapolated to 1080p,
720p, sd, or the anime profile -- those still use their original, unvalidated
preset=4/5/6 defaults. `av1_presets.select_preset()` raises a clear error rather
than silently reusing 2160p numbers if `film-fast` is requested for a tier it
hasn't been calibrated for.

## Denoise + nvenc + grav1synth: an nvenc-speed alternative to grain-aware CPU routing

Grain-aware routing (above) sends heavily grainy sources to CPU because
`libsvtav1`'s `film-grain-denoise=1` strips real per-pixel noise before
encoding and has the decoder resynthesize a statistically matched pattern
instead of spending bits coding every noisy pixel -- `av1_nvenc` has no
equivalent mechanism. That's a real, measured tradeoff: CPU is far more
bit-efficient on grainy content but roughly 6x slower than nvenc on this
library's hardware.

[`grav1synth`](https://github.com/rust-av/grav1synth) (third-party, MIT,
patched locally -- see `vendor-patches/grav1synth/README.md`) can read and
write AV1's film-grain-synthesis OBU metadata directly in an existing
bitstream, without re-encoding. That makes the same denoise-then-resynthesize
split possible as separate steps instead of inside one encoder: denoise the
source with ffmpeg, encode the now-clean picture with `av1_nvenc` (fast, and
efficient since there's no grain left to spend bits on), diff the original
against the denoised copy to get a real grain table, then inject that table
into the nvenc output's frame headers. `--backend nvenc-degrain`
(`src/psammophis/transcode/degrain.py`) implements this as five steps:
denoise (ffmpeg, `hqdn3d`, FFV1 lossless output), nvenc-encode the denoised
picture, `grav1synth diff`, `grav1synth apply`, then a final mux that pulls
audio/subtitles fresh from the original source (reusing command.py's own
audio/subtitle builders) rather than from anything grav1synth touched.

Two real bugs blocked this before it could work at all, both in
`grav1synth`'s own write path (not this repo's code) -- traced with its
`RUST_LOG=trace` field-level output and fixed with two local patches (see
`vendor-patches/grav1synth/README.md` for the full diagnosis): its sequence-
header rewriter patched the wrong byte in write mode whenever the
`film_grain_params_present` flag didn't happen to fall in the OBU's last
byte (encoder-dependent, not something either bug's presence or absence
say anything about grain quality), and a related size-accounting bug grew a
one-bit-only edit's declared OBU size by a spurious byte. A third, separate
limitation was *not* a bug worth chasing: `grav1synth apply` reliably
corrupts its output when audio/subtitle streams are muxed alongside video in
the same file, even though the identical operation is fine on a video-only
file. Rather than debug that further, every `grav1synth` step in this
pipeline is designed to only ever touch video-only intermediates.

Deliberately narrow for its first release, matching how `film-fast` above
only shipped for what was actually measured:

- **SDR/HDR10 only.** Dolby Vision and HDR10+ are refused outright --
  `degrain.check_supported` raises rather than attempting RPU/dynamic-SEI
  preservation through a pipeline that has never been tested against either.
- **Never selected by `--backend auto`.** `av1_backend.choose_backend`'s
  shared grain-routing decision (also used by the read-only `analyze` skill)
  is completely untouched by this path -- explicit `--backend nvenc-degrain`
  only, so a much less battle-tested pipeline can't silently become anyone's
  default.
- **Costs real disk space.** The denoise step writes an FFV1 lossless
  intermediate, roughly source-sized, so it can be fed to `grav1synth diff`
  without lossy compression artifacts contaminating the grain model. Not
  optional -- see `degrain.py`'s module docstring for why a lossy
  intermediate (even a "high quality" one) would misread its own compression
  artifacts as grain.

Verified end-to-end through the real `transcode run --backend nvenc-degrain`
path (not just its pure command builders in isolation): a full run against a
real clip produced a file that passed every existing `verify_output` check
(video/audio presence, duration match, measured track statistics, no stale
source statistics) and decoded cleanly. Not yet validated against a large,
varied batch the way the preset-speed sweep above was -- treat it as usable,
not as thoroughly proven as the default CPU/nvenc paths.

## Expected space savings

There is no honest single percentage: remux bitrate, grain, runtime, audio layout, and the chosen backend dominate. As a planning range, a high-bitrate 4K remux commonly falls from roughly 40–80+ Mb/s video to about 10–25 Mb/s AV1 at this quality target: about 50–75% less video data. 1080p remuxes often land around 40–65% smaller. A clean, already-compressed source may save little or become larger, which is why the bitrate ceiling and a before/after size check exist. These are estimates, not guarantees; run `compare` and inspect difficult grain, shadow, and fast-motion scenes before deleting originals.

## Opus tradeoffs

Audio is encoded to Opus using generous targets interpolated from 64 kb/s mono, 128 kb/s stereo, 320 kb/s 5.1, and 450 kb/s 7.1 (capped at 510 kb/s). At these rates Opus is usually transparent, but it is lossy: archival purists, music-heavy material, or people who need bit-perfect theatrical mixes should keep the original lossless track. Opus also depends on client support and can trigger a transcode on older hardware, although current Plex and Jellyfin clients generally play AV1/Opus well.

For a library viewed through television speakers or a soundbar without 7.1, keeping a full TrueHD/DTS-HD/7.1 track often stores channels that will never be used. A single Opus 5.1/stereo track is therefore a reasonable space and compatibility trade: the video savings are the main win, while audio savings are secondary. Keep multiple tracks when language, commentary, or home theater requirements justify them; `track-strip` is the tool for nuanced track policy before encoding.
