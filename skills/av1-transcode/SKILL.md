---
name: av1-transcode
version: 0.1.0
description: Use when the user wants to shrink ultra-high-bitrate Blu-ray remuxes in this Plex library by re-encoding video to AV1 and audio to Opus, while keeping quality as close to the source as possible - for 4K HDR/Dolby Vision remuxes, 1080p film, anime/cartoon sources, or older catalog titles. Supports both a CPU path (libsvtav1, the highest-quality option, slow) and a GPU path (av1_nvenc, much faster, needs an Ada Lovelace or newer NVIDIA GPU) with live progress monitoring during the encode. Output bitrate is capped to a fraction of the source's own bitrate so it can never end up larger than the original. Supports a separate --output-dir instead of converting in place, default-eng audio/subtitle language filtering (overridable per-track-language or disabled), and optional persistent .env configuration for all of the above. Triggers on phrases like "convert to AV1", "re-encode to AV1", "shrink this remux", "transcode to AV1 and Opus", "use the GPU/NVENC to encode", or any request to reduce the file size of a movie/show while preserving quality (as opposed to media-library's track stripping, which changes nothing about codecs).
allowed-tools:
    - Bash
metadata:
    author: "av1-transcode"
    focus: "AV1 (libsvtav1/av1_nvenc) + Opus re-encoding for max quality per byte"
---

# av1-transcode

Re-encodes video to AV1 and every matching-language audio track to Opus, for shrinking ultra-high-bitrate Blu-ray remuxes without giving up much (ideally any) visible quality, output bitrate capped to never exceed the source's own. Unlike `media-library` - which only ever stream-copies (remuxes track selection, never touches codecs) - this skill always re-encodes video, and is the slow/expensive/semi-destructive operation of the two. It does its own plain by-language audio/subtitle keep/drop (default `eng`, see "Language filtering and bitrate cap" below), but run `media-library`'s `apply` first if a file also needs the nuanced stuff (duplicate-track trimming, commentary/SDH dropping) that skill owns.

The toolkit lives at `scripts/av1transcode.py` (next to this file), a zero-third-party-dependency Python package wrapping `ffprobe`/`ffmpeg`, and for Dolby Vision / HDR10+ on GPU also `nvencc` (rigaya NVEnc with libdovi). See `reference/presets.md` for the full rationale (and citations) behind every encoder setting, and `reference/incidents.md` for real bugs a smoke test against actual files in this library caught before they ever became defaults - read both before changing `presets.py`, `command.py`, `nvencc_cmd.py`, or `gpu.py`.

## Running commands

```bash
python3 <path-to-this-skill>/scripts/av1transcode.py <subcommand> [options]
```

1. **`probe`** - Read-only. Reports each file's resolution/dynamic range (SDR/HDR10/Dolby Vision)/audio layout, and exactly which preset and backend `run` would pick for it.
   ```bash
   python3 scripts/av1transcode.py probe --path "Some Movie"
   python3 scripts/av1transcode.py probe --path "Some Anime" --profile anime
   ```

2. **`list-presets`** - Prints the full built-in resolution x profile preset table (CPU and GPU settings for each).
   ```bash
   python3 scripts/av1transcode.py list-presets
   ```

3. **`run`** - Re-encodes video to AV1 and audio to Opus. **Defaults to a dry run** that probes the file and prints the exact `ffmpeg` command it would run, touching nothing. Pass `--yes` to actually execute.
   ```bash
   # dry run (safe, default) - always do this first and read the printed command
   python3 scripts/av1transcode.py run --path "Some Movie"

   # execute for real, one file/show at a time
   python3 scripts/av1transcode.py run --path "Some Movie" --yes

   # anime/cartoon sources: --profile is never auto-detected, see below
   python3 scripts/av1transcode.py run --path "Some Anime" --profile anime --yes

   # write converted files elsewhere instead of swapping in place -- flat,
   # under their own filename directly in --output-dir (not mirroring the
   # source's directory structure), and the source is never touched in
   # this mode (no backup/delete either)
   python3 scripts/av1transcode.py run --path "Some Movie" --output-dir /converted --yes

   # --output-dir refuses to clobber a pre-existing file at the destination
   # unless told to
   python3 scripts/av1transcode.py run --path "Some Movie" --output-dir /converted --overwrite-existing --yes

   # keep every audio/subtitle track instead of the single-best-eng-track default
   python3 scripts/av1transcode.py run --path "Some Movie" --audio-lang all --subtitle-lang all --yes

   # force a specific poster instead of auto-detecting poster.jpg/cover.jpg next to the source
   python3 scripts/av1transcode.py run --path "Some Movie" --cover-image /path/to/poster.jpg --yes
   ```
   Defaults: `--profile film`, `--backend auto` (GPU if an AV1-capable NVIDIA GPU is found, else CPU; Dolby Vision / HDR10+ on GPU use `nvencc` so dynamic metadata is kept, plain SDR/HDR10 use ffmpeg `av1_nvenc`), `--audio-lang eng` reduced to the single highest-quality matching track, `--subtitle-lang eng` preferring plain over SDH, output bitrate capped to 85% of the source's own bitrate, cover art auto-embedded if a poster/cover image is found next to the source (see "Language filtering, single audio track, and bitrate cap" and "Cover art" below) - every one of these is overridable per-invocation via CLI flag or persistently via `.env` (see "Configuration").

4. **`purge-backups`** - Once re-encoded files have been spot-checked for playback/quality, permanently deletes the backed-up originals.
   ```bash
   python3 scripts/av1transcode.py purge-backups         # shows size, asks for --yes
   python3 scripts/av1transcode.py purge-backups --yes    # actually deletes
   ```

## Configuration (`.env`, optional)

Everything is a CLI flag by default, but `--output-dir`, `--audio-lang`, `--subtitle-lang`, and `--max-bitrate-fraction` can also be set persistently via a `.env` file (copy `.env.example`, or point `--env-file` at one elsewhere) so they don't need retyping every invocation - useful for an automated/scheduled `run`. Precedence: explicit CLI flag > real environment variable > `.env` file > built-in default. This skill needs no external credentials, unlike `media-organizer`'s `.env` - every key here is a plain preference, so there's nothing required to fill in.

## Language filtering, single audio track, and bitrate cap

- **Audio defaults to the single highest-quality `eng` track** (`--audio-lang`, or `all` to keep every language unfiltered). Once language-filtered, `--all-audio-tracks` keeps every matching track instead of reducing to one. Quality ranking is a plain codec-family tier (lossless -- TrueHD/FLAC/DTS-HD MA -- unconditionally outranks lossy -- E-AC3/AC3/AAC -- regardless of bitrate; ties within a tier break on channel count then bitrate), so a TrueHD Atmos track wins over a same-language E-AC3 "compatibility" copy of the same mix - a real pattern in remuxes that carry both (see `reference/presets.md`). A never-go-silent fallback still applies: if nothing matches `--audio-lang`, every original track becomes the fallback pool, and the single best of *that* is picked - never zero audio.
- **Subtitles default to `eng`, preferring plain over SDH** (`--subtitle-lang`, or `all`). Unlike audio, every matching plain (non-SDH) track is kept, not reduced to one - subtitles are cheap. SDH tracks for that language are only kept if there's no plain alternative at all; detected via the `hearing_impaired` disposition flag or an "SDH"/"hearing impaired"/"deaf" title, the same signals (deliberately simplified) as media-library's `track_policy.is_sdh`.
- The first kept audio track and first kept subtitle track each get an explicit `default` disposition flag; any other kept subtitle tracks get it explicitly cleared.
- **Output video bitrate is capped to 85% of the source's own bitrate by default** (`--max-bitrate-fraction`, or `--no-bitrate-cap` to disable). CRF/CQ targets a *quality level*, not a *size ceiling* - fine for a wastefully-high-bitrate Blu-ray remux, but an already-efficiently-encoded source (e.g. a tightly bitrate-capped web encode) can legitimately need *more* bits than the source used to hit that same quality bar, producing an output larger than the file this tool exists to shrink. This actually happened - see `reference/incidents.md` - and the cap is the fix, not just a warning after the fact. On a genuine high-bitrate remux the cap essentially never binds.

## Cover art (auto-detected, optional)

`run` looks for a conventional poster/cover image (`poster.jpg`, `poster.png`, `cover.jpg`, `cover.png`, `folder.jpg`, `folder.png`, checked in that order) sitting next to the source file and, if found, embeds it as a proper Matroska attachment named `cover.jpg`/`cover.png` - not a disposition-flagged video stream, which is silently a no-op for MKV output (confirmed the hard way, see `reference/incidents.md`). This composes directly with `media-organizer`: run that skill first to identify the movie and fetch `poster.jpg` into its folder, then `av1-transcode` picks it up automatically with no TMDB integration or new dependency of its own - it only ever reads whatever's already on disk. `--cover-image PATH` forces a specific image instead of auto-detecting; `--no-cover-art` disables the lookup entirely. Nothing found (and no override) just means no cover gets embedded - never an error.

## Before running `--yes` against anything - always confirm scope with the user first

This is a lossy, one-way, hours-long operation on the user's real media, not a quick reversible remux - **always confirm scope with the user before `run --yes`** (which file(s)/show(s), which profile, which backend), same as media-library's rule for `apply --yes` but more so given the cost of getting it wrong.

## Content profile: `film` (default) or `anime` - never auto-detected

Resolution and HDR/Dolby Vision are objective facts `probe` reads straight off the file and always get this right automatically. Whether the video itself is live-action or hand-drawn/flat-shaded animation is not derivable that way, and this skill deliberately does not reuse media-library's Japanese-audio-track heuristic to guess it: that heuristic answers "is this a Japanese-original release" for language *policy* purposes, and a Japanese-original live-action film (e.g. a Japanese Blu-ray with only a Japanese audio track) would be misclassified as animation by that signal. Pass `--profile anime` explicitly for animation/cartoon sources - ask the user if it isn't obvious from the title/folder.

## Backend: `cpu` (libsvtav1) or `nvenc` (GPU)

- **`cpu`** - highest quality per byte, much slower (can be hours for a single 4K film even on a fast preset). Uses ffmpeg `libsvtav1` with `-dolbyvision 1` when the source has Dolby Vision RPU.
- **`nvenc`** - an order of magnitude faster, needs an Ada Lovelace (RTX 40-series) or newer NVIDIA GPU, and gives up a little efficiency versus `cpu` for the same visual quality (accounted for in the preset table's GPU `cq` values - see `reference/presets.md`). Implementation splits by metadata:
  - **SDR / static HDR10** - ffmpeg `av1_nvenc` (unchanged).
  - **Dolby Vision or HDR10+** - **`nvencc`** (rigaya NVEnc, package `nvenc` on Arch/Cachy) with `--dolby-vision-rpu copy` + AV1 DV profile `10.1`, and/or `--dhdr10-info copy`. Plain ffmpeg `av1_nvenc` cannot preserve RPU or HDR10+ dynamic SEI. If `nvencc` is not on PATH: auto falls back to `cpu` for DV; explicit `--backend nvenc` on a DV source errors rather than silently dropping metadata. Optional supporting tools: `dovi_tool` / `hdr10plus_tool` for extract/inspect (HEVC-oriented); inject into AV1 is done by nvencc, not those CLIs.
- **`auto`** (default) - `nvenc` if `gpu.detect_av1_nvenc_gpu()` finds a GPU that actually accepts a real `av1_nvenc` encode (capability-probed, not guessed from the GPU's name/generation - see `reference/incidents.md`), else `cpu`. DV/HDR10+ stay on GPU when `nvencc` is available.

## Monitoring a running encode

A `run --yes` invocation prints a throttled progress line (at most once every ~10s) to stdout as it goes, but the real, full-fidelity feed is the per-file log path it prints at startup (`<root>/.cache/av1transcode/logs/<relative-path>.log` by default) - every line ffmpeg produces is written there in real time as it's produced, so `tail -f` on that path gives a genuinely live view regardless of how `run` itself was invoked (foreground, backgrounded, or from a fresh session checking on an encode started earlier). For a long CPU encode, start `run --yes` in the background and check that log path periodically rather than blocking on it.

## Safety model

- `run` without `--yes` never writes anything, even though it does a live probe of every targeted file - safe to run as often as needed to check what would happen.
- When executing: encodes to a temp file next to the original first, then verifies the result (has video, has audio, duration matches within 2%, a head-only decode spot check, and - if the source was HDR - that the output is still tagged HDR and still carries mastering-display metadata if the source did; if the source had Dolby Vision or HDR10+, that dynamic metadata must still be present on the output) *before* the original is touched. A file that ends up larger than its source gets flagged with a warning rather than silently accepted, in keeping with the actual goal (smaller, not just different).
- Originals are moved (not deleted) to `.cache/av1transcode/originals/<relative path>` by default, mirroring media-library's convention. Use `purge-backups` once re-encoded files are confirmed good. Pass `--no-backup` to delete originals immediately instead (verification still gates it). None of this applies when `--output-dir` is set - the source is never touched at all in that mode, so there's nothing to back up.
- Output is always `.mkv` regardless of the source container (AV1 in MP4 works too, but MKV is this library's convention and handles font attachments/multiple subtitle tracks better) - a `.mp4` source ends up backed up under its original extension while the new file takes over the same directory (or lands flat in `--output-dir`, under its own filename only - not mirroring the source's directory structure) under a `.mkv` name.
- **`--output-dir` writes flat, not mirrored**: the converted file lands at `<output-dir>/<source filename>.mkv`, regardless of how deep the source sits under `--root`. A pre-existing file at that destination is left alone and reported as an error unless `--overwrite-existing` is passed. If `--output-dir` (or a custom `--backup-dir`/`--log-dir`) resolves to a plain subdirectory of `--root` itself, it's excluded from the file walk so a freshly-written output can never be rediscovered as a new source and re-encoded on top of itself in the same invocation (see `reference/incidents.md`).
- Audio/subtitle tracks matching `--audio-lang`/`--subtitle-lang` (default `eng`) are kept and re-encoded/copied; attachments (fonts) are always kept regardless. This is a plain by-language filter, not the nuanced policy (commentary/SDH/anime-release detection) `media-library` owns - run that skill first for anything beyond "just keep English". Pass `--no-subtitles` to drop every subtitle track regardless of language (e.g. if a source subtitle codec can't mux into Matroska).
- Sequential by design, same as media-library's `apply`/`transcode` - no `--jobs` flag. An encode is CPU/GPU-bound rather than disk-bound the way a stream-copy remux is, but running two at once on the same GPU or the same set of CPU cores would just make both slower, not faster.

## Extending

`scripts/av1transcode/` is small and modular: `probe.py` (ffprobe wrapper, including HDR/Dolby Vision/HDR10+ side-data extraction, per-track profile/title/hearing_impaired fields, and attachment counting), `colorinfo.py` (pure HDR metadata parsing/formatting, CICP lookup tables, `needs_dynamic_metadata_path`), `presets.py` (the resolution x profile preset table plus `resolution_tier`/`select_preset`/`opus_bitrate_kbps`/`source_video_bitrate_bps`/`max_bitrate_bps`), `langfilter.py` (pure by-language audio/subtitle keep/drop, codec-quality ranking for picking the single best audio track, SDH detection, and the audio never-go-silent fallback), `config.py` (optional `.env` loading, mirroring `media-organizer`'s pattern), `gpu.py` (capability-probed AV1 NVENC GPU detection), `command.py` (builds the actual `ffmpeg` argv for libsvtav1/av1_nvenc - explicit per-stream `-map`, language/quality filtering, disposition flags, bitrate cap, cover-art attachment), `nvencc_cmd.py` (builds `nvencc` argv for GPU + Dolby Vision/HDR10+), `run.py` (per-file orchestration: backend/engine selection, live-streamed and persisted logging, verify, backup-or-mirror-to-output-dir, swap, sidecar cover detection - the same shape as media-library's `apply.py`'s `_execute_backend_plan`, sized for a job that runs hours rather than seconds), `cli.py` (argparse subcommands, CLI-flag/`.env`/default precedence via `_resolve()`). `test_presets.py`, `test_colorinfo.py`, `test_langfilter.py`, `test_av1transcode_config.py`, `test_command.py`, and `test_nvencc_cmd.py` cover the pure logic - run them (see `AGENTS.md` at the repo root for the lint/type-check/test commands, and note the test file naming gotcha it documents - test file basenames must be unique repo-wide, not just per skill) after touching any of those modules. Any change to `presets.py`'s numbers or encoder argument lists should be smoke-tested against a real short clip (see `reference/incidents.md` for why - most of its entries are bugs that only a real encoder invocation against real hardware/files surfaced, not something code review or the unit tests alone would have caught) before being trusted as a new default.
