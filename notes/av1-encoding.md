# AV1 and Opus encoding notes

`transcode` is tuned for large Blu-ray/UHD remuxes, not already-efficient web encodes. The executable preset table is in `src/psammophis/medialib/av1_presets.py`; this note explains the policy and the tradeoffs behind it.

## Success criterion

The pass/fail bar is **transparent or near-transparent against the source you actually have**. For a UHD remux that means remux-transparent. For a 21 Mb/s 4K AVC web encode it means transparent *to that AVC* — you cannot reconstruct remux quality from a source that never had it, and the remux CRF/CQ table will overspend (WowGirls CQ 22 wanted 40–60 Mb/s from a 21 Mb/s file). For a Constrained Baseline stream it means "don't make the mush worse, and don't spend remux bits on it."

Judge a candidate with `compare` / `abtest` on multiple windows: prefer VMAF **p5/min** and SSIMULACRA2, then inspect the worst stills at 1:1. Do not treat source bitrate as a quality target — a 60 Mb/s remux is "what the disc stored," not "what the picture needs." Do not treat a tiny CQ-driven bitrate as proof of efficiency if the stills lost texture.

## Source class (master / webrip / stream / lowest)

Resolution × film/anime is not enough. The third axis is **how the file was delivered**:

| Class | What it is | Detection | Quality policy |
| --- | --- | --- | --- |
| `master` | Blu-ray remux or raw capture | 10-bit HEVC/AV1 (or very high-bitrate AVC) at or above the master floor (4K 35 Mb/s, 1080p 12, 720p 8, SD 4) | Calibrated table as written |
| `webrip` | Decent commercial encode, already more compressed than a remux (WowGirls-style 4K AVC ~20 Mb/s) | Between stream ceiling and master floor | +3 CRF / +4 CQ vs master table |
| `stream` | Heavily streaming-optimized | Constrained/Baseline profile, **or** bitrate at/below the stream ceiling (4K 8 Mb/s, 1080p 3.5, 720p 1.8, SD 1.2) but above lowest | +6 CRF / +8 CQ; cap defaults to 70% of source |
| `lowest` | Recordings, vintage films with dubious compression, legacy codecs (MPEG-2, DivX/Xvid, WMV) | Legacy codec family, **or** bitrate at/below the lowest ceiling (4K 3 Mb/s, 1080p 1.5, 720p 0.8, SD 0.6) | +10 CRF / +12 CQ; cap defaults to 60% of source |

`--source-class auto` (default) runs `sourceclass.classify_source`. Override when the heuristic is wrong. Content profile (film/anime) stays explicit.

The non-master deltas are **starting points**, not a fourth full preset table. webrip +4 CQ is the WowGirls overspend measurement (uncapped CQ 22 wanted 40–60 Mb/s from a 21 Mb/s 4K AVC). stream +8 / lowest +12 were bounded against 75 Video DownloadHelper webcam captures (see "Lowest-class webcam (VDH)" below and `notes/source-class-audit.md`). Validate a new cell with `compare` before treating it as master-grade evidence. Probe/analyze print `source-class:` on every file so a mis-bucket is visible before `--yes`.

## Presets we use

The quality target is selected by coded frame height and an explicit content profile (`film` or `anime`). HDR changes signaling and metadata preservation, not the quality target: reducing CRF just because a source is HDR wastes bits unless a comparison demonstrates a visible benefit.

| Source | SVT preset | Mainline CRF | `svt-av1-hdr` CRF | SVT tune | Film grain | NVENC CQ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2160p film | 5 | 19 | 26 | 0 (VQ) | 0 | 22 |
| 2160p film-fast | 6 | 19 | 26 | 0 (VQ) | 0 | 22 |
| 2160p anime | 5 | 22 | 29 | 1 (PSNR) | 0 | 16 |
| 2160p anime-fast | 6 | 22 | 29 | 1 (PSNR) | 0 | 16 |
| 1080p film | 4 | 13 | 20 | 0 (VQ) | 0 | 22 |
| 1080p anime | 4 | 25 | 32 | 1 (PSNR) | 0 | 27 |
| 720p film | 5 | 15 | 22 | 0 (VQ) | 0 | 24 |
| 720p anime | 5 | 27 | 34 | 1 (PSNR) | 0 | 29 |
| SD film | 6 | 17 | 24 | 0 (VQ) | 0 | 24 |
| SD anime | 6 | 28 | 35 | 1 (PSNR) | 0 | 31 |

CPU uses SVT-AV1 preset 4 for archival work on tiers that haven't been re-validated (1080p/720p/sd): presets 0–3 cost a large amount of time for diminishing gains, while 5–6 are sensible for lower resolution material. 2160p film was raised to preset 5 (with a `film-fast` option at 6) and 2160p anime was raised to preset 5 (with an `anime-fast` option at 6) after the sweeps in "Preset-speed investigation" below. Film uses tune 0 (visual quality) and animation uses tune 1 because the psychovisual optimizer can ring on flat-colored anime edges. All presets enable variance boost.

Film-grain synthesis is **off** by default. The HDR fork itself disables source denoising because it "often harms visual fidelity," and a six-clip full-frame A/B on a digital 4K close-up (Lab Mutation) showed `film-grain=10` + `film-grain-denoise=1` was the smear: fair VMAF was already 95+ even on the old recipe, but SSIMULACRA2 and pores/specularity recovered once FGS was removed. CRF 26 (fork) / 19 (mainline) on 2160p film is one point more transparent than the previous 27/20 pair. Do not turn FGS back on as a size trick without a per-title grain check.

Do not copy the May 2025 Doom9 "use tune 3" advice onto today's binary. In that first HDR-fork post, tune 3 **was** the film-grain retention tune (encode real grain: TF/CDEF/restoration off, high `ac-bias`). After MS_SSIM was added, that tune was renumbered to **tune 5**; tune 3 is now IQ / still-image / AVIF. Tune 5 is for grainy film you want to keep as pixels, at preset 2, not a general 4K default. Our clip A/B of tune 5 / CRF 30 / preset 2 was slower and larger with worse SSIMULACRA2 than tune 0 / CRF 26 / no FGS on digital 4K.

GPU uses `av1_nvenc` at preset p7 with quality-oriented tuning, multipass, spatial/temporal AQ, and `b_ref_mode=middle`. GPU is faster but generally less efficient per bit than SVT, so its CQ is lower (more generous) than the corresponding SVT target. Dolby Vision and HDR10+ use NVEncC when available so dynamic metadata can be copied; otherwise the decision falls back to CPU rather than silently losing it. The NVEncC argv must carry the same tune / lookahead / AQ / B-ref extras as the ffmpeg path — omitting them is how the Violet Evergarden encode logged `Lookahead off / AQ off / Tuning hq`. GPU selection is capability-probed with a real small encode, not inferred from a GPU model-name table.

AV1 NVENC CQ is a **0–63** quality stop (`--qvbr N` is `--vbr 0 --vbr-quality N`), not an x264 0–51 CRF. QVBR does not spend a fraction of the remux bitrate; it stops once it thinks it hit that score.

Every encode also applies a default video bitrate ceiling of 85% of the source's measured video bitrate. CRF/CQ is a quality target, not a size limit; the cap protects against an already-compressed source becoming larger. For a 40–80+ Mb/s remux this cap normally never binds, so it does not reduce the usual quality target. Use `--no-bitrate-cap` only when deliberately accepting that risk.

## Mainline SVT-AV1 versus `svt-av1-hdr`

Both libraries register the same FFmpeg encoder name, `libsvtav1`, but they do not use the same CRF scale. The HDR fork allocates substantially more bits at the mainline value. Psammophis detects the loader-resolved implementation via the fork-only `svt_hdr_get_version` symbol and selects separate CRF tables; unknown implementations are rejected rather than guessed.

The scale offset was first measured at mainline CRF 20 versus fork CRF 27 on a reference UHD sample (approximately 31.1 versus 31.3 Mb/s, VMAF about 95.5/95.3). 2160p film now uses 19 / 26 — same seven-point gap, one CRF step more transparent after the Lab Mutation A/B. The offset for other tiers is a starting point, not a universal law: validate a new library build or tuning change with `compare` across several titles.

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

This sweep only covers 2160p film content. It was not extrapolated to 1080p or
720p/sd -- those still use their original, unvalidated preset=4/5/6 defaults.
`av1_presets.select_preset()` raises a clear error rather than silently
reusing 2160p numbers if `film-fast` is requested for a tier it hasn't been
calibrated for.

### 2160p anime addendum

The anime profile's own preset=4 default was equally unvalidated. A second
sweep reused film's methodology on the strength of its own result -- crf fixed
at 29 (the `svt-av1-hdr` fork's 2160p-anime target), `svt_preset` varied over
just 5, 6, 7 (not re-testing 2-4, since film's sweep already established that
lower presets aren't worth their time and this sweep exists to place the
speed/quality line, not re-litigate it) -- against three 3-minute clips from
this library's two 4K anime sources: one from *The Ghost in the Shell* (a
detail-dense, AI-upscaled cyberpunk episode) and two from *Violet Evergarden:
The Movie* (a dialogue/painterly scene and a later, more dynamic one), chosen
to span a real range of animation complexity rather than grain level, which
anime barely has (both sources measured well under the CPU-routing threshold:
grain scores 0.0071 and 0.0029).

Findings, same shape as film's:

- **Preset 5 was the best bits-per-quality of the three on every clip.**
  Raised as the new 2160p-anime default, same as film.
- **Preset 6 is a real, scene-dependent tradeoff.** 7-30% faster than preset 5
  depending on clip complexity (fastest gain on the busiest clip), at a cost
  that scales with the same complexity: ~0.04 VMAF / ~0.34 SSIMULACRA2 on the
  calm dialogue clip, up to ~0.52 VMAF / ~2.2 SSIMULACRA2 on the detail-dense
  one. Added as `anime-fast` (`--profile anime-fast`) with the same intent as
  `film-fast`: batch jobs where wall-clock time matters more than the last
  fraction of quality.
- **Preset 7 is the same trap film found.** Only a few more percentage points
  faster than preset 6 (roughly +5-9pp, a much smaller marginal gain than the
  5-to-6 step), but 8-13% *larger* output than preset 6 on every clip, for
  flat-to-worse VMAF/SSIMULACRA2 on two of the three. Deliberately not added
  as a preset, for the same reason `film-xfast` wasn't: don't re-add an
  `anime-xfast` at 7 (or retest higher presets) without new evidence across
  multiple titles.

One methodological gap worth naming: unlike film's sweep, this one didn't
also re-test preset 4 against 5 for anime specifically -- it started directly
at 5 on the strength of film's already-established 4-vs-5 result. Tune (1 vs
film's 0) and content type differ enough between the two profiles that this
is an assumption carried over, not independently re-proven; revisit if a
future anime encode looks meaningfully different from what preset 4 would
have produced.

### 2160p anime NVENC remux audit (Violet Evergarden)

The existing `anime/Violet Evergarden The Movie (2020).mkv` was NVEncC
`--qvbr 24` against the 62.1 Mb/s HEVC remux. Video landed at **1.46 Mb/s**.
Six random 20 s windows vs the remux (active 3840×1608, letterbox cropped):

- CQ 24 (shipped): mean VMAF 95.66 / p5 94.88 / S2 79.80; worst window
  (painted cliff) VMAF 95.03 / S2 75.36. 1:1 stills lost grass brushwork
  and water sparkle. **Failed the transparency bar.**
- Same cliff clip, CQ 20: VMAF 95.83 / S2 79.24 — still short.
- Same cliff clip, CQ 16: VMAF 96.28 / p5 95.33 / S2 82.00. 1:1 grass and
  cliff hold the remux paint. Matches the CPU anime remux-bar VMAF
  (~96.25) and is within ~2 S2 of that CPU recipe (83.94).
- CQ 16 + AQ/lookahead/uhq on all six windows: mean VMAF 96.66 / p5 95.97
  / S2 84.52. Isolated S2 mins of 17–34 are still-seek cut mismatches
  (VMAF at those timestamps stays mid-95s), same artifact as the CQ 24
  audit. Turning AQ on at CQ 24 only moved S2 75 → 78 and made the
  worst-frame tail worse. The CQ number is the lever; extras are not a
  substitute.

2160p-anime / anime-fast GPU CQ is therefore **16**. Expected video rate on
this title is roughly 3–5 Mb/s, not 1.5.

### Film NVENC across resolutions (WowGirls / MetArtX)

Same transparency bar, grain-free digital live-action instead of a BD remux:

- WowGirls *Hypnotic Whisper Of Euphoria* — 3840×2160 AVC High 8-bit,
  ~21 Mb/s, 59.94 fps, grain 0.0106 (nvenc-ok).
- MetArtX *Kitchen Cravings 2* 4K — 3840×2160 AVC High 8-bit, ~25 Mb/s,
  29.97 fps, grain 0.0074 (very clean). Native 1080p of the same title is
  Constrained Baseline ~5 Mb/s and native 480p is ~1 Mb/s; those are
  already-crushed web rungs, not masters. 1080p / 720p / 480p CQ was
  judged on lanczos downscales of the 4K windows so the source picture
  was still clean.

Four 10 s windows per title. Uncapped `av1_nvenc` (same extras as the
ffmpeg path). SDR VMAF is subjectively calibrated here.

| Tier | Old CQ | Result | New CQ | Result |
| --- | ---: | --- | ---: | --- |
| 2160p film | 22 | VMAF 98.7–99.97 / p5 ≥97.8 / S2 80.6–84.5; 1:1 lace/skin hold | **22** (keep) | — |
| 1080p film | 26 | VMAF ~98.3 / p5 95.5 / S2 80.4; sweater fuzz slightly soft | **22** | VMAF ~99.0 / p5 96.1–97.6 / S2 82.2–83.6 |
| 720p film | 28 | VMAF ~97.8 / p5 94.9 / S2 81.6 | **24** | VMAF ~98.6 / p5 95.8–96.7 / S2 83.5–84.6 |
| sd film | 30 | VMAF ~96.9 / p5 94.2 / min 92.3 / S2 80.3 | **24** | VMAF ~98.3 / p5 95.9–96.5 / S2 84.0–85.1 |

1080p/720p/sd **anime** NVENC CQ is unchanged — no animation sources in
this set. Do not copy the film −4/−6 step onto anime without measuring.

Caveat: WowGirls is 60 fps 4K at only ~21 Mb/s AVC. Uncapped CQ 22 wanted
~40–60 Mb/s on those windows (larger than the source). `transcode`'s 85%
source-bitrate cap **will bind** on files like these and will pull quality
below the uncapped scores. On a 40–80 Mb/s UHD remux the cap still should
not bind. That is a source-class difference, not a reason to raise CQ.

### Film CPU on the same WowGirls / MetArtX windows

Same refs as the NVENC audit above, `svt-av1-hdr` preset/tune from the
table, grain off.

| Tier | Old CRF (hdr) | Result | New CRF (hdr / mainline) | Result |
| --- | ---: | --- | ---: | --- |
| 2160p film | 26 | VMAF 97.3–99.7 / p5 ≥96.3 / S2 76–80. Lace slightly softer than NVENC CQ 22; CRF 20 only +2 S2 for +50% size | **26 / 19** (keep) | Lab Mutation remux A/B still stands; S2 lag here is diminishing returns vs a 21 Mb/s 60 fps AVC master |
| 1080p film | 31 | VMAF p5 ~93 / S2 ~75 — fail | **20 / 13** | p5 95.7–97.2 / S2 78.7–80.5 |
| 720p film | 33 | p5 ~92 / S2 ~75 — fail | **22 / 15** | p5 95.3–96.7 / S2 79.7–81.1 |
| sd film | 35 | p5 ~91 / S2 ~74 — fail | **24 / 17** | p5 95.2–96.6 / S2 79.8–81.2 |

Anime CPU CRF at 1080p/720p/sd is unchanged.

### Lowest-class webcam (VDH)

`/mnt/exos1/downloads/VDH` — 75 Video DownloadHelper portrait HLS
captures, all h264 8-bit, 0.69–8.28 Mb/s (median 1.40), 12.8 GiB /
16.3 h. Grain 0.006–0.008 on the five-file sample (nvenc-ok). Auto
class with today's height-based ceilings: lowest 46 / stream 23 /
webrip 6 / master 0.

Every file is portrait. `resolution_tier` keys off **height**, so
480×854 becomes 1080p and 1080×1920 becomes 2160p. That is how a
0.95 Mb/s 480-wide capture lands in `lowest` (below the 1080p 1.5 Mb/s
ceiling) — correct *content* class, wrong reason. Do **not** flip the
bitrate floors to `min(width,height)` without retuning: the SD master
floor of 4 Mb/s would promote two 396×858 @ 5.6 Mb/s captures to
`master`.

Five files × three 10 s windows, uncapped NVENC master / stream /
lowest CQ (plus CPU on the middle window). Full-frame `compare`.
Artifacts: `.cache/psammophis/compare/vdh-lowest/`.

| Stop | Mean VMAF / p5 / S2 (15 windows) | Encode vs source |
| --- | --- | ---: |
| master (CQ 22/24) | 97.54 / 95.87 / 82.00 | **2.01×** |
| stream (+8 CQ) | 96.08 / 94.19 / 76.81 | **0.95×** |
| lowest (+12 CQ) | 94.75 / 92.66 / 73.05 | **0.65×** |

Master is the only remux-bar stop and doubles the file. Stream is the
near-transparent-to-this-HLS stop that stays near source size. Lowest
is size-sane and close on stills, but Gebby w1 dropped to VMAF p5 90.6
/ S2 67 — a second generation of mush. On shaky 30 fps 1080×1920
(Biawak) even master only reaches p5 92.3; the source is the ceiling.

**Leave +12 in place.** If a VDH-like file should stay near-transparent
to its own HLS, override `--source-class stream` rather than treating
it as a remux. Full tables, stills, and the portrait-tier discussion:
`notes/source-class-audit.md`.

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
