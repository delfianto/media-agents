# media-agents

Companion skills for an LLM to inspect and maintain a self-hosted Plex media library --
codec/language statistics, stripping non-English audio and subtitle tracks, fixing
codec-level playback incompatibilities (e.g. DTS muted over eARC on some TVs), and
shrinking ultra-high-bitrate Blu-ray remuxes to AV1 + Opus -- all via
`ffprobe`/`ffmpeg`/`mkvmerge`, with your supervision at every destructive step.

## Skills

| Skill | Folder | Description |
| :--- | :--- | :--- |
| **`media-library`** | [`skills/media-library`](skills/media-library) | Scans a Plex library (`ffprobe`), reports video/audio/subtitle codec and language statistics, and remuxes/transcodes files to strip non-English tracks, drop redundant/incompatible audio codecs, trim to a single audio track, and drop SDH subtitles -- all dry-run-by-default with a verify-then-swap safety model. |
| **`av1-transcode`** | [`skills/av1-transcode`](skills/av1-transcode) | Re-encodes video to AV1 (CPU via `libsvtav1`, or GPU via `av1_nvenc` on an Ada Lovelace+ NVIDIA GPU) and audio to Opus, squeezing max quality per byte out of Blu-ray remuxes -- resolution/HDR/Dolby-Vision-aware presets for film and anime/cartoon sources, live progress monitoring, and the same dry-run/verify-then-swap safety model as `media-library`. |

See `AGENTS.md` for repo conventions (linting, typing, and test requirements for any
`skills/<name>/scripts/*.py`).
