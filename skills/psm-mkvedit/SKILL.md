---
name: psm-mkvedit
description: Safely edit Matroska metadata in place with mkvpropedit without remuxing media streams. Use for MKV cover attachments, segment titles, track names and languages, default audio/subtitle/video selection, track flags, tags, or chapters when codec payloads should remain untouched.
---

# MKV Edit

Use `mkvmerge -J` to inspect tracks and attachments, then make metadata-only changes
with `mkvpropedit`. Never use this skill to change codec, dimensions, timing, or
payload-dependent headers.

## Workflow

1. Run `env-check --category mkvedit`.
2. Select tracks explicitly with `uid:N`, `id:N`, or a one-based type ordinal such as
   `audio:1`, `subtitle:2`, or `video:1`.
3. Run without `--yes`; inspect the exact shell-quoted `mkvpropedit` command.
4. Apply with `--yes`. The runner copies each original to
   `<file>.mkvedit.bak`, validates the result with `mkvmerge -J`, and restores the
   original automatically if editing or validation fails -- **except for a
   cover-only edit** (just `--cover`/`--delete-cover`, nothing else in the same
   invocation), which skips the backup entirely: copying an entire multi-GB video
   first just to swap a few-MB poster attachment is pure overhead, not a
   proportionate safety measure. Verification still runs either way; a cover-only
   edit that fails verification just can't be auto-rolled-back (there was nothing
   to roll back to).
5. Keep the backup until playback has been verified (not applicable to cover-only
   edits, which never created one).

```bash
.agents/run.sh mkvedit --path movie.mkv --default-subtitle subtitle:2
.agents/run.sh mkvedit --path movie.mkv --track audio:1 --language en-US
.agents/run.sh mkvedit --path movie.mkv --cover poster.jpg --yes
.agents/run.sh mkvedit --path movie.mkv --title "Movie Title" --clear-all-track-names --yes
```

Track-scoped edits include `--track-name`, `--delete-track-name`, `--language`, and
repeatable `--flag NAME=yes|no`. Supported flags are default, forced, enabled,
original, commentary, hearing-impaired, visual-impaired, and text-descriptions.
Default-track options clear the default flag on other tracks of the same type.

`--clear-all-track-names` deletes the name/label on every track that currently has
one, container-wide, in one invocation -- for scene-release scatter like `"VFF
(forced)"`, `"EN (SDH)"`, or a non-English track title (a real release-metadata
pattern this library hit: Plex derives audio/subtitle track descriptions from each
track's actual codec/language properties, not this free text, so it's noise rather
than something worth keeping per-language). It only touches the `name` property, not
`language` -- track language tags stay untouched, since those are functional, not
cosmetic. Cannot be combined with `--track-name`/`--delete-track-name` in the same
invocation (both would target `name`, in an order-dependent way that's more
confusing than useful). Pair with `--title` to also set the container's own segment
title to the correct title in one edit -- fixes a title tag that's blank, is the raw
release filename, or (seen in this library) is in the wrong language entirely.

Cover changes use conventional `cover.jpg`/`cover.png` attachment names. Tags and
chapters accept XML files or their corresponding delete flags. Runs with no mutation
or contradictory set/delete options are rejected.

**A scene release's `--title` can be silently shadowed by its own `<Tags>` --
check for this, don't assume `--title` alone is enough.** Matroska has two
independent places a title can live: the Segment Info `title` property
(`--edit info --set title=...`, what `--title` sets) and a `TITLE` entry inside
the file's separate `<Tags>` tree (`ffprobe`/players read `format.tags.title`,
which several muxers populate from *either* source and can prefer the Tags one).
Confirmed on this library: an 18-episode Arcane release carried a full French/
English-mixed Tags block (`SYNOPSIS` in French, plus `COLLECTION`, `COMMENT`,
`DATE_RECORDED`/`DATE_RELEASED`/`DATE_TAGGED`, `TMDB`, `TVDB`, `IMDB`, a
redundant `TITLE`, ...) -- after setting `--title` via `--edit info`,
`ffprobe -show_entries format_tags=title` still showed the *old*, differently-
cased Tags value until `--delete-tags` also ran. Different releases scatter
this across different shapes -- Kandahar/RED 2 carried a flat
`ACTORS`/`COMPOSER`/`DIRECTOR`/... tag set, Westworld a hierarchical
`EPISODE/TITLE-eng`/`SEASON/TITLE-eng` one -- so **after any `--title` edit,
verify with `ffprobe -show_entries format_tags` (not just `mkvmerge -J`'s
`container.properties.title`, which only reflects the Segment Info side) and
run `--delete-tags` if anything besides `title` shows up.** `--delete-tags`
only clears the `<Tags>` tree; it does not touch Segment Info fields
(`title`, `muxing_application`, `date_utc`, ...) or attachments, so it's safe
to pair unconditionally with a `--title`/`--clear-all-track-names` cleanup
pass regardless of whether a given file actually has extra Tags to remove.
