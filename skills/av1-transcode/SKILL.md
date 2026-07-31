---
name: av1-transcode
description: Use when the user wants to shrink a Blu-ray remux or other large movie/show by re-encoding video to AV1 and audio to Opus while preserving quality. Handles 4K HDR/Dolby Vision, 1080p film, anime/cartoon, and older catalog sources; supports high-quality CPU/libsvtav1 and faster Ada-or-newer NVIDIA GPU/av1_nvenc paths with live progress. Auto mode measures grain/noise and routes heavily grainy sources to CPU, caps output bitrate below the source, supports a separate output directory, language filtering, and persistent .env settings. Triggers on "convert/re-encode to AV1", "shrink this remux", "transcode to AV1 and Opus", "use GPU/NVENC", or reducing media size while preserving quality. Use `analyze` for a read-only decision preview and `track-strip` when codecs should remain unchanged.
allowed-tools:
    - Bash
metadata:
    author: "av1-transcode"
    focus: "AV1 (libsvtav1/av1_nvenc) + Opus re-encoding for max quality per byte"
---

# av1-transcode

Re-encodes video to AV1 and every matching-language audio track to Opus, for shrinking ultra-high-bitrate Blu-ray remuxes without giving up much (ideally any) visible quality, output bitrate capped to never exceed the source's own. Unlike `track-strip` - which only ever stream-copies (remuxes track selection, never touches codecs) - this skill always re-encodes video, and is the slow/expensive/semi-destructive operation of the two. It does its own plain by-language audio/subtitle keep/drop (default `eng`, see "Language filtering and bitrate cap" below), but run `track-strip`'s `apply` first if a file also needs the nuanced stuff (duplicate-track trimming, commentary/SDH dropping) that skill owns.

The toolkit lives at `scripts/av1transcode/` (next to this file), a zero-third-party-dependency Python package wrapping `ffprobe`/`ffmpeg` plus `mkvpropedit` for accurate output track-statistics tags, and for Dolby Vision / HDR10+ on GPU also `nvencc` (rigaya NVEnc with libdovi). See `reference/presets.md` for the full rationale (and citations) behind every encoder setting, and `reference/incidents.md` for real bugs a smoke test against actual files in this library caught before they ever became defaults - read both before changing `presets.py`, `command.py`, `nvencc_cmd.py`, or `gpu.py`.

## Running commands

```bash
.agents/scripts/run-skill av1-transcode <subcommand> [options]
```

Run from the media-library root; the launcher loads `.agents/.envrc` before uv starts.

1. **`probe`** - Read-only. Reports each file's resolution/dynamic range (SDR/HDR10/Dolby Vision)/audio layout, and exactly which preset and backend `run` would pick for it.
   ```bash
   .agents/scripts/run-skill av1-transcode probe --path "Some Movie"
   .agents/scripts/run-skill av1-transcode probe --path "Some Anime" --profile anime
   ```

2. **`list-presets`** - Prints the full built-in resolution x profile preset table (CPU and GPU settings for each).
   ```bash
   .agents/scripts/run-skill av1-transcode list-presets
   ```

3. **`run`** - Re-encodes video to AV1 and audio to Opus. **Defaults to a dry run** that probes the file and prints the exact `ffmpeg` command it would run, touching nothing. Pass `--yes` to actually execute.
   ```bash
   # dry run (safe, default) - always do this first and read the printed command
   .agents/scripts/run-skill av1-transcode run --path "Some Movie"

   # execute for real, one file/show at a time
   .agents/scripts/run-skill av1-transcode run --path "Some Movie" --yes

   # anime/cartoon sources: --profile is never auto-detected, see below
   .agents/scripts/run-skill av1-transcode run --path "Some Anime" --profile anime --yes

   # write converted files elsewhere instead of swapping in place -- flat,
   # under their own filename directly in --output-dir (not mirroring the
   # source's directory structure), and the source is never touched in
   # this mode (no backup/delete either)
   .agents/scripts/run-skill av1-transcode run --path "Some Movie" --output-dir /converted --yes

   # --output-dir refuses to clobber a pre-existing file at the destination
   # unless told to
   .agents/scripts/run-skill av1-transcode run --path "Some Movie" --output-dir /converted --overwrite-existing --yes

   # keep every audio/subtitle track instead of the single-best-eng-track default
   .agents/scripts/run-skill av1-transcode run --path "Some Movie" --audio-lang all --subtitle-lang all --yes

   # force a specific poster instead of auto-detecting poster.jpg/cover.jpg next to the source
   .agents/scripts/run-skill av1-transcode run --path "Some Movie" --cover-image /path/to/poster.jpg --yes

   # skip grain measurement (faster dry run), or override its cpu-preference threshold
   .agents/scripts/run-skill av1-transcode run --path "Some Movie" --no-grain-routing
   .agents/scripts/run-skill av1-transcode run --path "Some Movie" --grain-threshold 0.015
   ```
   Defaults: `--profile film`, `--backend auto` (GPU if an AV1-capable NVIDIA GPU is found, else CPU; Dolby Vision / HDR10+ on GPU use `nvencc` so dynamic metadata is kept, plain SDR/HDR10 use ffmpeg `av1_nvenc`), `--audio-lang eng` reduced to the single highest-quality matching track, `--subtitle-lang eng` preferring plain over SDH, output bitrate capped to 85% of the source's own bitrate, cover art auto-embedded if a poster/cover image is found next to the source (see "Language filtering, single audio track, and bitrate cap" and "Cover art" below) - every one of these is overridable per-invocation via CLI flag or persistently via `.env` (see "Configuration").

4. **`purge-backups`** - Once re-encoded files have been spot-checked for playback/quality, permanently deletes the backed-up originals.
   ```bash
   .agents/scripts/run-skill av1-transcode purge-backups         # shows size, asks for --yes
   .agents/scripts/run-skill av1-transcode purge-backups --yes    # actually deletes
   ```

## Configuration (`.env`, optional)

Everything is a CLI flag by default, but `--output-dir`, `--audio-lang`, `--subtitle-lang`, and `--max-bitrate-fraction` can also be set persistently via a `.env` file (copy `.env.example`, or point `--env-file` at one elsewhere) so they don't need retyping every invocation - useful for an automated/scheduled `run`. Precedence: explicit CLI flag > real environment variable > `.env` file > built-in default. This skill needs no external credentials, unlike `organize`/`artwork` - every key here is a plain preference, so there's nothing required to fill in.

## Language filtering, single audio track, and bitrate cap

- **Audio defaults to the single highest-quality `eng` track** (`--audio-lang`, or `all` to keep every language unfiltered). Once language-filtered, `--all-audio-tracks` keeps every matching track instead of reducing to one. Quality ranking is a plain codec-family tier (lossless -- TrueHD/FLAC/DTS-HD MA -- unconditionally outranks lossy -- E-AC3/AC3/AAC -- regardless of bitrate; ties within a tier break on channel count then bitrate), so a TrueHD Atmos track wins over a same-language E-AC3 "compatibility" copy of the same mix - a real pattern in remuxes that carry both (see `reference/presets.md`). A never-go-silent fallback still applies: if nothing matches `--audio-lang`, every original track becomes the fallback pool, and the single best of *that* is picked - never zero audio.
- **Subtitles default to `eng`, preferring plain over SDH** (`--subtitle-lang`, or `all`). Unlike audio, every matching plain (non-SDH) track is kept, not reduced to one - subtitles are cheap. SDH tracks for that language are only kept if there's no plain alternative at all; detected via the `hearing_impaired` disposition flag or an "SDH"/"hearing impaired"/"deaf" title, the same signals (deliberately simplified) as track-strip's `track_policy.is_sdh`.
- The first kept audio track and first kept subtitle track each get an explicit `default` disposition flag; any other kept subtitle tracks get it explicitly cleared.
- **Output video bitrate is capped to 85% of the source's own bitrate by default** (`--max-bitrate-fraction`, or `--no-bitrate-cap` to disable). CRF/CQ targets a *quality level*, not a *size ceiling* - fine for a wastefully-high-bitrate Blu-ray remux, but an already-efficiently-encoded source (e.g. a tightly bitrate-capped web encode) can legitimately need *more* bits than the source used to hit that same quality bar, producing an output larger than the file this tool exists to shrink. This actually happened - see `reference/incidents.md` - and the cap is the fix, not just a warning after the fact. On a genuine high-bitrate remux the cap essentially never binds.

## Cover art (auto-detected, optional)

`run` looks for a conventional poster/cover image (`poster.jpg`, `poster.png`, `cover.jpg`, `cover.png`, `folder.jpg`, `folder.png`, checked in that order) sitting next to the source file and, if found, embeds it as a proper Matroska attachment named `cover.jpg`/`cover.png` - not a disposition-flagged video stream, which is silently a no-op for MKV output (confirmed the hard way, see `reference/incidents.md`). This composes directly with `organize` and `artwork`: organize the movie, fetch `poster.jpg`, then `av1-transcode` picks it up automatically with no TMDB integration or new dependency of its own - it only ever reads whatever's already on disk. `--cover-image PATH` forces a specific image instead of auto-detecting; `--no-cover-art` disables the lookup entirely. Nothing found (and no override) just means no cover gets embedded - never an error.

## Before running `--yes` against anything - always confirm scope with the user first

This is a lossy, one-way, hours-long operation on the user's real media, not a quick reversible remux - **always confirm scope with the user before `run --yes`** (which file(s)/show(s), which profile, which backend), same as track-strip's rule for `apply --yes` but more so given the cost of getting it wrong.

## Content profile: `film` (default) or `anime` - never auto-detected

Resolution and HDR/Dolby Vision are objective facts `probe` reads straight off the file and always get this right automatically. Whether the video itself is live-action or hand-drawn/flat-shaded animation is not derivable that way, and this skill deliberately does not reuse track-strip's Japanese-audio-track heuristic to guess it: that heuristic answers "is this a Japanese-original release" for language *policy* purposes, and a Japanese-original live-action film (e.g. a Japanese Blu-ray with only a Japanese audio track) would be misclassified as animation by that signal. Pass `--profile anime` explicitly for animation/cartoon sources - ask the user if it isn't obvious from the title/folder.

## Backend: `cpu` (libsvtav1) or `nvenc` (GPU)

  - **`cpu`** - highest quality per byte, much slower (can be hours for a single 4K film even on a fast preset). Uses ffmpeg `libsvtav1` with explicit grain denoising/synthesis for grain-enabled presets and `-dolbyvision 1` when the source has Dolby Vision RPU. The shared detector distinguishes upstream SVT-AV1 from `juliobbv-p/svt-av1-hdr`, because the fork deliberately exposes the same encoder name but has a different CRF scale and extra quality controls. Probe/analyze/dry-run always report the implementation and selected implementation-specific CRF.
- **`nvenc`** - an order of magnitude faster, needs an Ada Lovelace (RTX 40-series) or newer NVIDIA GPU, and gives up a little efficiency versus `cpu` for the same visual quality (accounted for in the preset table's GPU `cq` values - see `reference/presets.md`). Implementation splits by metadata:
  - **SDR / static HDR10** - ffmpeg `av1_nvenc` (unchanged).
  - **Dolby Vision or HDR10+** - **`nvencc`** (rigaya NVEnc, package `nvenc` on Arch/Cachy) with `--dolby-vision-rpu copy` + AV1 DV profile `10.1`, and/or `--dhdr10-info copy`. Plain ffmpeg `av1_nvenc` cannot preserve RPU or HDR10+ dynamic SEI. If `nvencc` is not on PATH: auto falls back to `cpu` for DV; explicit `--backend nvenc` on a DV source errors rather than silently dropping metadata. Optional supporting tools: `dovi_tool` / `hdr10plus_tool` for extract/inspect (HEVC-oriented); inject into AV1 is done by nvencc, not those CLIs.
- **`auto`** (default) - `nvenc` if `gpu.detect_av1_nvenc_gpu()` finds a GPU that actually accepts a real `av1_nvenc` encode (capability-probed, not guessed from the GPU's name/generation - see `reference/incidents.md`), else `cpu`. DV/HDR10+ stay on GPU when `nvencc` is available. When a GPU is otherwise available, `auto` also measures the source's grain/noise level (a few short denoise-diff samples via `medialib.grain.measure_grain`) and prefers `cpu` over `nvenc` when it's at or above `--grain-threshold` (default: `medialib.grain.GRAIN_CPU_THRESHOLD`, currently `0.012` and explicitly provisional - see `reference/presets.md`'s "Grain-aware backend routing" section) - `libsvtav1`'s film-grain synthesis handles real per-pixel noise far more efficiently than `nvenc`'s AQ settings can. Grain can only ever push the decision from `nvenc` to `cpu`, never the reverse. Pass `--no-grain-routing` to skip measurement entirely and revert to the pre-grain nvenc-whenever-available default.

## Monitoring a running encode

A `run --yes` invocation prints a throttled progress line (at most once every ~10s) to stdout as it goes, but the real, full-fidelity feed is the per-file log path it prints at startup (`<root>/.cache/av1transcode/logs/<relative-path>.log` by default) - every line ffmpeg produces is written there in real time as it's produced, so `tail -f` on that path gives a genuinely live view regardless of how `run` itself was invoked (foreground, backgrounded, or from a fresh session checking on an encode started earlier). For a long CPU encode, start `run --yes` in the background and check that log path periodically rather than blocking on it.

## Safety model

- `run` without `--yes` never writes anything, even though it does a live probe of every targeted file - safe to run as often as needed to check what would happen.
- When executing: encodes to a temp file next to the original first, recalculates Matroska per-track BPS/duration/frame/byte statistics with `mkvpropedit`, then verifies the result (has video, has audio, duration matches within 2%, no copied source-codec bitrate/byte counters on transcoded streams, a head-only decode spot check, and - if the source was HDR - that the output is still tagged HDR and still carries mastering-display metadata if the source did; if the source had Dolby Vision or HDR10+, that dynamic metadata must still be present on the output) *before* the original is touched. The success report gives measured before/after sizes and overall bitrates; a file that ends up larger than its source gets flagged with a warning rather than silently accepted.
- Originals are moved (not deleted) to `.cache/av1transcode/originals/<relative path>` by default, mirroring track-strip's convention. Use `purge-backups` once re-encoded files are confirmed good. Pass `--no-backup` to delete originals immediately instead (verification still gates it). None of this applies when `--output-dir` is set - the source is never touched at all in that mode, so there's nothing to back up.
- Output is always `.mkv` regardless of the source container (AV1 in MP4 works too, but MKV is this library's convention and handles font attachments/multiple subtitle tracks better) - a `.mp4` source ends up backed up under its original extension while the new file takes over the same directory (or lands flat in `--output-dir`, under its own filename only - not mirroring the source's directory structure) under a `.mkv` name.
- **`--output-dir` writes flat, not mirrored**: the converted file lands at `<output-dir>/<source filename>.mkv`, regardless of how deep the source sits under `--root`. A pre-existing file at that destination is left alone and reported as an error unless `--overwrite-existing` is passed. If `--output-dir` (or a custom `--backup-dir`/`--log-dir`) resolves to a plain subdirectory of `--root` itself, it's excluded from the file walk so a freshly-written output can never be rediscovered as a new source and re-encoded on top of itself in the same invocation (see `reference/incidents.md`).
- Audio/subtitle tracks matching `--audio-lang`/`--subtitle-lang` (default `eng`) are kept and re-encoded/copied; attachments (fonts) are always kept regardless. This is a plain by-language filter, not the nuanced policy (commentary/SDH/anime-release detection) `track-strip` owns - run that skill first for anything beyond "just keep English". Pass `--no-subtitles` to drop every subtitle track regardless of language (e.g. if a source subtitle codec can't mux into Matroska).
- Sequential by design, same as track-strip's `apply`/`transcode` - no `--jobs` flag. An encode is CPU/GPU-bound rather than disk-bound the way a stream-copy remux is, but running two at once on the same GPU or the same set of CPU cores would just make both slower, not faster.

## Extending

`scripts/av1transcode/` is small and modular, and shares its decision-making layer with the `analyze` skill via `lib/medialib` rather than owning it exclusively: `medialib.videoprobe` (ffprobe wrapper, including HDR/Dolby Vision/HDR10+ side-data extraction, per-track profile/title/hearing_impaired fields, and attachment counting), `medialib.colorinfo` (pure HDR metadata parsing/formatting, CICP lookup tables, `needs_dynamic_metadata_path`), `medialib.svt` (upstream-versus-HDR-fork ABI detection), `medialib.av1_presets` (the resolution x profile and implementation-specific preset table plus lookup/bitrate helpers), `medialib.grain` (the denoise-diff grain/noise measurement, `GRAIN_CPU_THRESHOLD`), `medialib.av1_backend` (`choose_backend`/`choose_encode_engine`/`grain_routing_applies`/`nvencc_available`) all moved out of this skill's own package once `analyze` needed the exact same decisions to report rather than reimplement (see root `AGENTS.md`'s `lib/medialib` section). What's left in `scripts/av1transcode/` itself is genuinely CLI/execution-specific: `langfilter.py` (pure by-language audio/subtitle keep/drop, codec-quality ranking for picking the single best audio track, SDH detection, and the audio never-go-silent fallback), `config.py` (optional `.env` loading, mirroring `organize`'s pattern), `command.py` (builds the actual `ffmpeg` argv for libsvtav1/av1_nvenc - explicit per-stream `-map`, language/quality filtering, disposition flags, bitrate cap, cover-art attachment), `nvencc_cmd.py` (builds `nvencc` argv for GPU + Dolby Vision/HDR10+), `run.py` (per-file orchestration: grain measurement, backend/engine selection, live-streamed and persisted logging, verify, backup-or-mirror-to-output-dir, swap, sidecar cover detection - the same shape as track-strip's `apply.py`'s `_execute_backend_plan`, sized for a job that runs hours rather than seconds), `cli.py` (argparse subcommands, CLI-flag/`.env`/default precedence via `_resolve()`). `lib/test_av1_presets.py`, `lib/test_svt.py`, `lib/test_colorinfo.py`, `lib/test_grain.py`, `lib/test_av1_backend.py`, plus this skill's own `test_langfilter.py`, `test_av1transcode_config.py`, `test_command.py`, and `test_nvencc_cmd.py` cover the pure logic - run them (see `AGENTS.md` at the repo root for the lint/type-check/test commands, and note the test file naming gotcha it documents - test file basenames must be unique repo-wide, not just per skill) after touching any of those modules. Any change to `av1_presets.py`'s numbers or encoder argument lists should be smoke-tested against a real short clip (see `reference/incidents.md` for why - most of its entries are bugs that only a real encoder invocation against real hardware/files surfaced, not something code review or the unit tests alone would have caught) before being trusted as a new default.
