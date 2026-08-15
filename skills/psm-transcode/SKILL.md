---
name: psm-transcode
description: Use when the user wants a remux-transparent (or near-transparent) AV1 encode from a high-quality HEVC Blu-ray/UHD remux, with Opus audio. Size reduction is a consequence of hitting that quality bar, not the goal — a tiny file that smears detail has failed. Handles 4K HDR/Dolby Vision, 1080p film, anime/cartoon, and older catalog sources; supports high-quality CPU/libsvtav1 and faster Ada-or-newer NVIDIA GPU/av1_nvenc paths with live progress. Auto mode measures grain/noise and routes heavily grainy sources to CPU, caps output bitrate below the source, supports a separate output directory, language filtering, and persistent .env settings. Triggers on "convert/re-encode to AV1", "shrink this remux", "transcode to AV1 and Opus", "use GPU/NVENC", or reducing media size while preserving quality. Use `psm-analyze` for a read-only decision preview and `psm-track-strip` when codecs should remain unchanged.
allowed-tools:
    - Bash
metadata:
    author: "transcode"
    focus: "AV1 (libsvtav1/av1_nvenc) + Opus; remux-transparent quality first"
---

# transcode

Re-encodes video to AV1 and every matching-language audio track to Opus (or stream-copies the audio untouched with `--audio-copy`, for shrinking the video alone). The success criterion is **transparent or near-transparent against the source you actually have** (a Blu-ray master, a webrip, a stream encode, or a lowest-rung recording — see source class below). File size is a consequence of that quality target, not a goal of its own — a 20× smaller encode that smears painted backgrounds or skin texture has failed the preset. Output bitrate is also capped to never exceed the source's own (a safety net for already-tight sources, not a quality knob). Unlike `track-strip` - which only ever stream-copies (remuxes track selection, never touches codecs) - this skill always re-encodes video, and is the slow/expensive/semi-destructive operation of the two. It does its own plain by-language audio/subtitle keep/drop (default `eng`, see "Language filtering and bitrate cap" below), but run `track-strip`'s `apply` first if a file also needs the nuanced stuff (duplicate-track trimming, commentary/SDH dropping) that skill owns.

The implementation lives at `src/psammophis/transcode/` inside the packaged application. It wraps `ffprobe`/`ffmpeg` plus `mkvpropedit` for accurate output track-statistics tags, and for Dolby Vision / HDR10+ on GPU also `nvencc` (rigaya NVEnc with libdovi). See [`notes/av1-encoding.md`](../../notes/av1-encoding.md) for the rationale behind the current encoder settings.

## Running commands

```bash
.agents/run.sh transcode <subcommand> [options]
```

Run from the media-library root; the launcher loads `.agents/.envrc` before uv starts.

1. **`probe`** - Read-only. Reports each file's resolution/dynamic range (SDR/HDR10/Dolby Vision)/audio layout, and exactly which preset and backend `run` would pick for it.
   ```bash
   .agents/run.sh transcode probe --path "Some Movie"
   .agents/run.sh transcode probe --path "Some Anime" --profile anime
   ```

2. **`list-presets`** - Prints the full built-in resolution x profile preset table (CPU and GPU settings for each).
   ```bash
   .agents/run.sh transcode list-presets
   ```

3. **`run`** - Re-encodes video to AV1 and audio to Opus (pass `--audio-copy` to stream-copy the kept audio tracks bit-exactly instead of re-encoding them - only the video is shrunk then). **Defaults to a dry run** that probes the file and prints the exact `ffmpeg` command it would run, touching nothing. Pass `--yes` to actually execute.
   ```bash
   # dry run (safe, default) - always do this first and read the printed command
   .agents/run.sh transcode run --path "Some Movie"

   # execute for real, one file/show at a time
   .agents/run.sh transcode run --path "Some Movie" --yes

   # anime/cartoon sources: --profile is never auto-detected, see below
   .agents/run.sh transcode run --path "Some Anime" --profile anime --yes

   # write converted files elsewhere instead of swapping in place -- flat,
   # under their own filename directly in --output-dir (not mirroring the
   # source's directory structure), and the source is never touched in
   # this mode (no backup/delete either)
   .agents/run.sh transcode run --path "Some Movie" --output-dir /converted --yes

   # --output-dir refuses to clobber a pre-existing file at the destination
   # unless told to
   .agents/run.sh transcode run --path "Some Movie" --output-dir /converted --overwrite-existing --yes

   # keep every audio/subtitle track instead of the single-best-eng-track default
   .agents/run.sh transcode run --path "Some Movie" --audio-lang all --subtitle-lang all --yes

   # keep the original audio untouched (bit-exact stream copy, no Opus re-encode)
   .agents/run.sh transcode run --path "Some Movie" --audio-copy --yes

   # force a specific poster instead of auto-detecting poster.jpg/cover.jpg next to the source
   .agents/run.sh transcode run --path "Some Movie" --cover-image /path/to/poster.jpg --yes

   # skip grain measurement (faster dry run), or override its cpu-preference threshold
   .agents/run.sh transcode run --path "Some Movie" --no-grain-routing
   .agents/run.sh transcode run --path "Some Movie" --grain-threshold 0.015
   ```
   Defaults: `--profile film`, `--backend auto` (GPU if an AV1-capable NVIDIA GPU is found, else CPU; Dolby Vision / HDR10+ on GPU use `nvencc` so dynamic metadata is kept, plain SDR/HDR10 use ffmpeg `av1_nvenc`), `--audio-lang eng` reduced to the single highest-quality matching track, `--subtitle-lang eng` preferring plain over SDH, output bitrate capped to 85% of the source's own bitrate, cover art auto-embedded if a poster/cover image is found next to the source (see "Language filtering, single audio track, and bitrate cap" and "Cover art" below) - every one of these is overridable per-invocation via CLI flag or persistently via `.env` (see "Configuration").

4. **`purge-backups`** - Once re-encoded files have been spot-checked for playback/quality, permanently deletes the backed-up originals.
   ```bash
   .agents/run.sh transcode purge-backups         # shows size, asks for --yes
   .agents/run.sh transcode purge-backups --yes    # actually deletes
   ```

## Configuration (`.env`, optional)

Everything is a CLI flag by default, but `--output-dir`, `--audio-lang`, `--subtitle-lang`, `--audio-copy`, and `--max-bitrate-fraction` can also be set persistently via a `.env` file (copy `.env.example`, or point `--env-file` at one elsewhere) so they don't need retyping every invocation - useful for an automated/scheduled `run`. Precedence: explicit CLI flag > real environment variable > `.env` file > built-in default. This skill needs no external credentials, unlike `organize`/`artwork` - every key here is a plain preference, so there's nothing required to fill in.

## Language filtering, single audio track, and bitrate cap

- **Audio defaults to the single highest-quality `eng` track** (`--audio-lang`, or `all` to keep every language unfiltered). Once language-filtered, `--all-audio-tracks` keeps every matching track instead of reducing to one. Keep tracks are re-encoded to Opus unless `--audio-copy` is set, in which case they are stream-copied bit-exactly (video-only shrink). Quality ranking is a plain codec-family tier (lossless -- TrueHD/FLAC/DTS-HD MA -- unconditionally outranks lossy -- E-AC3/AC3/AAC -- regardless of bitrate; ties within a tier break on channel count then bitrate), so a TrueHD Atmos track wins over a same-language E-AC3 "compatibility" copy of the same mix - a real pattern in remuxes that carry both (see `../../notes/av1-encoding.md`). A never-go-silent fallback still applies: if nothing matches `--audio-lang`, every original track becomes the fallback pool, and the single best of *that* is picked - never zero audio.
- **Subtitles default to `eng`, preferring plain over SDH** (`--subtitle-lang`, or `all`). Unlike audio, every matching plain (non-SDH) track is kept, not reduced to one - subtitles are cheap. SDH tracks for that language are only kept if there's no plain alternative at all; detected via the `hearing_impaired` disposition flag or an "SDH"/"hearing impaired"/"deaf" title, the same signals (deliberately simplified) as track-strip's `track_policy.is_sdh`.
- The first kept audio track and first kept subtitle track each get an explicit `default` disposition flag; any other kept subtitle tracks get it explicitly cleared.
- **Output video bitrate is capped to 85% of the source's own bitrate by default** (`--max-bitrate-fraction`, or `--no-bitrate-cap` to disable). CRF/CQ targets a *quality level*, not a *size ceiling* - fine for a wastefully-high-bitrate Blu-ray remux, but an already-efficiently-encoded source (e.g. a tightly bitrate-capped web encode) can legitimately need *more* bits than the source used to hit that same quality bar, producing an output larger than the file this tool exists to shrink. This actually happened - see `../../notes/track-filtering.md` - and the cap is the fix, not just a warning after the fact. On a genuine high-bitrate remux the cap essentially never binds.

## Cover art (auto-detected, optional)

`run` looks for a conventional poster/cover image (`poster.jpg`, `poster.png`, `cover.jpg`, `cover.png`, `folder.jpg`, `folder.png`, checked in that order) sitting next to the source file and, if found, embeds it as a proper Matroska attachment named `cover.jpg`/`cover.png` - not a disposition-flagged video stream, which is silently a no-op for MKV output (confirmed the hard way, see `../../notes/track-filtering.md`). This composes directly with `organize` and `artwork`: organize the movie, fetch `poster.jpg`, then `transcode` picks it up automatically with no TMDB integration or new dependency of its own - it only ever reads whatever's already on disk. `--cover-image PATH` forces a specific image instead of auto-detecting; `--no-cover-art` disables the lookup entirely. Nothing found (and no override) just means no cover gets embedded - never an error.

## Before running `--yes` against anything - always confirm scope with the user first

This is a lossy, one-way, hours-long operation on the user's real media, not a quick reversible remux - **always confirm scope with the user before `run --yes`** (which file(s)/show(s), which profile, which backend), same as track-strip's rule for `apply --yes` but more so given the cost of getting it wrong.

## Source class: `auto` (default), `master`, `webrip`, `stream`, or `lowest`

Resolution × film/anime is not enough. `auto` classifies each file from codec, profile, and bitrate:

- **master** -- Blu-ray remux or raw capture. Uses the calibrated CRF/CQ table.
- **webrip** -- decent commercial encode, already more compressed than a remux. Raises CRF/CQ so a 21 Mb/s 4K AVC is not treated as a 62 Mb/s remux.
- **stream** -- heavily streaming-optimized (Baseline profile or low bitrate). Raises CRF/CQ further and defaults the bitrate cap to 70%.
- **lowest** -- recordings, vintage films with dubious compression, legacy codecs (MPEG-2, DivX/Xvid, WMV). Raises CRF/CQ most and defaults the cap to 60%.

Override with `--source-class master|webrip|stream|lowest` when the heuristic is wrong. Probe/analyze print the class and reason on every file.

## Content profile: `film` (default), `anime`, `film-fast`, or `anime-fast` - never auto-detected

Resolution and HDR/Dolby Vision are objective facts `probe` reads straight off the file and always get this right automatically. Whether the video itself is live-action or hand-drawn/flat-shaded animation is not derivable that way, and this skill deliberately does not reuse track-strip's Japanese-audio-track heuristic to guess it: that heuristic answers "is this a Japanese-original release" for language *policy* purposes, and a Japanese-original live-action film (e.g. a Japanese Blu-ray with only a Japanese audio track) would be misclassified as animation by that signal. Pass `--profile anime` explicitly for animation/cartoon sources - ask the user if it isn't obvious from the title/folder.

`film-fast` and `anime-fast` are faster-CPU-preset siblings of `film`/`anime` (2160p-only so far - see `../../notes/av1-encoding.md`'s "Preset-speed investigation"), for batch jobs where wall-clock time matters more than the last fraction of quality. They're a straight substitute for the base profile, not a third axis - pick one of the four, don't combine.

## Backend: `cpu` (libsvtav1) or `nvenc` (GPU)

  - **`cpu`** - highest quality per byte, much slower (can be hours for a single 4K film even on a fast preset). Uses ffmpeg `libsvtav1` with explicit grain denoising/synthesis for grain-enabled presets and `-dolbyvision 1` when the source has Dolby Vision RPU. The shared detector distinguishes upstream SVT-AV1 from `juliobbv-p/svt-av1-hdr`, because the fork deliberately exposes the same encoder name but has a different CRF scale and extra quality controls. Probe/analyze/dry-run always report the implementation and selected implementation-specific CRF.
- **`nvenc`** - an order of magnitude faster, needs an Ada Lovelace (RTX 40-series) or newer NVIDIA GPU, and gives up a little efficiency versus `cpu` for the same visual quality (accounted for in the preset table's GPU `cq` values - see `../../notes/av1-encoding.md`). Implementation splits by metadata:
  - **SDR / static HDR10** - ffmpeg `av1_nvenc` (unchanged).
  - **Dolby Vision or HDR10+** - **`nvencc`** (rigaya NVEnc, package `nvenc` on Arch/Cachy) with `--dolby-vision-rpu copy` + AV1 DV profile `10.1`, and/or `--dhdr10-info copy`. Plain ffmpeg `av1_nvenc` cannot preserve RPU or HDR10+ dynamic SEI. If `nvencc` is not on PATH: auto falls back to `cpu` for DV; explicit `--backend nvenc` on a DV source errors rather than silently dropping metadata. Optional supporting tools: `dovi_tool` / `hdr10plus_tool` for extract/inspect (HEVC-oriented); inject into AV1 is done by nvencc, not those CLIs.
- **`auto`** (default) - `nvenc` if `gpu.detect_av1_nvenc_gpu()` finds a GPU that actually accepts a real `av1_nvenc` encode (capability-probed, not guessed from the GPU's name/generation - see `../../notes/track-filtering.md`), else `cpu`. DV/HDR10+ stay on GPU when `nvencc` is available. When a GPU is otherwise available, `auto` also measures the source's grain/noise level (a few short denoise-diff samples via `medialib.grain.measure_grain`) and prefers `cpu` over `nvenc` when it's at or above `--grain-threshold` (default: `medialib.grain.GRAIN_CPU_THRESHOLD`, currently `0.012` and explicitly provisional - see `../../notes/av1-encoding.md`'s "Grain-aware backend routing" section) - `libsvtav1`'s film-grain synthesis handles real per-pixel noise far more efficiently than `nvenc`'s AQ settings can. Grain can only ever push the decision from `nvenc` to `cpu`, never the reverse. Pass `--no-grain-routing` to skip measurement entirely and revert to the pre-grain nvenc-whenever-available default.
- **`nvenc-degrain`** - explicit-opt-in only, **never** chosen by `auto`. An alternative to sending grainy sources to `cpu`: denoise the source, encode the clean picture with `av1_nvenc`, then reinject a real diff-derived grain table via the third-party [`grav1synth`](https://github.com/rust-av/grav1synth) tool (patched locally, see `../../vendor-patches/grav1synth/README.md`) - nvenc speed with closer-to-cpu grain efficiency. Runs as five internal phases (`denoise`, `nvenc-encode`, `grain-diff`, `grain-apply`, `mux`), each visible in progress/phase reporting same as any other run. **SDR/HDR10 only** - refuses Dolby Vision and HDR10+ sources outright (`degrain.check_supported` raises rather than attempting unvalidated dynamic-metadata handling). Needs `grav1synth` on `PATH`; `env-check` reports it as an optional `transcode`-category prerequisite. Costs real temporary disk space - the denoise step writes a lossless FFV1 intermediate roughly source-sized, cleaned up automatically when the run finishes (success or failure). See `../../notes/av1-encoding.md`'s "Denoise + nvenc + grav1synth" section for the full investigation, the two real bugs it found and fixed in `grav1synth` itself, and why it's scoped this narrowly for its first release.

## Monitoring a running encode

Interactive runs render a Rich progress bar on stderr. Redirected human runs use newline-delimited plain progress, while `--reporter jsonl` emits versioned structured events for automation; command results remain on stdout. The full encoder feed is also written live to `<root>/.cache/transcode/logs/<relative-path>.log`, so `tail -f` remains a diagnostic fallback. Keep observing the original foreground process when possible, or use `runs show/events` against its durable run ID; quiet intervals are not a reason to launch the encode again.

## Safety model

- `run` without `--yes` never writes anything, even though it does a live probe of every targeted file - safe to run as often as needed to check what would happen.
- When executing: encodes to a temp file next to the original first, recalculates Matroska per-track BPS/duration/frame/byte statistics with `mkvpropedit`, then verifies the result (has video, has audio, duration matches within 2%, no copied source-codec bitrate/byte counters on transcoded streams -- the audio counters check stands down when `--audio-copy` is set, since a copied track legitimately carries its source's exact bytes, a head-only decode spot check, and - if the source was HDR - that the output is still tagged HDR and still carries mastering-display metadata if the source did; if the source had Dolby Vision or HDR10+, that dynamic metadata must still be present on the output) *before* the original is touched. The success report gives measured before/after sizes and overall bitrates; a file that ends up larger than its source gets flagged with a warning rather than silently accepted.
- Originals are staged safely at `.cache/transcode/originals/<relative path>` before the verified replacement is installed; same-filesystem backups use a hard link and cross-filesystem backups use a complete, fsynced copy. The source path remains live until the atomic commit. Use `purge-backups` once re-encoded files are confirmed good. Pass `--no-backup` to replace originals without retaining that recovery copy (verification still gates it). None of this applies when `--output-dir` is set - the source is never touched at all in that mode, so there's nothing to back up.
- Output is always `.mkv` regardless of the source container (AV1 in MP4 works too, but MKV is this library's convention and handles font attachments/multiple subtitle tracks better) - a `.mp4` source ends up backed up under its original extension while the new file takes over the same directory (or lands flat in `--output-dir`, under its own filename only - not mirroring the source's directory structure) under a `.mkv` name.
- **`--output-dir` writes flat, not mirrored**: the converted file lands at `<output-dir>/<source filename>.mkv`, regardless of how deep the source sits under `--root`. A pre-existing file at that destination is left alone and reported as an error unless `--overwrite-existing` is passed. If `--output-dir` (or a custom `--backup-dir`/`--log-dir`) resolves to a plain subdirectory of `--root` itself, it's excluded from the file walk so a freshly-written output can never be rediscovered as a new source and re-encoded on top of itself in the same invocation (see `../../notes/track-filtering.md`).
- Audio/subtitle tracks matching `--audio-lang`/`--subtitle-lang` (default `eng`) are kept and re-encoded/copied; attachments (fonts) are always kept regardless. This is a plain by-language filter, not the nuanced policy (commentary/SDH/anime-release detection) `track-strip` owns - run that skill first for anything beyond "just keep English". Pass `--no-subtitles` to drop every subtitle track regardless of language (e.g. if a source subtitle codec can't mux into Matroska).
- Sequential by design, same as track-strip's `apply`/`transcode` - no `--jobs` flag. An encode is CPU/GPU-bound rather than disk-bound the way a stream-copy remux is, but running two at once on the same GPU or the same set of CPU cores would just make both slower, not faster.

## Extending

`src/psammophis/transcode/` is small and modular, and shares its decision-making layer with the `analyze` skill via `psammophis.medialib` rather than owning it exclusively: `medialib.videoprobe` (ffprobe wrapper, including HDR/Dolby Vision/HDR10+ side-data extraction, per-track profile/title/hearing_impaired fields, and attachment counting), `medialib.colorinfo` (pure HDR metadata parsing/formatting, CICP lookup tables, `needs_dynamic_metadata_path`), `medialib.svt` (upstream-versus-HDR-fork ABI detection), `medialib.av1_presets` (the master-calibrated resolution x profile table), `medialib.sourceclass` (master/webrip/stream/lowest detection and CRF/CQ deltas), `medialib.grain` (the denoise-diff grain/noise measurement, `GRAIN_CPU_THRESHOLD`), `medialib.av1_backend` (`choose_backend`/`choose_encode_engine`/`grain_routing_applies`/`nvencc_available`) all moved out of this skill's own package once `analyze` needed the exact same decisions to report rather than reimplement (see root `AGENTS.md`'s `psammophis.medialib` section). What's left in `src/psammophis/transcode/` itself is genuinely CLI/execution-specific: `langfilter.py` (pure by-language audio/subtitle keep/drop, codec-quality ranking for picking the single best audio track, SDH detection, and the audio never-go-silent fallback), `config.py` (optional `.env` loading, mirroring `organize`'s pattern), `command.py` (builds the actual `ffmpeg` argv for libsvtav1/av1_nvenc - explicit per-stream `-map`, language/quality filtering, disposition flags, bitrate cap, cover-art attachment; its nvenc/audio/disposition/metadata argument builders are public, not underscore-prefixed, specifically so `degrain.py` can reuse them rather than duplicate), `nvencc_cmd.py` (builds `nvencc` argv for GPU + Dolby Vision/HDR10+), `degrain.py` (the `nvenc-degrain` backend's five-step pipeline - pure command builders for each step plus `run_pipeline`'s orchestration, kept deliberately separate from `av1_backend.py`'s shared cpu/nvenc decision logic so it can never be chosen implicitly), `run.py` (per-file orchestration: grain measurement, backend/engine selection, live-streamed and persisted logging, verify, backup-or-mirror-to-output-dir, swap, sidecar cover detection - the same shape as track-strip's `apply.py`'s `_execute_backend_plan`, sized for a job that runs hours rather than seconds), `cli.py` (argparse subcommands, CLI-flag/`.env`/default precedence via `_resolve()`). `tests/medialib/test_av1_presets.py`, `tests/medialib/test_svt.py`, `tests/medialib/test_colorinfo.py`, `tests/medialib/test_grain.py`, `tests/medialib/test_av1_backend.py`, plus this skill's own `tests/transcode/test_langfilter.py` and `tests/transcode/test_degrain.py` (and siblings) cover the pure logic - run them (see `AGENTS.md` at the repo root for the lint/type-check/test commands, and note the test file naming gotcha it documents - test file basenames must be unique repo-wide, not just per skill) after touching any of those modules. Any change to `av1_presets.py`'s numbers or encoder argument lists should be smoke-tested against a real short clip (see `../../notes/track-filtering.md` for why - most of its entries are bugs that only a real encoder invocation against real hardware/files surfaced, not something code review or the unit tests alone would have caught) before being trusted as a new default.

## Progress for long-running applied work

For multi-hour encodes or large batch mutations, prefer JSONL progress so the
session can be observed without ANSI noise:

```bash
.agents/run.sh --reporter jsonl --progress-interval 30 transcode run ... --yes
```

- Continue observing the **same** process/session; do not relaunch an encode
  merely because an interval produced no output (heartbeats and journals exist
  for this).
- Accept success only when both `run.completed.status` and the process exit code
  indicate success. Encoder percent reaching 100% is **not** success — verification
  and commit still follow.
- Inspect durable status without the original terminal:

```bash
.agents/run.sh runs list --active --json
.agents/run.sh runs show <run-id> --json
.agents/run.sh runs events <run-id> --after <seq>
```
