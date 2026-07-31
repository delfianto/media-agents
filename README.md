# Psammophis

Psammophis is a standalone Python toolkit for maintaining a self-hosted Plex or Jellyfin media library. The name is a nod to sand snakes: the application quietly removes waste, reshapes large remuxes, and leaves the library easier to carry without making destructive decisions on its own.

The project is packaged under `src/psammophis`, requires Python 3.14, and is invoked through one CLI. It uses `ffprobe`/`ffmpeg` and, where needed, `mkvmerge`, `mkvpropedit`, NVEncC, TMDB, and OpenSubtitles. Operations that change media are dry-run by default and verify temporary output before any original is replaced.

## Requirements

Psammophis can run as a standalone CLI; an AI agent is optional. The exact
requirements depend on the command. Install the base runtime first, then the
system tools for the features you intend to use. All executables must be on
`PATH` for both your shell and the agent process.

### Base software

| Dependency | Requirement | Notes |
| --- | --- | --- |
| Operating system | Linux or another POSIX-like system | Linux is the primary target and is required for the documented NVIDIA/SVT capability probes. The launcher is a Bash script. |
| Python | 3.14 or newer | This is a hard project requirement, not merely the development target. |
| [`uv`](https://docs.astral.sh/uv/) | Required for `run.sh` / `.agents/run.sh` | `uv` installs the package and its Python dependencies (`guessit` and `rich`) from `pyproject.toml` and `uv.lock`. A manually installed package can instead be run as `psammophis` or `python -m psammophis`. |
| Bash | Required for the provided launcher | Not needed when invoking an already-installed `psammophis` package directly. |
| Network access | Feature-dependent | Required for TMDB, OpenSubtitles, and any remote or local-network MCP service; media-only operations work offline. |

Install the locked runtime environment with:

```bash
uv sync
```

Use `uv sync --group dev` only when developing the project; it additionally
installs `ruff`, `basedpyright`, and `pytest`.

### Media and feature software

| Command or feature | Required software | Optional software / capability |
| --- | --- | --- |
| `analyze`, `transcode probe` | `ffprobe`; `ffmpeg` for grain sampling and backend capability checks | NVIDIA support described below |
| `track-strip` | `ffprobe`, `ffmpeg`, and MKVToolNix (`mkvmerge`) | None |
| `mkvedit` | MKVToolNix (`mkvmerge` and `mkvpropedit`) | None |
| `transcode run` | `ffprobe`; `ffmpeg` built with the `libsvtav1` and `libopus` encoders; `mkvpropedit`; an identifiable SVT-AV1 implementation (upstream or community `svt-av1-hdr`) | `av1_nvenc`, NVEncC, `dovi_tool`, and `hdr10plus_tool` as described below |
| `compare` (video) | `ffprobe` and an FFmpeg build containing the `libvmaf` filter | Official libjxl `ssimulacra2` binary |
| `compare` (images) | ImageMagick 7 (`magick`) with decoders for the compared formats and LittleCMS color-profile support | Official libjxl `ssimulacra2` binary |
| `organize` | Bundled Python package `guessit`, TMDB credentials, and network access | None |
| `artwork` | TMDB credentials and network access | None |
| `subtitle` fetch | OpenSubtitles API credentials and network access | Username/password can provide account quota; they are not needed for commands that do not fetch |
| `stash-app` agent skill | A configured `stash-mcp` executable and a reachable Stash server | This is prompt/MCP-only and is not part of the Python CLI |
| `env-check`, `runs` | Base runtime only | `env-check` reports the other installed capabilities |

Package names vary by operating system. FFmpeg distributions also vary in
their compiled-in encoders and filters, so installing a package named
`ffmpeg` does not by itself guarantee `libsvtav1`, `libopus`, `av1_nvenc`, or
`libvmaf` support. For CPU AV1 encoding, either the upstream SVT-AV1 library
or the community-enhanced `svt-av1-hdr` library may provide FFmpeg's
`libsvtav1` encoder. They intentionally use the same encoder name but have
different quality/CRF scales and tuning options. Psammophis identifies the
implementation linked into FFmpeg at runtime and selects the matching preset
table; an unknown implementation is rejected rather than guessed. The
community fork is especially useful for its HDR-oriented tuning, but it is an
alternative implementation, not an additional encoder that must be installed
alongside upstream SVT-AV1.

After installation, run the project's read-only capability
audit. It checks the core runtime and most feature dependencies; the table
above remains authoritative for feature-specific requirements such as
`libopus`, `libvmaf`, and ImageMagick:

```bash
./run.sh env-check
./run.sh env-check --required-only
```

For a focused check, use `--category`, for example `--category transcode` or
`--category mkvedit`. The audit is read-only and does not install anything.

### Hardware and storage

| Resource | Requirement |
| --- | --- |
| CPU | No specific model is required. Probing, remuxing, metadata, and API commands are light; CPU AV1 encoding uses upstream SVT-AV1 or the community-enhanced `svt-av1-hdr` through FFmpeg's `libsvtav1` and is practical only on a reasonably modern multi-core CPU. |
| Memory | There is no application-enforced minimum. Allow at least 8 GiB for ordinary probing/remuxing and prefer 16 GiB or more for 4K AV1 encoding and full-reference comparison; FFmpeg settings, frame size, and external-tool builds determine actual use. |
| GPU | Not required. The CPU backend is the supported fallback. GPU AV1 encoding requires an NVIDIA Ada Lovelace / RTX 40-series or newer GPU, a working NVIDIA driver (`nvidia-smi`), and FFmpeg with `av1_nvenc`. Psammophis validates capability with a real test encode rather than relying on the model name. |
| GPU dynamic HDR | Preserving Dolby Vision or HDR10+ on the GPU path additionally requires rigaya NVEncC (`nvencc`, commonly packaged as `nvenc`) with the relevant metadata support. Without it, automatic mode uses the safe CPU fallback for Dolby Vision. `dovi_tool` and `hdr10plus_tool` are optional inspection helpers. |
| Storage | The media root and destination must be writable. Keep free space for at least one complete temporary output per active file. When backups are enabled, the verified output remains alongside the original moved into `.cache`, so the output size is additional retained usage until backups are purged. Large library-wide runs can therefore require terabytes of headroom. |

CPU and GPU encoding are deliberately sequential; adding more GPUs or CPU
workers does not make Psammophis process multiple files concurrently.

### Credentials and agent setup

API-backed commands read credentials from the environment or the optional
`.env`/`.envrc` configuration loaded by the launcher:

| Feature | Variables |
| --- | --- |
| Organize | `ORGANIZE_TMDB_API_KEY` or shared `TMDB_API_KEY` |
| Artwork | `ARTWORK_TMDB_API_KEY` or shared `TMDB_API_KEY` |
| Subtitles | `SUBTITLE_OPENSUBTITLES_API_KEY`; optional `SUBTITLE_OPENSUBTITLES_USERNAME` and `SUBTITLE_OPENSUBTITLES_PASSWORD` |

To use the workflows through an agent, the agent host must be able to read
`AGENTS.md` and `skills/*/SKILL.md`, run shell commands, keep a long-running
process attached for encodes/comparisons, and access the media library with the
same read/write permissions as the user. The documented layout symlinks this
checkout into the library root as `.agents`:

```bash
ln -s /path/to/media-agents /path/to/library/.agents
```

Only `stash-app` needs MCP. Its host must load [`mcp_config.json`](mcp_config.json),
find `stash-mcp` on `PATH`, and reach the configured Stash server. The other
skills invoke the local Psammophis CLI and do not require an MCP server.

## Quick start

From the repository checkout:

```bash
uv sync --group dev
./run.sh --help
./run.sh transcode --help
./run.sh compare --help
uv run pytest
```

From a media-library root where this repository is symlinked as `.agents`:

```bash
.agents/run.sh analyze --help
.agents/run.sh track-strip stats
.agents/run.sh transcode probe --path "Dune"
.agents/run.sh --reporter jsonl transcode run --path "Dune" --yes
```

`run.sh` is the human-friendly launcher. `uv run psammophis ...` is the direct project entry point. For automation or an LLM, use `--reporter jsonl`: it emits versioned lifecycle, progress-heartbeat, result, and completion events while keeping command output structured and durable run journals available through `runs show` and `runs events`.

## Skills

| Skill | Purpose |
| --- | --- |
| [`analyze`](skills/analyze) | Read-only explanation of the AV1 preset, detected HDR/grain characteristics, backend, and active encoder implementation. |
| [`artwork`](skills/artwork) | Fetch posters, fanart, episode stills, and NFO sidecars for organized media. |
| [`compare`](skills/compare) | Full-reference video comparison (VMAF/PSNR/SSIM/MS-SSIM) and color-managed still-image comparison, with stratified or full sampling. |
| [`env-check`](skills/env-check) | Read-only audit of external tools, Python, GPU capability, and optional credentials. |
| [`mkvedit`](skills/mkvedit) | Safely edit Matroska titles, track metadata, attachments, tags, and chapters without re-encoding. |
| [`organize`](skills/organize) | Match inbox media with guessit/TMDB and move it into Plex/Jellyfin naming layouts. |
| [`stash-app`](skills/stash-app) | Prompt-only workflows for a local Stash server through the configured MCP server. |
| [`subtitle`](skills/subtitle) | Find and download OpenSubtitles sidecars for already-organized media. |
| [`track-strip`](skills/track-strip) | Scan and remux media to keep the intended language/audio/subtitle tracks and drop redundant codecs. |
| [`transcode`](skills/transcode) | Re-encode video to AV1 and selected audio to Opus with CPU/GPU, HDR/Dolby-Vision, grain-aware, and bitrate-capped paths. |

Public command names are `analyze`, `artwork`, `compare`, `env-check`,
`mkvedit`, `organize`, `runs`, `subtitle`, `track-strip`, and `transcode`.

## Choosing the right operation

- Use `analyze` before a large encode when you want to see why a profile, preset, backend, grain route, or CRF was selected.
- Use `track-strip` when codecs should remain unchanged and the goal is to remove foreign dubs, redundant audio, SDH subtitles, or incompatible tracks.
- Use `transcode` when video size is the problem. It targets high-quality AV1, can convert audio to Opus, and is substantially slower or more expensive than a remux. Confirm the exact scope before passing `--yes`.
- Use `compare` before purging originals. Numbers are evidence, not a replacement for checking the worst-looking frames and testing playback.

## Notes and rationale

The skill definitions stay short and operational. Detailed, reusable explanations live in [`notes/`](notes/):

- [`av1-encoding.md`](notes/av1-encoding.md) — current CPU/GPU presets, mainline versus `svt-av1-hdr`, bitrate caps, expected savings, and AV1/Opus tradeoffs.
- [`track-filtering.md`](notes/track-filtering.md) — language, anime, commentary, and SDH detection, safety gates, stripping mechanics, and space-saving estimates.
- [`quality-comparison.md`](notes/quality-comparison.md) — comparison pipeline, sampling, metrics, and how to interpret values.
- [`library-naming.md`](notes/library-naming.md) — Plex/Jellyfin folder, filename, artwork, and NFO conventions.
- [`organize-apis.md`](notes/organize-apis.md) — TMDB, OpenSubtitles, and the OpenSubtitles hash request shapes.

These notes deliberately describe portable lessons and current behavior, not one machine's incident diary or a particular library's file names.

## Development

```bash
uv sync --group dev
uv run ruff check .
uv run ruff format .
uv run basedpyright .
uv run pytest -q
uv build
```

See [`AGENTS.md`](AGENTS.md) for repository conventions, safety requirements, launcher behavior, and quality gates. External tools and credentials are checked by `env-check`; the application does not install them automatically.
