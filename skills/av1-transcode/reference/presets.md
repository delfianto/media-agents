# Preset rationale and sourcing

The full preset table lives in `scripts/av1transcode/presets.py` as code (so
it's the thing that actually runs); this doc is the "why" behind each number,
for whoever tunes it next. Two independent, verified-on-this-machine facts
back every choice below: the exact AVOptions/CLI surface this system's
ffmpeg build (`n8.1.2`, `libsvtav1` via `SVT-AV1 4.1.0`, `av1_nvenc`) actually
exposes (`ffmpeg -h encoder=<name>`, `SvtAv1EncApp --help`), and this
machine's actual GPUs (`nvidia-smi -L`: an RTX 4080 at index 0, Ada
Lovelace, AV1 encode-capable; an RTX 3060 at index 1, Ampere, decode-only).

## CPU (libsvtav1)

- **preset 4-6**: the community-consensus efficiency/speed sweet spot across
  every guide consulted (ffmpeg.party, dvaupel's and mrintrepide's SVT-AV1
  gists, the JET anime-encoding guide). Presets 0-3 buy little extra quality
  for multiples of the encode time on a whole-movie/episode job; presets
  above 8 give up too much efficiency. 4K uses 4 (slower, since it's this
  library's highest-value archival tier); 720p/SD step up to 5-6 since lower
  resolutions need less per-frame work to hit the same perceptual quality.
- **CRF 20-28 baseline (SDR)**: lower (more bits) at higher resolution tiers,
  since 4K amplifies compression artifacts and these are usually the
  highest-value sources in the library. `HDR_QUALITY_BONUS` (2) subtracts
  further for PQ/HLG content — banding in smooth gradients is far more
  visible in HDR's extended range, confirmed by every HDR-focused guide
  found, not just this project's own judgment call.
- **tune**: `0` (VQ) for film, `1` (PSNR) for anime — deliberately opposite
  choices per profile, not a shared default. Community guides for live
  action (ffmpeg.party, both gists) default their example commands to VQ for
  better subjective texture/grain rendition. The JET anime-encoding guide
  explicitly found tune=0's psychovisual optimizer rings on flat-color edges
  in animation and recommends staying on tune=1 (SvtAv1EncApp's own default)
  for exactly that reason.
- **film-grain**: 8-10 for film (community range for "normal amount of film
  grain" is 8-15; this library's Blu-ray remuxes are rarely the noisiest end
  of that range, so 10 was picked over 15), 4 for anime (JET's figure — a
  mild dithering value against banding, not real grain synthesis, since flat
  digital animation has no grain to reproduce).
- **enable-variance-boost=1**: JET: "little to no performance cost when
  properly bitrate normalized," and it targets exactly AV1's known weak spot
  (low-contrast area detail loss). Applied to every preset.
- **scd=1** (scene change detection): applied unconditionally in
  `command.py` rather than threaded through every preset entry — keyframes
  land on actual cuts instead of only the fixed GOP interval, a plain
  efficiency win with no downside (ffmpeg.party).
- **HDR10 metadata**: ffmpeg's libsvtav1 wrapper does not forward AVFrame HDR
  side data into the AV1 bitstream on its own (`ffmpeg` trac #10355) — the
  `color-primaries`/`transfer-characteristics`/`matrix-coefficients`/
  `mastering-display`/`content-light` `svtav1-params` keys have to be set
  explicitly from what `probe.py` read off the source. Verified end-to-end
  against a real 4K remux in this library (Mission: Impossible - The Final
  Reckoning): the mastering-display/content-light/DOVI side data all
  round-tripped correctly into the AV1 output.
- **Dolby Vision**: `-dolbyvision` (default `auto`) is a real, working
  libsvtav1/ffmpeg feature — it reads the `DOVI RPU Buffer`/`DOVI Metadata`
  AVFrame side data ffmpeg's demuxer already attaches and re-injects it into
  the AV1 stream. No `libdovi` build dependency needed; this ffmpeg build
  doesn't have one and it still worked. Confirmed by a real encode: a 6s clip
  from the Mission: Impossible source (profile 8, HEVC) came out the other
  side as profile 10 (the AV1-native DV profile number — a correct
  container-appropriate remap, not a bug) with `rpu_present_flag=1` intact.
  `av1_nvenc`'s full AVOptions listing has no equivalent option at all, which
  is why a Dolby Vision source always forces the `cpu` backend regardless of
  `--backend` (see `run.choose_backend`).

## GPU (av1_nvenc)

- **preset p7, tune uhq**: the highest quality settings this SDK generation
  exposes short of lossless. Originally defaulted to `p6` on the unverified
  assumption that `p7` cost meaningfully more time for little gain (the
  general "higher preset number = slower" intuition, true for most
  encoders' preset ladders) — measured directly instead (see
  `reference/incidents.md`): on this machine's RTX 4080, `p6` and `p7` take
  identical wall-clock time, and `p7` comes out marginally *smaller* at the
  same `cq`. No reason not to use it. Only measured on this specific
  GPU/driver; worth re-measuring if this ever runs on different NVENC
  hardware rather than assuming the same holds.
- **rc vbr + cq + b:v 0**: the standard "quality-targeted VBR" NVENC recipe
  (constant-quality within VBR rate control, uncapped bitrate) rather than a
  fixed target bitrate — matches the CRF-style "quality first" goal instead
  of hitting a size target.
- **cq = svt_crf - 2 (roughly)**: NVENC is a hardware encoder and, bit for
  bit, less efficient than a well-tuned software encoder — pushing `cq` a
  few points lower (more bits) than the CRF that gets equivalent SVT-AV1
  quality is standard community guidance for exactly this reason.
- **multipass fullres**: two-pass full-resolution rate control, still fast
  on GPU silicon, for better bit allocation than single-pass.
- **spatial-aq/temporal-aq/aq-strength**: adaptive quantization, this SDK's
  closest equivalent to variance-boost's "spend more bits where detail is
  low-contrast" goal.
- **b_ref_mode middle, not "each"**: `each` is a real, documented
  `-b_ref_mode` value (`ffmpeg -h encoder=av1_nvenc` lists it), but the
  driver rejects it at encoder-open time for `av1_nvenc` specifically —
  confirmed directly against this machine's RTX 4080: `"Each B frame as
  reference is not supported"` -> `"No capable devices found"`. `middle`
  (every other B-frame as a reference) is the strongest mode that actually
  opens; this was caught by an end-to-end smoke test before it ever became
  the default in production, see `reference/incidents.md`.
- **GPU selection is capability-probed, not name-matched**: `gpu.py` doesn't
  hardcode "RTX 40-series and up support AV1 encode" — it hands each
  candidate GPU index a real, trivial `av1_nvenc` encode and checks whether
  ffmpeg accepts it. This machine is the reason why that matters: an RTX
  4080 (index 0, encode-capable) and an RTX 3060 (index 1, decode-only) are
  both present, and a name/architecture table would need constant upkeep as
  new generations ship. See `reference/incidents.md` for why the probe clip
  itself has to be at least ~192px per side, not the trivial 64x64 it
  started as.

## Audio (libopus)

- Bitrate-per-channel-count is a piecewise-linear interpolation anchored at
  four community-consensus "very high quality" figures: mono 64k, stereo
  128k, 5.1 320k, 7.1 450k. Deliberately generous rather than minimal —
  audio is a small fraction of total remux size next to 4K/1080p video, so
  there's little to gain from squeezing it further, and Opus at these
  bitrates is effectively transparent for every layout tested.
- `mapping_family` is left at ffmpeg's own default (`-1`): libopus already
  auto-selects channel mapping family 1 (the one with surround
  masking/LFE-bandwidth handling) for anything above stereo, so there was
  nothing to override.
- Every *matching-language* audio track gets transcoded (see "Language
  filtering" below) — full nuanced track-selection policy (dropping
  commentary tracks, SDH subtitles, trimming duplicate same-language tracks,
  anime-release detection) is still `media-library`'s job, not this skill's.
  Run that skill first for anything beyond a plain by-language keep/drop.

## Bitrate ceiling (both backends)

CRF/CQ target a *quality level*, not a *size ceiling*. That's fine for a
genuine Blu-ray remux (40-80+ Mbps 4K, wastefully high for its actual
complexity) but breaks down on an already-efficiently-encoded source: a
real case (see `reference/incidents.md`) was a 3840x2160 x264 file
deliberately bitrate-capped to 15 Mbps, where hitting this skill's quality
target legitimately needed *more* bits than the source used, producing an
output larger than the file the tool exists to shrink.

Every encode now caps output video bitrate to
`presets.MAX_BITRATE_FRACTION_OF_SOURCE` (default `0.85`) of the source's
own video bitrate (`presets.source_video_bitrate_bps` — prefers the video
stream's own tagged `bit_rate`, falls back to the container's overall
bit_rate, then to `size*8/duration`):

- **CPU (libsvtav1)**: `--mbr <kbps>` alongside `--crf` puts the encoder in
  "Capped CRF" mode — confirmed via its own startup log
  (`BRC mode ... capped CRF`) once `mbr` is set.
- **NVENC**: `-maxrate`/`-bufsize` (bufsize = 2x maxrate) alongside `-cq`,
  the standard "capped VBR" pattern — confirmed directly: uncapped `cq=22`
  on a real clip produced ~17.9 Mbps; adding `-maxrate 4M -bufsize 8M`
  brought it to ~4.4 Mbps.

This is a safety net, not a target: on a genuine high-bitrate remux source
the cap essentially never binds (CRF/CQ-driven output lands far under 85%
of 40-80 Mbps anyway), so there's no quality cost in the common case.
Override with `--max-bitrate-fraction` (or `AV1TRANSCODE_MAX_BITRATE_FRACTION`
in `.env`), or disable entirely with `--no-bitrate-cap` for pure CRF/CQ.

## Language filtering (both backends)

`langfilter.py` does plain by-language keep/drop filtering for audio and
subtitle tracks — deliberately simple (no commentary/SDH/anime-release
nuance, that's still `media-library`'s job), added because the common case
(keep the English track(s), drop the rest) is worth having directly in this
skill's default behavior rather than requiring a separate pass first.

- Default language is `eng` for both audio and subtitles
  (`--audio-lang`/`--subtitle-lang`, or `AV1TRANSCODE_AUDIO_LANG`/
  `AV1TRANSCODE_SUBTITLE_LANG` in `.env`); pass `all` to keep every track,
  no filtering.
- Audio has a safety net: if nothing matches the target language, every
  original audio track is kept instead of producing a silent file — the
  same "don't guess your way into no audio" principle as media-library's
  `track_policy` fallback. Subtitles have no such guarantee (and don't need
  one): a file with zero subtitle tracks is a completely normal outcome.
- The first kept audio/subtitle track gets an explicit `default` disposition
  flag (and every other kept track of that type gets explicitly cleared to
  `0`), so a player auto-selects the right track regardless of what the
  source's own (possibly stale) disposition flags said.
- Switching to explicit per-index `-map` (needed to filter at all) replaced
  the previous blanket `-map 0`, which also fixed a latent issue: a source
  with embedded cover art (a second, `attached_pic`-flagged "video" stream)
  would previously have had `-c:v` apply to *both* streams. Only
  `probed["video"]["index"]` (the real, non-cover-art video stream `probe.py`
  already identifies) is ever mapped as video now.

## Output location

Defaults to in-place (same swap-behind-a-backup model as
media-library's `apply`). `--output-dir` (or `AV1TRANSCODE_OUTPUT_DIR`)
writes the converted file to a separate directory instead, mirroring each
source file's path relative to `--root` — in that mode the original source
is never touched at all (no backup, no delete), since nothing about it
changed.
