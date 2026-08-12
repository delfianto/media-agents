---
name: psm-subtitle
description: Search and download external OpenSubtitles subtitle sidecars for already-organized movies and episodes, optionally muxing them into the MKV as a real track. Use to fetch missing subtitle languages or refresh subtitle files independently of organizing and artwork.
---

# Subtitle

Requires an already-organized path -- never re-identifies a title. A title's TMDB id
is resolved the same way `artwork` does, in order: a `{tmdb-N}`/`[tmdbid-N]` path tag
if present (older libraries, or a hand-tagged file -- `organize` no longer writes this
tag into folder/file names, see
[`library-naming.md`](../../notes/library-naming.md)), then the video's own NFO
sidecar's `<uniqueid type="tmdb">` (the normal path for anything organized since).
Episode numbers are read from `SxxEyy` in the filename either way.

```bash
.agents/run.sh subtitle --path /library/Movies --language en
.agents/run.sh subtitle --path /library/show.mkv --language en --yes
.agents/run.sh subtitle --path /library/show.mkv --language en --embed --yes
```

Runs are dry-run by default. Existing subtitle files are skipped unless `--overwrite`
is used. Configure `SUBTITLE_OPENSUBTITLES_API_KEY`; username and password are
optional quota credentials. Other keys are `SUBTITLE_LANGUAGES`, `SUBTITLE_SERVER`,
and `SUBTITLE_USER_AGENT`.

`--embed` muxes each downloaded `.srt` into its video as a real subtitle track via
`mkvmerge`, right after the download -- **`mkvedit` alone cannot do this**:
`mkvpropedit` only edits properties on tracks/attachments that already exist, it has
no way to add a track's payload, so a genuine new track needs a real remux (the same
operation family as `track-strip`'s own backends). Skips a video where that language
is already embedded rather than adding a duplicate track. The new track becomes the
default subtitle, clearing the flag from whatever was default before, unless
`--no-embed-default` is passed. Safety model mirrors `mkvedit`: writes to a temp file
first, verifies (video/audio intact, exactly one more subtitle track than before,
duration matches, decodes cleanly at the head) before touching the original, and
keeps a sibling backup (`<file>.subtitle-embed.bak`) restored automatically if
anything after the remux fails.
