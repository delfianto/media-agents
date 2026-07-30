---
name: mkvedit
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
   original automatically if editing or validation fails.
5. Keep the backup until playback has been verified.

```bash
.agents/scripts/run-skill mkvedit --path movie.mkv --default-subtitle subtitle:2
.agents/scripts/run-skill mkvedit --path movie.mkv --track audio:1 --language en-US
.agents/scripts/run-skill mkvedit --path movie.mkv --cover poster.jpg --yes
```

Track-scoped edits include `--track-name`, `--delete-track-name`, `--language`, and
repeatable `--flag NAME=yes|no`. Supported flags are default, forced, enabled,
original, commentary, hearing-impaired, visual-impaired, and text-descriptions.
Default-track options clear the default flag on other tracks of the same type.

Cover changes use conventional `cover.jpg`/`cover.png` attachment names. Tags and
chapters accept XML files or their corresponding delete flags. Runs with no mutation
or contradictory set/delete options are rejected.
