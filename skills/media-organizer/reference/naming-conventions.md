# Plex and Jellyfin naming conventions, and how FileBot fits in

## How FileBot actually does this (and why this skill doesn't need a license for it)

FileBot's pipeline is: parse the filename for identifying info -> query an
online database to resolve it to an exact title/year/season/episode ->
rename/move the file into a folder structure, optionally writing artwork and
subtitles alongside it. The pieces:

- **Movies:** matched against TheMovieDB (TMDB), OpenSubtitles, or IMDb.
- **TV/anime:** matched against TheTVDB, AniDB, or TMDB.
- **Subtitles:** fetched from OpenSubtitles, using an exact file-hash match
  when possible, falling back to title-based search.
- **Artwork/NFO:** pulled from whichever database resolved the match, in
  Kodi-compatible NFO format for local metadata.

None of that requires a FileBot license -- the license pays for FileBot's own
matching engine, UI, and years of accumulated edge-case handling, not for
access to the underlying databases. TMDB is genuinely free for this exact
use case; the one piece FileBot leans on that *isn't* free-with-a-key
anymore is TheTVDB (see below), which this skill sidesteps entirely by using
TMDB for TV shows too, rather than integrating both.

## Why TMDB only, not TheTVDB

TheTVDB's v4 API dropped its old no-strings-attached free tier: real usage
now needs either a "subscriber-supported" key (every end user needs their
own $12/year TheTVDB subscription to get a login PIN) or a negotiated
commercial license priced by company revenue tier. There's a free
"user-supported" registration for the API key itself, but it still pushes
the subscription requirement onto whoever actually runs it day to day.

TMDB has no such catch: a free account and a free API key (Settings -> API)
cover both movies and TV shows, with no subscription, no per-end-user
requirement, and no usage-based paywall for this scale of use. That's the
entire reason this skill only integrates TMDB.

## Plex naming (verified via multiple independent Plex Support/community
sources quoting the same syntax)

**Movies:**
```
Movies/Movie Title (Year) {tmdb-12345}/Movie Title (Year) {tmdb-12345}.mkv
```

**TV shows:**
```
TV Shows/Series Name (Year) {tmdb-12345}/Season 01/Series Name - s01e01 - Episode Title.mkv
```

- Provider-ID tag: curly braces, hyphen before the digits --
  `{tmdb-12345}`, `{tvdb-12345}`, or `{imdb-tt1234567}`. Square brackets do
  **not** work here; it must be `{}`.
- The tag goes on the folder (applies to every file inside, and guarantees a
  unique folder name even for two movies that happen to share a title/year);
  Plex also accepts it on the file name, so this skill sets it on both for a
  movie, matching the folder-name-equals-file-name-minus-extension
  convention both platforms otherwise share.
- Season folders: `Season 01`, `Season 02`, ... zero-padded to two digits.
  `Season 00` (or `/Specials`) for specials.
- Episode files carry **no** ID tag of their own -- only the series folder
  does. The `- s01e01 -` pattern (lowercase s/e, hyphen-separated) is what
  Plex's episode matcher looks for; the episode title suffix is optional.

## Jellyfin naming (verified via jellyfin.org's own docs)

**Movies:**
```
Movies/Movie Title (Year) [tmdbid-12345]/Movie Title (Year) [tmdbid-12345].mkv
```

**TV shows:**
```
Shows/Series Name (Year) [tmdbid-12345]/Season 01/Series Name (Year) S01E01 Episode Title.mkv
```

- Provider-ID tag: square brackets, **no** hyphen before the digits --
  `[tmdbid-12345]` uses a hyphen between the key and id (`tmdbid-12345`),
  but the key itself has no separator from "tmdb" (`tmdbid`, not `tmdb id`).
  Also supports `[imdbid-tt1234567]` and `[tvdbid-12345]`.
- Jellyfin's own docs are explicit: "the video files within a folder should
  have the same name as the folder" -- so, same as Plex, the tag goes on
  both the movie's folder and its file.
- Season folders: `Season 01`, `Season 02`, ... **never** abbreviated to
  `S01`; zero-padded so every entry has the same digit count.
- Episode files: `Series Name (Year) S01E01 Episode Title.ext` -- no
  dashes, space-separated, uppercase S/E. No ID tag on the episode file
  itself (same rule as Plex -- only the series folder carries it).
- Multi-episode files (`S01E01-E02.mkv`) are read as one entry combining
  both episodes' metadata; Jellyfin's docs recommend splitting them with
  MKVToolNix instead. This skill's `parse.py` takes the *first* episode
  number from a multi-episode guessit result rather than attempting a split
  -- out of scope for v1.

## NFO metadata (Kodi-compatible; both Plex and Jellyfin read it)

Verified against Jellyfin's own NFO-parsing docs (`jellyfin.org/docs/general/server/metadata/nfo/`):
provider IDs are read from `<uniqueid type="tmdb" default="true">150540</uniqueid>`
/ `<uniqueid type="imdb">tt2096673</uniqueid>` -- the `type` attribute is
what ties an ID to a specific provider, and `default="true"` marks which one
Jellyfin should treat as primary when more than one is present.

`nfo.py` builds three document shapes:
- `movie.nfo`-equivalent (named `<video-filename>.nfo`, Kodi's own
  recommended convention for movies in their own folder): title,
  originaltitle, year, plot, tagline, runtime, premiered, genre*, studio*,
  director*, credits* (writers), actor* (name/role/order), uniqueid*.
- `tvshow.nfo` at the series-folder root: title, plot, premiered, mpaa,
  genre*, studio*, uniqueid*. Only written once per series (checked via
  `tvshow_nfo_path.exists()` before generating it) so re-running `organize`
  against more episodes of an already-organized show doesn't keep
  regenerating it.
- Episode `<episodedetails>` NFO alongside each episode file: title,
  showtitle, season, episode, plot, aired, director*, credits*, uniqueid.

Artwork file names (`poster.jpg`, `fanart.jpg`) match the Kodi/Jellyfin
convention for folder-level artwork -- both platforms look for exactly
these names at the movie/series folder root without needing anything in
the NFO to point at them.
