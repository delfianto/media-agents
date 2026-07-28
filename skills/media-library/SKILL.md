---
name: media-library
version: 0.2.0
description: >
    Use when the user wants to inspect or clean up this Plex media library
    (Movies/ and TV Shows/) - report video/audio/subtitle codec and language
    statistics, find files with non-English audio or subtitle tracks (or a
    specific audio codec like DTS), remux (strip) tracks out, drop redundant
    audio tracks, trim to a single audio track, drop SDH subtitles, or
    transcode a codec that doesn't play on some device (e.g. DTS muted over
    eARC) to a compatible one. Triggers on phrases like "codec stats",
    "language stats", "non-English tracks", "DTS compatibility", "strip
    subtitles/audio", "clean up the media folder", "how many audio streams",
    "SDH subtitles", or any request to remux/transcode audio or subtitles in
    this directory.
allowed-tools:
    - Bash
metadata:
    author: "media-library"
    focus: "Plex library codec/language auditing and safe track-level remuxing"
---

# media-library

This Plex library (`Movies/` and `TV Shows/`) is inspected and maintained by a
zero-dependency Python toolkit at `scripts/mediatools.py` (next to this file).
It wraps `ffprobe`, `ffmpeg`, and `mkvmerge`. Track selection (`apply`) never
re-encodes - it only remuxes (stream-copies) files to add/remove tracks.
`transcode` is the one exception: it re-encodes only the audio streams that
need it (video is always stream-copied), used when a codec itself is the
problem (e.g. DTS not decoding over eARC on some LG TVs) rather than the
track's language.

See `reference/incidents.md` for the full incident history (real bugs found
auditing this exact library, and the fixes) behind every safety rule below -
read it before changing verification/fallback logic in `scripts/mediatools/`,
since several of these were subtle enough to ship once already.

Only files inside `Movies/`/`TV Shows/` subdirectories are ever touched -
anything sitting loose directly at the library root (e.g. a freshly added
file not yet sorted) is invisible to every subcommand by design.

## Running commands

Always invoke via `python3`, from anywhere (paths default to this library
root automatically - see "Path resolution" below):

```bash
python3 <path-to-this-skill>/scripts/mediatools.py <subcommand> [options]
```

Subcommands, in the order you'd normally use them:

1. **`scan`** - Probe every media file with `ffprobe` and update the JSON
   cache at `<library-root>/.cache/mediatools/scan.json`. Read-only.
   Incremental (skips files whose size+mtime match the cache) unless
   `--force` is passed.
   ```bash
   python3 scripts/mediatools.py scan --jobs 8
   ```

2. **`stats`** - Print codec and language statistics from the cache: video
   codec/profile/resolution breakdown, audio codec + lossless/lossy split,
   subtitle codec breakdown, per-language track counts, how many files have
   non-English audio/subtitles, and an estimate of what the default policy
   would strip. Read-only; requires `scan` to have run at least once.
   ```bash
   python3 scripts/mediatools.py stats
   ```

3. **`plan`** - Fast, cache-based preview of exactly which tracks `apply`
   would drop per file, with reasons. Read-only.
   ```bash
   python3 scripts/mediatools.py plan [--path "substring"] [--limit N]
   ```

4. **`apply`** - Remux files to drop non-English audio/subtitle tracks (plus
   whatever other policy flags are set - see the table below). **Defaults to
   a live, authoritative dry run** (re-probes each file fresh and prints the
   exact `mkvmerge`/`ffmpeg` command it would run, touching nothing). Pass
   `--yes` to actually execute.
   ```bash
   # authoritative dry run (safe, default)
   python3 scripts/mediatools.py apply --path "Some Show"

   # execute for real, for one show first
   python3 scripts/mediatools.py apply --path "Some Show" --yes

   # execute for the whole library
   python3 scripts/mediatools.py apply --yes
   ```

5. **`transcode`** - Re-encode audio tracks of a given codec to a more
   compatible one; video and every other track are always stream-copied
   untouched. For codec-level playback problems rather than language ones -
   e.g. DTS/DTS-HD MA is muted on some LG TVs over eARC (confirmed on this
   library: LG has full Dolby licensing for AC-3/E-AC-3/TrueHD/Atmos, but
   doesn't license DTS decode in most webOS firmware). Same dry-run-by-default
   and verify-then-swap safety model as `apply`.
   ```bash
   python3 scripts/mediatools.py transcode --path "Some Show"                    # dry run
   python3 scripts/mediatools.py transcode --path "Some Show" --yes --no-backup  # execute
   # defaults: --from-codec dts --to-codec eac3 --bitrate 640k
   ```
   Also use `--drop-audio-codec CODEC` on `plan`/`apply` for files where the
   problem codec has a working fallback already present (e.g. DTS-HD MA
   alongside a TrueHD or AC3 track) - a plain track drop rather than a
   transcode, since no compatible audio would be lost. The safety net
   doubles as protection here: run `apply --drop-audio-codec dts`
   library-wide and it will only ever affect files that have another
   *usable* audio track to fall back to - files where the flagged codec is
   the only real audio come back `unchanged` automatically, no need to
   `--path`-filter them out by hand. "Usable" specifically excludes
   commentary tracks (see the Prometheus incident in `reference/incidents.md`
   - a real data-loss case this guards against now).

6. **`purge-backups`** - Once you've confirmed the remuxed files play fine,
   permanently delete the backed-up originals to reclaim disk space.
   ```bash
   python3 scripts/mediatools.py purge-backups         # shows size, asks for --yes
   python3 scripts/mediatools.py purge-backups --yes    # actually deletes
   ```

## Path resolution

`scripts/mediatools.py` finds the library root by walking up from its own
location looking for an ancestor directory named `.agents`, then using
*that* directory's parent - i.e. wherever this repo is checked out into.
Override with `--root` or the `MEDIATOOLS_ROOT` env var if invoking against
a different library. The scan cache and backup directory default to
`<library-root>/.cache/mediatools/` - outside this repo, since they're
disposable runtime state, not source.

## Track-keep policy (what counts as "non-English")

Default policy, tunable per-invocation on `stats`/`plan`/`apply`:

| Flag | Default behavior | With the flag |
|---|---|---|
| `--strip-unknown` | Tracks tagged `und`/unknown language are **kept** | Also stripped |
| `--strip-forced` | Forced subtitles are **kept** regardless of language (they're usually the essential foreign-dialogue translation lines in an otherwise English film) | Non-English forced subs also stripped |
| `--strip-commentary` | English commentary audio tracks are **kept** | Commentary tracks stripped too |
| `--no-detect-anime` | Anime handling (below) is **on** | Disabled - every file uses the English-first policy |
| `--drop-audio-codec CODEC[,CODEC...]` | No codec-based dropping (default `""`) | Drops audio tracks of the given ffprobe codec_name(s) (e.g. `dts`) regardless of language, but only where another *non-commentary* audio track survives |
| `--single-audio-track` | Keep every audio track the language/codec policy allows | Keep only one: prefers non-commentary, then the default-flagged track. Drops downmixes/duplicate-master extras |
| `--drop-sdh` | Keep every subtitle track the language policy allows | Drop SDH subtitle tracks when a plain (non-SDH) sibling of the same language *and codec* survives - see detection details below |

Video tracks are never touched. At least one *non-commentary* audio track is
always kept whenever the file has one, even if none match the policy (falls
back to the file's original default/first primary track) - `apply` cannot
produce a silent file, and cannot leave a file with only commentary tracks
either (see the Prometheus incident in `reference/incidents.md`).

### Anime handling (Japanese-original releases)

A file is treated as a Japanese-original release - and gets the **opposite**
policy - if it has a Japanese audio track *and* two or fewer audio tracks
total (Japanese alone, or Japanese + one English dub). For these files:

- **Audio:** keep Japanese, drop English (and anything else).
- **Subtitles:** keep English *and* Japanese, drop every other language.

This threshold matters: a file with Japanese audio buried among a dozen+
other dub tracks (e.g. a Netflix live-action show also dubbed into Japanese,
such as *Daredevil - Born Again* or *The Rings of Power* in this library) is
**not** anime by this test and stays on the plain English-first policy -
only genuine JP-original/dubbed-anime releases (e.g. *SPY x FAMILY*, *Tales
of Wedding Rings*) match the &le;2-track shape. `plan`/`apply` print
`[anime: keep JP audio, EN+JP subs]` next to any file classified this way,
so it's always visible before you commit to `--yes`. Disable entirely with
`--no-detect-anime` if you'd rather every file used the plain English rule.

### `--single-audio-track`: reducing to one audio track

Many releases carry multiple same-language audio tracks that survive the
language policy untouched: a stereo downmix alongside the surround mix, a
legacy AC-3 core alongside a lossless TrueHD/DTS-HD MA track, or (seen on
this library, e.g. *The Rings of Power*) multiple same-spec English tracks
that are genuinely different encodes/masters with no metadata distinguishing
them. `--single-audio-track` reduces these down to one: prefer a
non-commentary track, then whichever one the source flagged `default`, else
the first. This composes with the commentary-safety guarantee above rather
than bypassing it.

### `--drop-sdh`: dropping SDH subtitles

Detects SDH ("Subtitles for the Deaf and Hard-of-hearing" - adds speaker
labels and bracketed sound-effect/music cues) via, in order: the
`hearing_impaired` disposition flag, an "SDH"/"hearing impaired"/"deaf" title,
and - only when neither signal is present - a bounded byte-size comparison
(a real SDH track runs measurably but moderately larger than a plain
sibling for the same file). The size heuristic is deliberately narrow: it
only fires on exactly two candidates of the *same subtitle codec* (never
mix PGS bitmap sizes against SubRip text sizes) in *English or Japanese*
(never compare across an unrelated "unknown language" bucket), excluding
anything titled "Forced"/"Commentary" or naming a different language, with
the ratio bounded to 1.10-3.0x. Every one of these restrictions closes a
real false positive found auditing this library by hand before running
anything for real - see `reference/incidents.md` for the specific files that
exposed each one. Never drops every subtitle track for a language it
actually has, same principle as the audio safety net.

## Safety model - read before running `apply --yes`

- `apply` without `--yes` **never writes anything**, even though it does a
  full live re-probe per file - safe to run as often as you like.
- When executing, each file is remuxed to a temp file next to the original
  first, then verified (has video, has audio, duration matches within 2%,
  decodes cleanly at the head) *before* the original is touched. See
  `reference/incidents.md` for why the decode check is head-only and
  whitelists one specific benign ffmpeg message.
- Language tags are written explicitly on every kept audio/subtitle track
  during muxing (`--language`/`-metadata:s:*:N language=`), fixing the
  blank/`und` tags common on this library's releases.
- Originals are moved (not deleted) to `.cache/mediatools/originals/<relative
  path>` by default, mirroring the library layout. Use `purge-backups` to
  reclaim that space once you've spot-checked playback. Pass `--no-backup`
  to delete originals immediately instead (verification still gates it -
  the original is only ever touched after its replacement passes every
  check above).
- **Backups do not shrink disk usage - they relocate bytes.** The backup
  move is a same-filesystem rename (free), but until `purge-backups` runs,
  the disk holds both the new file and the full-size backup for every file
  processed so far. Backing up everything and purging once at the end means
  peak extra usage approaches the combined post-remux size of every changed
  file - likely multiple TB for a whole-library run. For a run covering
  most/all of the library, either pass `--no-backup` (each original is
  freed immediately once verified) or purge in small batches (`apply --path
  "<show>" --yes` then `purge-backups --yes`, repeated) instead of one
  backup-everything-then-purge-once pass. See `reference/incidents.md` for a
  case where this genuinely came close to filling the disk.
- The backing disk is a single spinning HDD - `apply`/`transcode` process
  files sequentially by design (no `--jobs` flag) to avoid seek-thrashing;
  `scan` parallelizes ffprobe reads instead since those only touch small
  headers.
- **Always confirm with the user before running `apply`/`transcode --yes`
  against the real library** (as opposed to a `--path`-filtered subset or a
  dry run) - it is a bulk, semi-destructive operation across the user's real
  Plex content, even with backups enabled.

## Common tasks

- "What codecs do I have?" / "non-English audio stats" -> `stats`
- "What would get stripped from *Show X*?" -> `plan --path "Show X"`
- "Strip non-English tracks from *Show X*" -> confirm scope with the user,
  then `apply --path "Show X" --yes`
- "Clean up the whole library" -> confirm with the user first (large,
  semi-destructive), then `apply --yes` (consider a `--path` per top-level
  show/movie loop instead of one giant run, so progress survives an
  interruption, and so backups can be purged in batches - see the safety
  model above)
- "Free up the backup space" -> `purge-backups --yes` (only after the user
  has confirmed remuxed files play correctly)

## Extending

The package under `scripts/mediatools/` is small and modular: `langs.py`
(language code table), `scan.py` (ffprobe walk+cache), `track_policy.py`
(the single keep/drop decision function, shared by both strip backends and
by `stats`), `remux_mkv.py` (mkvmerge backend for track selection),
`remux_ffmpeg.py` (ffmpeg backend for mp4/other containers), `transcode.py`
(ffmpeg audio re-encode, the one place that isn't pure stream copy),
`apply.py` (shared orchestration used by both `apply` and `transcode`: temp
file, verify, backup, swap - see `_execute_backend_plan`), `cli.py` (argparse
subcommands). Add new subcommands in `cli.py`. `test_track_policy.py` covers
the policy logic - run it (see `AGENTS.md` at the repo root for the
lint/type-check/test commands) after touching `track_policy.py`.

When adding a new backend, note that `track_policy.from_mkvmerge_track()`
normalizes mkvmerge's audio `codec` field (a free-text description like
"DTS-HD Master Audio" that varies with profile) through its stable
`codec_id` (e.g. `A_DTS`) into the same short name ffprobe uses (`dts`) -
extend `_MKVMERGE_AUDIO_CODEC_ID_MAP` there if you add logic that compares
`codec_name` for a codec not already in that table, or codec-based matching
will silently no-op on `.mkv` files (this exact bug shipped once already -
see `reference/incidents.md`).
