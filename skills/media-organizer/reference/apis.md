# TMDB, OpenSubtitles, and the OpenSubtitles hash algorithm

Both clients (`tmdb.py`, `opensubtitles.py`) are stdlib `urllib` only, built directly against the endpoint shapes below. **Neither has been exercised against the real, live API** -- this environment has no TMDB or OpenSubtitles account/API key to test with. What *is* verified: the request shapes match each service's own published API reference, the response parsing matches their documented JSON shapes, and the full plan-building pipeline (`organize.py`) was smoke-tested end-to-end with mocked client responses standing in for real ones (see the module docstrings). Treat the network-calling code itself as reviewed-but-live-untested until it's run against a real key.

## TMDB (The Movie Database) API v3

- **Auth:** a free API key from an account's Settings -> API page. No subscription, no per-end-user requirement -- this is the whole reason this skill uses TMDB for TV shows too, rather than also integrating TheTVDB (see reference/naming-conventions.md).
- **Base URL:** `https://api.themoviedb.org/3`
- **Search:** `GET /search/movie?query=...&year=...`, `GET /search/tv?query=...&first_air_date_year=...`
- **Details:** `GET /movie/{id}?append_to_response=external_ids,credits`, `GET /tv/{id}?append_to_response=external_ids`, `GET /tv/{id}/season/{s}/episode/{e}?append_to_response=credits` -- `append_to_response` folds what would otherwise be separate requests (cast/crew, external IDs like the IMDb id) into the one call.
- **Images:** response fields like `poster_path`/`backdrop_path` are *relative* paths (e.g. `/abc123.jpg`); the full image URL is `https://image.tmdb.org/t/p/{size}{path}` (`tmdb.py`'s `image_url()` /`download_image()` do this join -- `organize.py`'s `Plan` deliberately stores the raw relative path, not a pre-built URL, so there's exactly one place that does this join rather than two copies that could drift).

## OpenSubtitles.com REST API

- **Auth:** register an "API Consumer" from an opensubtitles.com account's profile to get an API key -- required on every request via the `Api-Key` header. Logging in with a username/password (`POST /login`) is optional and returns a JWT (`Authorization: Bearer <token>`), needed only to raise the daily download quota above the anonymous ceiling.
- **Base URL:** `https://api.opensubtitles.com/api/v1`
- **Quota:** anonymous requests (API key only, no login) get 5 downloads/24h **per IP**; logging in raises this to a tier-dependent 10-1000/day depending on account rank. `opensubtitles.py`'s `login()` is a no-op (not an error) when no username/password is configured -- it just means staying on the anonymous tier.
- **Search:** `GET /subtitles` with any combination of `tmdb_id`, `imdb_id`, `query`, `moviehash`, `languages` (comma-separated ISO 639-1, e.g. `en,es`), `season_number`/`episode_number` (for TV), `type` (`movie`/`episode`). Response: `{"data": [{"id": ..., "attributes": {"files": [{"file_id": ..., "file_name": ...}], ...}}]}` -- this skill always takes the first result's first file, favoring simplicity over picking the "best" of several candidate subtitle files.
- **Download:** `POST /download` with `{"file_id": ...}` (not the search result's `id` -- the nested `file_id` from `attributes.files[]`). Response: `{"link": "<temporary URL, GET it to fetch the actual subtitle>", "file_name": ..., "remaining": <int>, "reset_time": ...}`. Download links are temporary -- request a fresh one right before each actual download rather than caching it.
- **moviehash:** the search endpoint's exact-match parameter for the OpenSubtitles hash (see below) -- not used by this skill's default search (which searches by `tmdb_id` instead, since that's already resolved by the time subtitle search runs), but exposed on `OpenSubtitlesClient.search()` for anyone who wants to add hash-based matching later.

## The OpenSubtitles hash algorithm (`oshash.py`)

`hash = file_size + sum_uint64_le(first 64KB) + sum_uint64_le(last 64KB)`, all arithmetic unsigned 64-bit with natural overflow (wrapping), formatted as a zero-padded 16-character lowercase hex string. Originally from Media Player Classic, adopted by OpenSubtitles as the primary way to look up subtitles for an exact file independent of its (often wrong or missing) filename.

Full spec and official test vectors: [opensubtitles.github.io/oshash](https://opensubtitles.github.io/oshash/). This implementation's own test suite (`test_oshash.py`) couldn't use those exact test vectors directly (they're hashes of specific real video files this environment doesn't have), so it instead verifies the same properties by construction: an all-zero file's hash must equal its size exactly (every summed value is 0), and a crafted all-`0xFF` file's hash is checked against an *independently* computed expected value that exercises the same 64-bit wraparound the real algorithm depends on -- see the test file for the exact derivation. Files under 128KB (131,072 bytes) can't be hashed at all; `compute()` returns `None` rather than raising, since that's a legitimate "this file is too small," not an error.
