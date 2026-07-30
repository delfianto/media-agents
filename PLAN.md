# IN PROGRESS: split media-organizer into organize / artwork / subtitle

Status: **planning complete, implementation not started** (stopped before any
code was written/moved — repo is clean, nothing to undo). Delete this file
once the split below is fully implemented, tested, and its own docs/SKILL.md
are in place — it's a working handoff doc, not permanent repo documentation.

## Why

`media-organizer` currently does four unrelated jobs in one pipeline: parse a
messy inbox filename (guessit), match it against TMDB with confidence
scoring, rename/move it into Plex/Jellyfin layout, **and** write NFO, fetch
poster/fanart, and fetch OpenSubtitles — all in one `organize` command, one
`Plan` object, one execution pass. User wants this split into three
independent skills so artwork/subtitles can be fetched/refreshed on their own
schedule against an *already-organized* library, without redoing
identification every time. User also wants `organize` to gain `--overwrite`
(confirmed: does not exist today anywhere in this skill).

## Decisions already made (do not re-litigate these)

1. **New skill names**: `organize`, `artwork`, `subtitle` (all lowercase,
   single word, matching `analyze`'s style — user picked these directly).
2. **Match handoff**: `artwork`/`subtitle` read the TMDB id back off an
   **already-organized** file/folder name rather than re-running
   guessit+TMDB-search+confidence-scoring themselves. `organize` is a hard
   prerequisite — it must run first. (User picked this explicitly over each
   skill independently re-matching.)
3. **NFO ownership**: `artwork` writes the `.nfo` file (same TMDB detail
   call as the images, avoids a second TMDB round trip). (User picked this
   explicitly over `organize` owning NFO.)
4. **Tag-reading mechanism — my own design decision, not literally asked but
   strongly justified, revisit only if it turns out wrong**: do **not** use
   guessit in `artwork`/`subtitle` to re-read the tag. guessit is already
   confirmed (tested live) to parse both `{tmdb-12345}` and `[tmdbid-12345]`
   back out, but using a heuristic NLP-ish parser to re-read a tag *this same
   codebase writes in a rigid, known format* is fragile and pulls in a
   dependency neither skill otherwise needs. Instead: add a small
   dependency-free `extract_provider_id(name: str) -> int | None` regex
   function living next to `naming.py`'s existing `_id_tag()` writer (single
   source of truth for the tag format). Season/episode for TV can similarly
   be regex-read from the episode file's own standard `SxxEyy`-shaped name
   (Plex: `s01e01`, Jellyfin: `S01E01`) — no guessit needed there either.
   Net effect: only `organize` depends on guessit; `artwork`/`subtitle`
   become 100% stdlib.
5. **Shared TMDB credential**: `organize` and `artwork` both need a TMDB key.
   Rather than force the user to paste the same key into two `.env`s, each
   skill checks its own `<PREFIX>_TMDB_API_KEY` first, then falls back to
   unprefixed `TMDB_API_KEY`. This mirrors the `TRACKSTRIP_ROOT` (own) /
   `MEDIALIB_ROOT` (shared fallback) pattern already shipped this session for
   the library-root env var — same shape, same rationale (don't let a
   per-skill-prefixed name orphan a value other skills also need).
   `OPENSUBTITLES_*` stays under `SUBTITLE_*` only — single consumer now, no
   sharing need.
6. **`--overwrite` behavior (new, doesn't exist today anywhere in this
   skill)**: default unchanged (destination exists -> refuse, nothing
   touched). With `--overwrite`: **back up the existing destination file**
   (not the source) before replacing it — mirrors `av1-transcode`/
   `track-strip`'s backup-before-replace convention, per this repo's
   "prefer backing up over deleting" rule (see root `AGENTS.md`).
7. **CLI shape**: all three new skills drop the vestigial single-subcommand
   layer (today's `mediaorganizer organize --path ...`) in favor of the flat
   flag style `analyze`/`env-check` already use: `organize --inbox ...
   --yes`, `artwork --path ... --type poster`, `subtitle --path ...
   --language eng`. All three keep dry-run-by-default + `--yes`-to-execute.
8. **`artwork --type`**: `all` (default) | `poster` | `fanart` | `still`
   (episode thumbnail) | `nfo`. Optional `--tmdb-id N` override for a
   single `--path`-scoped file that predates the tag convention (i.e. no tag
   to read).

## Exact current-state facts (verified by reading every file directly —
## use these, don't re-derive them)

### Module inventory of `skills/media-organizer/scripts/mediaorganizer/`

| File | Moves to | Notes |
|---|---|---|
| `config.py` | split 3 ways (see below) | `Config` dataclass, `ConfigError`, `load_config()`, prefix `MEDIAORGANIZER_` |
| `parse.py` | `organize` only | guessit wrapper, only file that imports guessit |
| `matching.py` | `organize` only | pure confidence scoring, no I/O, `MIN_AUTO_CONFIDENCE = 0.75` |
| `naming.py` | **`lib/medialib/naming.py`** | pure path builders, 100% dependency-free (dataclasses+pathlib only) — add `extract_provider_id()` here |
| `nfo.py` | `artwork` only | pure XML string building, no I/O |
| `tmdb.py` | **`lib/medialib/tmdb.py`** | stdlib `urllib` HTTP client, 100% dependency-free — needed by both `organize` (search+details) and `artwork` (details+images) |
| `opensubtitles.py` | `subtitle` only | stdlib `urllib` HTTP client |
| `oshash.py` | `subtitle` only | stdlib hash algorithm, currently unused/dead in the pipeline (wired into `OpenSubtitlesClient.search(moviehash=...)` but never called) — move as-is, no obligation to wire it up |
| `organize.py` | becomes `organize/plan.py` (renamed — `organize/organize.py` would be a confusing self-referential name) | **must be split**: keep parse/identify/details-for-naming/naming/exec-mkdir/exec-move steps; cut nfo/poster/fanart/subtitle steps entirely |
| `cli.py` | split 3 ways | argparse, currently `prog="mediaorganizer"`, one subcommand `organize` |
| `__main__.py` | 3 copies, `artwork`/`subtitle` drop the PEP 723 block entirely (no guessit dep) | current one already correctly lives at `mediaorganizer/__main__.py` (this session's earlier entrypoint-relocation work already applies — don't redo that part, just replicate the *shape*) |

### `naming.py` — exact tag format (already shipped, do not change)

```python
def _id_tag(server: str, tmdb_id: int) -> str:
    return f"{{tmdb-{tmdb_id}}}" if server == "plex" else f"[tmdbid-{tmdb_id}]"
```
- Plex: `{tmdb-12345}` on folder AND file (movies), folder only (series). Episode files: `Series - s01e01 - Title.ext` (lowercase, no tag).
- Jellyfin: `[tmdbid-12345]` on folder AND file (movies), folder only (series). Episode files: `Series (Year) S01E01 Title.ext` (uppercase, space-separated, no tag).
- Full function list to move as-is: `sanitize_title`, `_check_server`, `_id_tag`, `MovieMeta`, `EpisodeMeta`, `movie_base_name`, `movie_folder`, `movie_video_path`, `series_base_name`, `series_folder`, `season_folder`, `episode_base_name`, `episode_video_path`, `sidecar_path`, `poster_path`, `fanart_path`, `tvshow_nfo_path`, `subtitle_path`. `VALID_SERVERS = ("plex", "jellyfin")`.
- **New function to add**: `extract_provider_id(name: str) -> int | None` — regex both `\{tmdb-(\d+)\}` and `\[tmdbid-(\d+)\]`, return the int or None. Add tests: both styles, absent tag, malformed tag (e.g. `{tmdb-}`, `[tmdbid-abc]`).

### `tmdb.py` — exact methods to move as-is

`TmdbClient(api_key, user_agent, timeout=15.0)`: `search_movie(query, year=None)`, `search_tv(query, first_air_date_year=None)`, `movie_details(tmdb_id)` (uses `append_to_response="external_ids,credits"`), `tv_details(tmdb_id)` (`append_to_response="external_ids"`), `episode_details(tv_id, season, episode)` (`append_to_response="credits"`), `image_url(image_path, size="original")` (staticmethod), `download_image(image_path, dest, size="original") -> bool`. `TmdbError(RuntimeError)`. `BASE_URL = "https://api.themoviedb.org/3"`, `IMAGE_BASE_URL = "https://image.tmdb.org/t/p"`.
**Note**: `download_image`'s except clause is already the py3.14 bare-comma form (`except urllib.error.HTTPError, urllib.error.URLError:`) — keep as-is when moving, don't "fix" it back to tuple form.

### `organize.py` → `organize/plan.py` split (exact pipeline today)

**Plan-building** (`build_movie_plan`/`build_episode_plan`, both `(cfg, tmdb, source) -> Plan | OrganizeResult`):
1. `parse(source)` → `ParsedName` — **keep**
2. `identify_movie`/`identify_series` — if `parsed.tmdb_id` already set, skip search (`(id, 1.0, "tmdb id already embedded in filename")`); else `tmdb.search_movie`/`search_tv` → `matching.best_match` — **keep**
3. No confident match → `OrganizeResult(status="review")` — **keep**
4. `tmdb.movie_details`/`tv_details`+`episode_details` — **keep** (organize still needs canonical title/year for the destination name — this is NOT purely an artwork concern even though artwork also needs it independently later)
5. Build `naming.MovieMeta`/`EpisodeMeta` → destination folder/video path — **keep**
6. Build NFO data/content — **CUT** (moves to `artwork`, which independently refetches details)
7. Compute poster/fanart raw+dest paths — **CUT** (moves to `artwork`)
8. Compute `subtitle_plan` — **CUT** (moves to `subtitle`)
9. Return `Plan` — **keep, but drop nfo_path/nfo_content/tvshow_nfo/poster_*/fanart_*/subtitle_plan fields**

**Execution** (`execute_plan(plan, tmdb, opensubs, copy_instead_of_move=False)`, exact current order):
1. Refuse if `plan.video_path.exists()` — **keep as default; add `--overwrite` → back up existing destination first, then proceed** (new)
2. `mkdir(parents=True, exist_ok=True)` — **keep**
3. Write `tvshow.nfo` — **CUT**
4. Download poster — **CUT**
5. Download fanart — **CUT**
6. Write NFO — **CUT**
7. `_fetch_subtitles` — **CUT**
8. `shutil.move`/`shutil.copy2` — **keep**, now runs right after mkdir (no more metadata steps between)
9. Return `OrganizeResult` — **keep**, drop the `tmdb`/`opensubs` params from the signature entirely (no longer needed post-cut)

### `cli.py` — exact current flags (for parity when rebuilding `organize`'s CLI)

`--env-file` (default `.env`), `--inbox` (override), `--path` (substring), `--limit`, `--yes`, `--copy`. **Add**: `--overwrite`. No confidence/subtitle-lang/server override flags exist today (env-var only) — fine to leave as-is unless it comes up.

### `.env` keys today — all in `config.py`, prefix `MEDIAORGANIZER_`, real env always wins over file

`TMDB_API_KEY` (required), `SERVER` (optional, default `plex`, must be `plex`/`jellyfin`), `MOVIES_DIR` (required), `TV_SHOWS_DIR` (required), `INBOX_DIR` (required), `OPENSUBTITLES_API_KEY`/`USERNAME`/`PASSWORD` (all optional), `SUBTITLE_LANGUAGES` (optional, default `en`, comma-split+stripped), `MIN_CONFIDENCE` (optional, default `matching.MIN_AUTO_CONFIDENCE` = 0.75), `USER_AGENT` (optional, default `"media-organizer/0.1"`).

**New split**:
- `organize`: `ORGANIZE_TMDB_API_KEY`→`TMDB_API_KEY` (required, shared-fallback pattern), `ORGANIZE_SERVER` (default `plex`), `ORGANIZE_MOVIES_DIR`, `ORGANIZE_TV_SHOWS_DIR`, `ORGANIZE_INBOX_DIR` (all required), `ORGANIZE_MIN_CONFIDENCE` (default 0.75), `ORGANIZE_USER_AGENT` (default `"organize/0.1"`).
- `artwork`: `ARTWORK_TMDB_API_KEY`→`TMDB_API_KEY` (required, shared-fallback), `ARTWORK_USER_AGENT` (default `"artwork/0.1"`). Needs to know `SERVER` too (plex vs jellyfin tag style + poster/fanart path convention) — reuse `ARTWORK_SERVER` (default `plex`), independent of organize's own copy (a library could theoretically mix, though unlikely — keep it simple, don't try to auto-detect).
- `subtitle`: `SUBTITLE_OPENSUBTITLES_API_KEY` (optional — no subs fetched without it, not a hard error), `SUBTITLE_OPENSUBTITLES_USERNAME`/`PASSWORD` (optional), `SUBTITLE_LANGUAGES` (default `en`), `SUBTITLE_USER_AGENT` (default `"subtitle/0.1"`), `SUBTITLE_SERVER` (default `plex`, needed to know the tag style when reading it back — same reasoning as artwork).

### Tests — exact moves (unique-basename rule applies repo-wide)

- `test_naming.py` → `lib/test_naming.py` (collision check: none exists yet) + new `extract_provider_id` cases.
- `test_config.py` → **three separate files**, `test_organize_config.py` / `test_artwork_config.py` / `test_subtitle_config.py` (same basename three times would collide with each other, not just precedent — this is a *new* collision, not just the old repo-wide-uniqueness rule).
- `test_matching.py`, `test_parse.py` → stay with `organize`, unchanged content.
- `test_nfo.py` → moves to `artwork`, unchanged content.
- `test_oshash.py` → moves to `subtitle`, unchanged content.
- New: tests for `--overwrite` backup-then-replace, `extract_provider_id`, `artwork --type` selection.

### Reference docs

`reference/naming-conventions.md` and `reference/apis.md` — the ID-tag section is now genuinely shared (organize writes, artwork/subtitle read). Simplest: keep as canonical under `organize/reference/`, link from `artwork`/`subtitle` `SKILL.md`s rather than duplicating. `apis.md`'s TMDB section is relevant to both `organize` and `artwork`; OpenSubtitles section relevant to `subtitle` only — could split apis.md in two, or keep it one file under `organize/reference/` and link from both — decide during implementation, not critical.

## Cross-repo updates needed (same shape as the recent track-strip rename —
## see that commit, `1a007d6`, for the exact pattern to replicate)

- `env-check/scripts/envcheck/checks.py`: split `media-organizer` category
  into `organize` (guessit + TMDB key required-check), `artwork` (no new
  required check — shared TMDB key already covered by organize's check,
  unless env-check should check it independently too — decide during
  implementation), `subtitle` (optional check for
  `SUBTITLE_OPENSUBTITLES_API_KEY`).
- `requirements.txt` comment (currently says "media-organizer needs
  guessit" — becomes "organize needs guessit").
- `README.md` skill table: 3 new rows replacing 1, keep alphabetical order
  (`artwork`, `organize`, `subtitle` interleave among the existing
  `analyze`/`av1-transcode`/`env-check`/`stash-app`/`track-strip` rows).
- `AGENTS.md` skill-package list (currently lists `media-organizer` among
  the "real logic" multi-module packages).
- Root `conftest.py` if it names the old path (check — last session's
  track-strip rename found one stale reference here for that skill, worth
  checking for media-organizer too).
- Delete `skills/media-organizer/` entirely once `organize`/`artwork`/
  `subtitle` are all built and verified — don't leave it dangling half-migrated.

## Verification (once implemented)

- `ruff check .`, `ruff format .`, `basedpyright .`, `pytest` (same
  pre-existing guessit-missing gap as every prior round this session — not
  a regression, `env-check` already flags it).
- Live smoke test: each of the three new entrypoints' `--help`; a dry-run of
  `organize` against a real inbox-shaped path if one exists; confirm
  `env-check --category organize` / `--category artwork` / `--category
  subtitle` all resolve correctly; if there's a real already-organized movie
  folder in the library, dry-run `artwork`/`subtitle` against it and confirm
  `extract_provider_id` actually reads the real embedded tag correctly.

## Where to resume

Nothing has been written yet — start at "Move tmdb.py and naming.py into
lib/medialib" (session task #12 from the session that wrote this plan).
Re-read this file fully before starting; it supersedes needing to
re-explore `skills/media-organizer/` from scratch.
