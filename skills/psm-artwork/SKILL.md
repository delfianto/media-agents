---
name: psm-artwork
description: Fetch or refresh posters, fanart, episode stills, season-poster episode covers, and Kodi-compatible NFO metadata from TMDB for already-organized media. Use when artwork or NFO sidecars are missing or stale, independently of media identification and renaming.
---

# Artwork

Requires an already-organized path -- identification never runs guessit or a fresh
title match. A title's TMDB id is resolved, in order: a `{tmdb-N}`/`[tmdbid-N]` path
tag if present (older libraries, or a hand-tagged file -- `organize` no longer writes
this tag into folder/file names, see
[`library-naming.md`](../../notes/library-naming.md)), then the video's own NFO
sidecar's `<uniqueid type="tmdb">` (the normal path for anything organized since).
Use `--tmdb-id N` only for one legacy file with neither.

```bash
.agents/run.sh artwork --path /library/Movies --type all
.agents/run.sh artwork --path /library/movie.mkv --type poster --yes
```

`--type` accepts `all`, `poster`, `fanart`, `still`, `cover`, or `nfo`; `still` and
`cover` are episode-only. Every run is dry-run unless `--yes` is present.

`cover` fetches the *episode's own season* poster from TMDB (falling back to the
show's main poster if that season has none) and embeds it as the episode file's MKV
cover attachment -- a real Matroska attachment change via `mkvedit`'s cover path, not
another `poster.jpg` sidecar. This replaces whatever cover attachment the file
already carries (a leftover scene-release poster is common). Movie files aren't
touched by `cover` -- point `mkvedit --cover` at a movie directly if it needs one.

**`all` no longer includes `still` for episodes -- `cover` (the season poster,
embedded) is the default per-episode artwork now, not a separate per-episode
screencap.** Fetching and storing a still is unnecessary work when the season
poster already serves as the file's own artwork; don't add it back into a
default/batch pass. `--type still` remains available standalone for anyone who
explicitly wants the classic episode-screencap sidecar (`<episode>.jpg`) on top
of that -- just isn't fetched automatically anymore.

Configure `ARTWORK_TMDB_API_KEY` or shared `TMDB_API_KEY`, with optional
`ARTWORK_SERVER` and `ARTWORK_USER_AGENT`. See
[`organize` naming conventions](../../notes/library-naming.md) for the
provider-tag contract.
