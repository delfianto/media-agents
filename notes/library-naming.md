# Plex and Jellyfin naming conventions, and how FileBot fits in

## How FileBot actually does this (and why this skill doesn't need a license for it)

FileBot's pipeline is: parse the filename for identifying info -> query an online database to resolve it to an exact title/year/season/episode -> rename/move the file into a folder structure, optionally writing artwork and subtitles alongside it. The pieces:

- **Movies:** matched against TheMovieDB (TMDB), OpenSubtitles, or IMDb.
- **TV/anime:** matched against TheTVDB, AniDB, or TMDB.
- **Subtitles:** fetched from OpenSubtitles, using an exact file-hash match when possible, falling back to title-based search.
- **Artwork/NFO:** pulled from whichever database resolved the match, in Kodi-compatible NFO format for local metadata.

None of that requires a FileBot license -- the license pays for FileBot's own matching engine, UI, and years of accumulated edge-case handling, not for access to the underlying databases. TMDB is genuinely free for this exact use case; the one piece FileBot leans on that *isn't* free-with-a-key anymore is TheTVDB (see below), which this skill sidesteps entirely by using TMDB for TV shows too, rather than integrating both.

## Why TMDB only, not TheTVDB

TheTVDB's v4 API dropped its old no-strings-attached free tier: real usage now needs either a "subscriber-supported" key (every end user needs their own $12/year TheTVDB subscription to get a login PIN) or a negotiated commercial license priced by company revenue tier. There's a free "user-supported" registration for the API key itself, but it still pushes the subscription requirement onto whoever actually runs it day to day.

TMDB has no such catch: a free account and a free API key (Settings -> API) cover both movies and TV shows, with no subscription, no per-end-user requirement, and no usage-based paywall for this scale of use. That's the entire reason this skill only integrates TMDB.

## Provider-ID tags: supported syntax, not used in this skill's own output

Both platforms *support* an optional folder/file provider-ID tag for
disambiguation -- Plex: curly braces, hyphen before the digits
(`{tmdb-12345}`, also `{tvdb-12345}`/`{imdb-tt1234567}`); Jellyfin: square
brackets, no hyphen before "tmdbid" itself (`[tmdbid-12345]`, also
`[imdbid-tt1234567]`/`[tvdbid-12345]`). **Neither platform requires it** --
both match primarily by folder name + year via their own metadata agent, and
recommend the tag only as a disambiguation aid for two same-title/year
entries. This skill stopped emitting it (2026-08) because it adds visual
noise to every folder/file name for a library that already resolves cleanly
without it, and it isn't the tool's job to guess whether name+year is
disambiguation-worthy on a per-title basis.

`naming.py`'s `extract_provider_id`/`_PROVIDER_ID_RE` still recognize this
syntax -- it's how `parse.py` accepts a *hand-embedded* id in a messy
**inbox** filename (e.g. `X-Men.2.2003.{tmdb-36658}...`) to skip fuzzy
title matching when guessit's own search would pick the wrong TMDB entry
(a real case in this library: TMDB's actual titles for two 2003 films are
"X2" and "Star Wars", not "X-Men 2"/"Star Wars Episode IV..." -- fuzzy
matching alone scored those too low to auto-apply). That inbox-embedding
use is unaffected by dropping the tag from *organized* output -- they're
different filenames read at different pipeline stages.

Once a title is organized, anything that needs its TMDB id back (`artwork`
refreshing art later, without re-running guessit or a fresh title match)
reads it from the NFO sidecar's `<uniqueid type="tmdb">` instead -- see the
NFO section below. `artwork identify()` tries a path tag first (for
libraries organized before this change, or a hand-tagged file) and falls
back to the sidecar NFO.

## Plex naming (verified via multiple independent Plex Support/community sources)

**Movies:**
```
Movies/Movie Title (Year)/Movie Title (Year).mkv
```

**TV shows:**
```
TV Shows/Series Name (Year)/Season 01/Series Name - S01E01 - Episode Title.mkv
```

- Season folders: `Season 01`, `Season 02`, ... zero-padded to two digits. `Season 00` (or `/Specials`) for specials.
- The `- S01E01 -` pattern (capital S/E, hyphen-separated) is what this library's Plex instance is matched against; the episode title suffix is optional.

## Jellyfin naming (verified via jellyfin.org's own docs)

**Movies:**
```
Movies/Movie Title (Year)/Movie Title (Year).mkv
```

**TV shows:**
```
Shows/Series Name (Year)/Season 01/Series Name (Year) S01E01 Episode Title.mkv
```

- Jellyfin's own docs are explicit: "the video files within a folder should have the same name as the folder" -- same as Plex, the movie file matches its folder name exactly.
- Season folders: `Season 01`, `Season 02`, ... **never** abbreviated to `S01`; zero-padded so every entry has the same digit count.
- Episode files: `Series Name (Year) S01E01 Episode Title.ext` -- no dashes, space-separated, uppercase S/E.
- Multi-episode files (`S01E01-E02.mkv`) are read as one entry combining both episodes' metadata; Jellyfin's docs recommend splitting them with MKVToolNix instead. This skill's `parse.py` takes the *first* episode number from a multi-episode guessit result rather than attempting a split -- out of scope for v1.

## NFO metadata (Kodi-compatible; both Plex and Jellyfin read it)

Verified against Jellyfin's own NFO-parsing docs (`jellyfin.org/docs/general/server/metadata/nfo/`): provider IDs are read from `<uniqueid type="tmdb" default="true">150540</uniqueid>` / `<uniqueid type="imdb">tt2096673</uniqueid>` -- the `type` attribute is what ties an ID to a specific provider, and `default="true"` marks which one Jellyfin should treat as primary when more than one is present.

`nfo.py` builds three document shapes:
- `movie.nfo`-equivalent (named `<video-filename>.nfo`, Kodi's own recommended convention for movies in their own folder): title, originaltitle, year, plot, tagline, runtime, premiered, genre*, studio*, director*, credits* (writers), actor* (name/role/order), uniqueid*.
- `tvshow.nfo` at the series-folder root: title, plot, premiered, mpaa, genre*, studio*, uniqueid*. Only written once per series (checked via `tvshow_nfo_path.exists()` before generating it) so re-running `organize` against more episodes of an already-organized show doesn't keep regenerating it.
- Episode `<episodedetails>` NFO alongside each episode file: title, showtitle, season, episode, plot, aired, director*, credits*, uniqueid.

Artwork file names (`poster.jpg`, `fanart.jpg`) match the Kodi/Jellyfin convention for folder-level artwork -- both platforms look for exactly these names at the movie/series folder root without needing anything in the NFO to point at them.
