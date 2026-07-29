---
name: media-organizer
version: 0.1.0
description: >
    Use when the user wants to identify, rename, and organize video files
    (movies/TV episodes sitting in a downloads/inbox folder) into a Plex or
    Jellyfin-standard library layout - the FileBot-equivalent job, done via
    free APIs instead of a license: TMDB for identification/artwork/NFO
    metadata, OpenSubtitles for subtitles. Reads all configuration (API
    keys, inbox/library directories, target server convention) from a .env
    file, and is safe to run unattended from an automated harness (dry-run
    default, confidence-gated matching, never overwrites an existing
    destination). Triggers on phrases like "organize my downloads",
    "rename to Plex/Jellyfin convention", "identify these movies/episodes",
    "fetch posters/NFO/subtitles", "FileBot alternative", or any request to
    sort unorganized media files into a proper library structure.
allowed-tools:
    - Bash
metadata:
    author: "media-organizer"
    focus: "TMDB-based media identification, Plex/Jellyfin renaming, NFO/artwork/subtitle fetching"
---

# media-organizer

Does what FileBot does -- identify a video file, rename/move it into a
Plex- or Jellyfin-standard layout, and fetch artwork/NFO/subtitles for it --
without a FileBot license, using free APIs directly: TMDB (themoviedb.org)
for identification, artwork, and metadata; OpenSubtitles.com for subtitles.
See `reference/naming-conventions.md` for exactly how FileBot's own pipeline
works and why this skill only needs TMDB (not TheTVDB, which stopped
offering a no-strings-attached free API tier -- see that doc for the
specifics), and `reference/apis.md` for the full endpoint reference both
clients were built against.

The toolkit lives at `scripts/mediaorganizer.py` (next to this file), a
Python package wrapping `guessit` (filename parsing -- the one third-party
dependency this repo has; see "Why guessit" below), `urllib` (TMDB/
OpenSubtitles HTTP calls), and `xml.etree.ElementTree` (NFO generation).

## Setup

Copy `.env.example` to `.env` (or anywhere, and pass `--env-file`) and fill
in at minimum `MEDIAORGANIZER_TMDB_API_KEY` (free -- themoviedb.org account
-> Settings -> API), `MEDIAORGANIZER_INBOX_DIR`, `MEDIAORGANIZER_MOVIES_DIR`,
`MEDIAORGANIZER_TV_SHOWS_DIR`. Every key is documented in `.env.example`;
the real process environment always overrides `.env`, so an automated
harness can inject values without touching the file.

**This repo has no system `pip`.** Run via `uv` (already installed on this
machine), either of:
```bash
uv run <path-to-this-skill>/scripts/mediaorganizer.py organize [options]
```
(the script's inline PEP 723 metadata tells `uv` to provision `guessit`
automatically -- no separate install step), or the conventional way if a
system with `pip` is used instead:
```bash
pip install -r requirements.txt   # from the .agents repo root
python3 scripts/mediaorganizer.py organize [options]
```

## Running

```bash
uv run scripts/mediaorganizer.py organize --env-file .env [options]
```

**Defaults to a dry run** that parses and identifies every file under the
inbox and prints exactly what it would do (destination path, confidence,
NFO/artwork/subtitle plan) without touching anything. Pass `--yes` to
actually execute.

```bash
# dry run (safe, default) -- always do this first
uv run scripts/mediaorganizer.py organize --env-file .env

# scope to one subdirectory of the inbox first
uv run scripts/mediaorganizer.py organize --env-file .env --path "Some Show"

# execute for real
uv run scripts/mediaorganizer.py organize --env-file .env --path "Some Show" --yes

# first time trying this against real files: copy instead of move, so the
# inbox file is still there if something looks wrong
uv run scripts/mediaorganizer.py organize --env-file .env --yes --copy
```

## Confidence-gated matching -- there is no safe fallback here

Every file gets a 0.0-1.0 confidence score (title similarity + a year-match
bonus/penalty, see `matching.py`). Below `MEDIAORGANIZER_MIN_CONFIDENCE`
(default 0.75), the file is left alone and reported as `[REVIEW]` rather
than acted on. This is stricter than media-library's safety nets elsewhere
in this repo: a stripped audio track always has a fallback ("keep the
original track"), but a misidentified movie renamed to the wrong title has
no equivalent "leave it as it was" -- so low-confidence matches are never
auto-applied, full stop. Files already carrying an explicit ID tag in their
name (e.g. downloaded as `Movie Name (2020) {tmdb-12345}.mkv`) skip search
entirely and go straight to that ID at confidence 1.0.

## What actually happens on `--yes`

1. Fetch full details (overview, cast/crew, genres, external IDs) for the
   resolved TMDB id.
2. Build the destination path per `MEDIAORGANIZER_SERVER` (`plex` or
   `jellyfin` -- see `reference/naming-conventions.md` for the exact
   folder/file/ID-tag conventions each one uses).
3. Refuse outright if the destination already exists (never silently
   overwrites/clobbers -- reported as an `[ERROR]`, nothing is touched).
4. Write the NFO (`<video>.nfo`, plus `tvshow.nfo` the first time a series
   folder is created), download `poster.jpg`/`fanart.jpg` from TMDB if
   available, and fetch a subtitle per `MEDIAORGANIZER_SUBTITLE_LANGUAGES`
   from OpenSubtitles (skipped entirely if no OpenSubtitles API key is
   configured -- renaming/NFO/artwork still happen either way).
5. Move (or, with `--copy`, copy) the source file into place last, only
   after every other artifact above succeeded.

## Why guessit (the one third-party dependency in this repo)

Every other skill here is stdlib-only. Parsing scene-release filenames
(title vs. year vs. season/episode vs. release-group tags, in whatever
order and bracket convention a given release uses) is exactly the
multi-year accumulated-edge-case problem FileBot's own matching engine
solves -- a from-scratch regex parser would only ever cover a fraction of
what `guessit` already handles (anime bracket conventions, multi-episode
files, unusual tag ordering). `parse.py` is the only module that imports
it, isolating that dependency behind a narrow, stable interface.

## A note on what's verified vs. not

`oshash.py`'s algorithm, every naming/NFO-building rule, and the full
plan-building pipeline (`organize.py`) are unit-tested and were additionally
smoke-tested end-to-end with mocked TMDB responses standing in for real
network calls (see module docstrings for specifics) -- this environment has
no TMDB/OpenSubtitles account to test the actual live HTTP calls with. Both
clients (`tmdb.py`, `opensubtitles.py`) are built directly against each
service's own published API reference (`reference/apis.md`), but treat
them as reviewed-but-live-untested until run against a real key.

## Extending

`scripts/mediaorganizer/` is small and modular: `config.py` (`.env` loading),
`parse.py` (the one guessit-touching module), `matching.py` (pure
confidence scoring -- no I/O, fully unit-tested), `naming.py` (pure
Plex/Jellyfin path building), `nfo.py` (pure Kodi-compatible NFO XML
building), `tmdb.py` / `opensubtitles.py` (the two HTTP clients),
`oshash.py` (the OpenSubtitles file-hash algorithm, exposed on
`OpenSubtitlesClient.search()` for hash-based matching but not used by the
default TMDB-id-based search), `organize.py` (orchestration: identify ->
plan -> execute), `cli.py` (argparse). Run `test_*.py` (see `AGENTS.md` at
the repo root for lint/type-check/test commands -- note this skill's tests
need `guessit` installed, e.g. via `uv run --with guessit -m pytest ...` or
a `uv venv`/`uv pip install -r requirements.txt -r requirements-dev.txt`
project venv) after touching any pure-logic module.
