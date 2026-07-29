---
name: av1-transcode
version: 0.1.0
description: >
    Use when the user wants to shrink ultra-high-bitrate Blu-ray remuxes in
    this Plex library by re-encoding video to AV1 and audio to Opus, while
    keeping quality as close to the source as possible - for 4K HDR/Dolby
    Vision remuxes, 1080p film, anime/cartoon sources, or older catalog
    titles. Supports both a CPU path (libsvtav1, the highest-quality option,
    slow) and a GPU path (av1_nvenc, much faster, needs an Ada Lovelace or
    newer NVIDIA GPU) with live progress monitoring during the encode.
    Triggers on phrases like "convert to AV1", "re-encode to AV1", "shrink
    this remux", "transcode to AV1 and Opus", "use the GPU/NVENC to encode",
    or any request to reduce the file size of a movie/show while preserving
    quality (as opposed to media-library's track stripping, which changes
    nothing about codecs).
allowed-tools:
    - Bash
metadata:
    author: "av1-transcode"
    focus: "AV1 (libsvtav1/av1_nvenc) + Opus re-encoding for max quality per byte"
---

# av1-transcode

Re-encodes video to AV1 and every audio track to Opus, for shrinking
ultra-high-bitrate Blu-ray remuxes without giving up much (ideally any)
visible quality. Unlike `media-library` - which only ever stream-copies
(remuxes track selection, never touches codecs) - this skill always
re-encodes video, and is the slow/expensive/semi-destructive operation of
the two. Run `media-library`'s `apply` first if a file also needs
non-English/duplicate tracks or SDH subtitles dropped; there's no reason to
spend encode time on streams that are about to be stripped anyway.

The toolkit lives at `scripts/av1transcode.py` (next to this file), a
zero-third-party-dependency Python package wrapping `ffprobe`/`ffmpeg`. See
`reference/presets.md` for the full rationale (and citations) behind every
encoder setting, and `reference/incidents.md` for real bugs a smoke test
against actual files in this library caught before they ever became defaults
- read both before changing `presets.py`, `command.py`, or `gpu.py`.

## Running commands

```bash
python3 <path-to-this-skill>/scripts/av1transcode.py <subcommand> [options]
```

1. **`probe`** - Read-only. Reports each file's resolution/dynamic range
   (SDR/HDR10/Dolby Vision)/audio layout, and exactly which preset and
   backend `run` would pick for it.
   ```bash
   python3 scripts/av1transcode.py probe --path "Some Movie"
   python3 scripts/av1transcode.py probe --path "Some Anime" --profile anime
   ```

2. **`list-presets`** - Prints the full built-in resolution x profile preset
   table (CPU and GPU settings for each).
   ```bash
   python3 scripts/av1transcode.py list-presets
   ```

3. **`run`** - Re-encodes video to AV1 and audio to Opus. **Defaults to a
   dry run** that probes the file and prints the exact `ffmpeg` command it
   would run, touching nothing. Pass `--yes` to actually execute.
   ```bash
   # dry run (safe, default) - always do this first and read the printed command
   python3 scripts/av1transcode.py run --path "Some Movie"

   # execute for real, one file/show at a time
   python3 scripts/av1transcode.py run --path "Some Movie" --yes

   # anime/cartoon sources: --profile is never auto-detected, see below
   python3 scripts/av1transcode.py run --path "Some Anime" --profile anime --yes
   ```
   Defaults: `--profile film`, `--backend auto` (GPU if an AV1-capable
   NVIDIA GPU is found and the source has no Dolby Vision, else CPU).

4. **`purge-backups`** - Once re-encoded files have been spot-checked for
   playback/quality, permanently deletes the backed-up originals.
   ```bash
   python3 scripts/av1transcode.py purge-backups         # shows size, asks for --yes
   python3 scripts/av1transcode.py purge-backups --yes    # actually deletes
   ```

## Before running `--yes` against anything - always confirm scope with the user first

This is a lossy, one-way, hours-long operation on the user's real media, not
a quick reversible remux - **always confirm scope with the user before
`run --yes`** (which file(s)/show(s), which profile, which backend), same as
media-library's rule for `apply --yes` but more so given the cost of getting
it wrong.

## Content profile: `film` (default) or `anime` - never auto-detected

Resolution and HDR/Dolby Vision are objective facts `probe` reads straight
off the file and always get this right automatically. Whether the video
itself is live-action or hand-drawn/flat-shaded animation is not derivable
that way, and this skill deliberately does not reuse media-library's
Japanese-audio-track heuristic to guess it: that heuristic answers "is this
a Japanese-original release" for language *policy* purposes, and a
Japanese-original live-action film (e.g. a Japanese Blu-ray with only a
Japanese audio track) would be misclassified as animation by that signal.
Pass `--profile anime` explicitly for animation/cartoon sources - ask the
user if it isn't obvious from the title/folder.

## Backend: `cpu` (libsvtav1) or `nvenc` (av1_nvenc)

- **`cpu`** - highest quality per byte, much slower (can be hours for a
  single 4K film even on a fast preset). The only backend that preserves
  Dolby Vision RPU metadata.
- **`nvenc`** - an order of magnitude faster, needs an Ada Lovelace (RTX
  40-series) or newer NVIDIA GPU, and gives up a little efficiency versus
  `cpu` for the same visual quality (accounted for in the preset table's GPU
  `cq` values - see `reference/presets.md`). **Cannot preserve Dolby Vision**
  (`av1_nvenc` has no equivalent of libsvtav1's RPU-passthrough option) -
  `run` automatically falls back to `cpu` for any Dolby Vision source
  regardless of `--backend`, and refuses outright if `--backend nvenc` is
  forced explicitly on one.
- **`auto`** (default) - `nvenc` if `gpu.detect_av1_nvenc_gpu()` finds a GPU
  that actually accepts a real `av1_nvenc` encode (capability-probed, not
  guessed from the GPU's name/generation - see `reference/incidents.md` for
  why that distinction mattered on this exact machine) and the source has no
  Dolby Vision, else `cpu`.

## Monitoring a running encode

A `run --yes` invocation prints a throttled progress line (at most once
every ~10s) to stdout as it goes, but the real, full-fidelity feed is the
per-file log path it prints at startup
(`<root>/.cache/av1transcode/logs/<relative-path>.log` by default) - every
line ffmpeg produces is written there in real time as it's produced, so
`tail -f` on that path gives a genuinely live view regardless of how `run`
itself was invoked (foreground, backgrounded, or from a fresh session
checking on an encode started earlier). For a long CPU encode, start `run
--yes` in the background and check that log path periodically rather than
blocking on it.

## Safety model

- `run` without `--yes` never writes anything, even though it does a live
  probe of every targeted file - safe to run as often as needed to check
  what would happen.
- When executing: encodes to a temp file next to the original first, then
  verifies the result (has video, has audio, duration matches within 2%, a
  head-only decode spot check, and - if the source was HDR - that the
  output is still tagged HDR and still carries mastering-display metadata if
  the source did) *before* the original is touched. A file that ends up
  larger than its source gets flagged with a warning rather than silently
  accepted, in keeping with the actual goal (smaller, not just different).
- Originals are moved (not deleted) to `.cache/av1transcode/originals/<relative
  path>` by default, mirroring media-library's convention. Use
  `purge-backups` once re-encoded files are confirmed good. Pass
  `--no-backup` to delete originals immediately instead (verification still
  gates it).
- Output is always `.mkv` regardless of the source container (AV1 in MP4
  works too, but MKV is this library's convention and handles font
  attachments/multiple subtitle tracks better) - a `.mp4` source ends up
  backed up under its original extension while the new file takes over the
  same directory under a `.mkv` name.
- Every audio/subtitle/attachment stream in the source is kept (only audio
  *codec* changes; audio/subtitle track *selection* is media-library's job,
  not this skill's) - pass `--no-subtitles` if a source subtitle codec can't
  mux into Matroska (rare; surfaces as an `ffmpeg` error in the log if hit
  without the flag).
- Sequential by design, same as media-library's `apply`/`transcode` - no
  `--jobs` flag. An encode is CPU/GPU-bound rather than disk-bound the way a
  stream-copy remux is, but running two at once on the same GPU or the same
  set of CPU cores would just make both slower, not faster.

## Extending

`scripts/av1transcode/` is small and modular: `probe.py` (ffprobe wrapper,
including HDR/Dolby Vision side-data extraction), `colorinfo.py` (pure HDR
metadata parsing/formatting, CICP lookup tables), `presets.py` (the
resolution x profile preset table plus `resolution_tier`/`select_preset`/
`opus_bitrate_kbps`), `gpu.py` (capability-probed AV1 NVENC GPU detection),
`command.py` (builds the actual `ffmpeg` argv for either backend),
`run.py` (per-file orchestration: backend selection, live-streamed and
persisted logging, verify, backup, swap - the same shape as media-library's
`apply.py`'s `_execute_backend_plan`, sized for a job that runs hours rather
than seconds), `cli.py` (argparse subcommands). `test_presets.py` and
`test_colorinfo.py` cover the pure logic - run them (see `AGENTS.md` at the
repo root for the lint/type-check/test commands) after touching either
module. Any change to `presets.py`'s numbers or `command.py`'s argument
lists should be smoke-tested against a real short clip (see
`reference/incidents.md` for why - three of its four entries are bugs that
only a real `ffmpeg` invocation against real hardware surfaced, not
something code review or the unit tests alone would have caught) before
being trusted as a new default.
