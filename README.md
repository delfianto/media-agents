# media-agents

Companion skills for an LLM to inspect and maintain a self-hosted Plex media library -- codec/language statistics, stripping non-English audio and subtitle tracks, fixing codec-level playback incompatibilities (e.g. DTS muted over eARC on some TVs), shrinking ultra-high-bitrate Blu-ray remuxes to AV1 + Opus, and identifying/renaming/organizing downloaded media into a Plex/Jellyfin library layout -- all via `ffprobe`/`ffmpeg`/`mkvmerge`/TMDB/OpenSubtitles, with your supervision at every destructive step.

## Skills

| Skill | Folder | Description |
| :--- | :--- | :--- |
| **`media-library`** | [`skills/media-library`](skills/media-library) | Scans a Plex library (`ffprobe`), reports video/audio/subtitle codec and language statistics, and remuxes/transcodes files to strip non-English tracks, drop redundant/incompatible audio codecs, trim to a single audio track, and drop SDH subtitles -- all dry-run-by-default with a verify-then-swap safety model. |
| **`av1-transcode`** | [`skills/av1-transcode`](skills/av1-transcode) | Re-encodes video to AV1 (CPU via `libsvtav1`, or GPU via `av1_nvenc` on an Ada Lovelace+ NVIDIA GPU) and audio to Opus, squeezing max quality per byte out of Blu-ray remuxes -- resolution/HDR/Dolby-Vision-aware presets for film and anime/cartoon sources, live progress monitoring, and the same dry-run/verify-then-swap safety model as `media-library`. |
| **`media-organizer`** | [`skills/media-organizer`](skills/media-organizer) | The FileBot job without a FileBot license: identifies inbox video files via TMDB, renames/moves them into a Plex- or Jellyfin-standard layout, and fetches posters/fanart/NFO metadata and OpenSubtitles subtitles -- `.env`-configured, confidence-gated matching (no safe fallback for a misidentified file, so low-confidence matches are left for manual review), safe for unattended/automated-harness use. |
| **`stash-app`** | [`skills/stash-app`](skills/stash-app) | Organizes, tags, and browses a local [Stash](https://github.com/stashapp/stash) media server (performers, scenes, tags) via the `stash-mcp` MCP server -- prompt-only, no Python harness. See `mcp_config.json`/`mcp/stash/` for the MCP server config and tool schemas it depends on. |
| **`env-check`** | [`skills/env-check`](skills/env-check) | Read-only audit of whether this machine has everything the other skills above need -- `ffmpeg`/`ffprobe`/`mkvmerge`/`nvencc`/`dovi_tool`/`hdr10plus_tool`/`stash-mcp` on `PATH`, Python 3.14+, an AV1-capable NVIDIA GPU, `guessit` plus TMDB/OpenSubtitles credentials -- with a plain-language install/setup hint for anything missing. Never installs or changes anything itself. |

See `AGENTS.md` for repo conventions (linting, typing, and test requirements for any `skills/<name>/scripts/*.py`).
