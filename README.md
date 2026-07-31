# Psammophis

Psammophis is a standalone Python toolkit for maintaining a self-hosted Plex or Jellyfin media library. The name is a nod to sand snakes: the application quietly removes waste, reshapes large remuxes, and leaves the library easier to carry without making destructive decisions on its own.

The project is packaged under `src/psammophis`, requires Python 3.14, and is invoked through one CLI. It uses `ffprobe`/`ffmpeg` and, where needed, `mkvmerge`, `mkvpropedit`, NVEncC, TMDB, and OpenSubtitles. Operations that change media are dry-run by default and verify temporary output before any original is replaced.

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

The only public command names are `transcode` and `compare`; historical names are intentionally not accepted.

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
