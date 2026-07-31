---
name: organize
description: Identify messy inbox movie and TV filenames with guessit and TMDB, then safely rename and move or copy them into Plex or Jellyfin layouts. Use for organizing new downloads, confidence-gated TMDB matching, provider-ID naming, dry-run move plans, or backing up and replacing an existing organized destination.
---

# Organize

Run identification and naming separately from artwork and subtitle fetching. Leave
low-confidence matches untouched for manual review.

## Workflow

1. Check the environment with `env-check --category organize`.
2. Configure `.env` from the keys below.
3. Run without `--yes` and inspect every planned destination and confidence score.
4. Use `--copy` for the safest first applied run. Use `--overwrite` only when replacing
   an existing destination is intentional; it backs that file up first.
5. Run `artwork` and `subtitle` against the organized library afterward.

```bash
.agents/run.sh organize --env-file .env --inbox /path/to/inbox
.agents/run.sh organize --env-file .env --path "Movie.Name" --copy --yes
```

Configuration uses `ORGANIZE_TMDB_API_KEY` with shared `TMDB_API_KEY` fallback, plus
`ORGANIZE_SERVER`, `ORGANIZE_MOVIES_DIR`, `ORGANIZE_TV_SHOWS_DIR`,
`ORGANIZE_INBOX_DIR`, `ORGANIZE_MIN_CONFIDENCE`, and `ORGANIZE_USER_AGENT`.

Read [`notes/library-naming.md`](../../notes/library-naming.md) when diagnosing path or
provider-tag behavior. Read [`notes/organize-apis.md`](../../notes/organize-apis.md)
for TMDB API context.
