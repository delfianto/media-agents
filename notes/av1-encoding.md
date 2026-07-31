# AV1 and Opus encoding notes

`transcode` is tuned for large Blu-ray/UHD remuxes, not already-efficient web encodes. The executable preset table is in `src/psammophis/medialib/av1_presets.py`; this note explains the policy and the tradeoffs behind it.

## Presets we use

The quality target is selected by coded frame height and an explicit content profile (`film` or `anime`). HDR changes signaling and metadata preservation, not the quality target: reducing CRF just because a source is HDR wastes bits unless a comparison demonstrates a visible benefit.

| Source | SVT preset | Mainline CRF | `svt-av1-hdr` CRF | SVT tune | Film grain | NVENC CQ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2160p film | 4 | 20 | 27 | 0 (VQ) | 10 | 22 |
| 2160p anime | 4 | 22 | 29 | 1 (PSNR) | 4 | 24 |
| 1080p film | 4 | 24 | 31 | 0 (VQ) | 10 | 26 |
| 1080p anime | 4 | 25 | 32 | 1 (PSNR) | 4 | 27 |
| 720p film | 5 | 26 | 33 | 0 (VQ) | 8 | 28 |
| 720p anime | 5 | 27 | 34 | 1 (PSNR) | 4 | 29 |
| SD film/anime | 6 | 28 | 35 | 0/1 | 6/4 | 30/31 |

CPU uses SVT-AV1 preset 4 for archival 1080p/4K work: presets 0–3 cost a large amount of time for diminishing gains, while 5–6 are sensible for lower resolution material. Film uses tune 0 (visual quality) and animation uses tune 1 because the psychovisual optimizer can ring on flat-colored anime edges. All presets enable variance boost. Film-grain presets explicitly set `film-grain-denoise=1`, so source noise is removed before encoding and a statistically similar grain signal is synthesized at playback instead of spending bits coding every noisy pixel.

GPU uses `av1_nvenc` at preset p7 with quality-oriented tuning, multipass, spatial/temporal AQ, and `b_ref_mode=middle`. GPU is faster but generally less efficient per bit than SVT, so its CQ is lower (more generous) than the corresponding SVT target. Dolby Vision and HDR10+ use NVEncC when available so dynamic metadata can be copied; otherwise the decision falls back to CPU rather than silently losing it. GPU selection is capability-probed with a real small encode, not inferred from a GPU model-name table.

Every encode also applies a default video bitrate ceiling of 85% of the source's measured video bitrate. CRF/CQ is a quality target, not a size limit; the cap protects against an already-compressed source becoming larger. For a 40–80+ Mb/s remux this cap normally never binds, so it does not reduce the usual quality target. Use `--no-bitrate-cap` only when deliberately accepting that risk.

## Mainline SVT-AV1 versus `svt-av1-hdr`

Both libraries register the same FFmpeg encoder name, `libsvtav1`, but they do not use the same CRF scale. The HDR fork allocates substantially more bits at the mainline value. Psammophis detects the loader-resolved implementation via the fork-only `svt_hdr_get_version` symbol and selects separate CRF tables; unknown implementations are rejected rather than guessed.

The practical calibration for 2160p film is mainline CRF 20 versus fork CRF 27. On the reference UHD sample that produced approximately 31.1 versus 31.3 Mb/s with near-identical full-reference scores (VMAF about 95.5/95.3, PSNR-Y about 44.7/44.5 dB, SSIM about 0.9989/0.9990). The seven-point offset for other tiers is a useful starting point, not a universal law: validate a new library build or tuning change with `compare` across several titles.

HDR10 signaling is passed explicitly (CICP, mastering display, and content light metadata). Dolby Vision RPU is retained on the CPU path; NVEncC is used for GPU dynamic-metadata paths. Check the output metadata, not only whether the file decodes.

## Expected space savings

There is no honest single percentage: remux bitrate, grain, runtime, audio layout, and the chosen backend dominate. As a planning range, a high-bitrate 4K remux commonly falls from roughly 40–80+ Mb/s video to about 10–25 Mb/s AV1 at this quality target: about 50–75% less video data. 1080p remuxes often land around 40–65% smaller. A clean, already-compressed source may save little or become larger, which is why the bitrate ceiling and a before/after size check exist. These are estimates, not guarantees; run `compare` and inspect difficult grain, shadow, and fast-motion scenes before deleting originals.

## Opus tradeoffs

Audio is encoded to Opus using generous targets interpolated from 64 kb/s mono, 128 kb/s stereo, 320 kb/s 5.1, and 450 kb/s 7.1 (capped at 510 kb/s). At these rates Opus is usually transparent, but it is lossy: archival purists, music-heavy material, or people who need bit-perfect theatrical mixes should keep the original lossless track. Opus also depends on client support and can trigger a transcode on older hardware, although current Plex and Jellyfin clients generally play AV1/Opus well.

For a library viewed through television speakers or a soundbar without 7.1, keeping a full TrueHD/DTS-HD/7.1 track often stores channels that will never be used. A single Opus 5.1/stereo track is therefore a reasonable space and compatibility trade: the video savings are the main win, while audio savings are secondary. Keep multiple tracks when language, commentary, or home theater requirements justify them; `track-strip` is the tool for nuanced track policy before encoding.
