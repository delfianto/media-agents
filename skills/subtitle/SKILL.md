---
name: subtitle
description: Search and download external OpenSubtitles subtitle sidecars for movies and episodes already organized with Plex or Jellyfin TMDB provider-ID tags. Use to fetch missing subtitle languages or refresh subtitle files independently of organizing and artwork.
---

# Subtitle

Read the TMDB ID from the organized movie filename or series folder and read episode
numbers from `SxxEyy`. Do not re-identify titles.

```bash
.agents/scripts/run-skill subtitle --path /library/Movies --language en
.agents/scripts/run-skill subtitle --path /library/show.mkv --language en --yes
```

Runs are dry-run by default. Existing subtitle files are skipped unless `--overwrite`
is used. Configure `SUBTITLE_OPENSUBTITLES_API_KEY`; username and password are
optional quota credentials. Other keys are `SUBTITLE_LANGUAGES`, `SUBTITLE_SERVER`,
and `SUBTITLE_USER_AGENT`.

See [`organize` naming conventions](../organize/reference/naming-conventions.md) for
the provider-tag contract.
