---
name: artwork
description: Fetch or refresh posters, fanart, episode stills, and Kodi-compatible NFO metadata from TMDB for media already organized with Plex or Jellyfin provider-ID tags. Use when artwork or NFO sidecars are missing or stale, independently of media identification and renaming.
---

# Artwork

Require an already-organized path containing `{tmdb-N}` or `[tmdbid-N]`. Do not run
guessit or perform a fresh title match. Use `--tmdb-id N` only for one legacy file
without a provider tag.

```bash
python3 .agents/skills/artwork/scripts/artwork/__main__.py --path /library/Movies --type all
python3 .agents/skills/artwork/scripts/artwork/__main__.py --path /library/movie.mkv --type poster --yes
```

`--type` accepts `all`, `poster`, `fanart`, `still`, or `nfo`; `still` is episode-only.
Every run is dry-run unless `--yes` is present.

Configure `ARTWORK_TMDB_API_KEY` or shared `TMDB_API_KEY`, with optional
`ARTWORK_SERVER` and `ARTWORK_USER_AGENT`. See
[`organize` naming conventions](../organize/reference/naming-conventions.md) for the
provider-tag contract.
