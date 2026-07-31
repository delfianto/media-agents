# AGENTS.md

This repo is a collection of skills for maintaining a self-hosted Plex media library -- codec/language statistics, stripping non-English audio/subtitle tracks, and fixing codec-level playback incompatibilities. This file is the entry point for any coding agent working here; `CLAUDE.md` is a symlink to it.

The executable application is **psammophis**: one installable Python 3.14 package with a single CLI that every skill invokes.

## What's here

```
README.md              skill catalog and usage overview
REFACTOR.md            packaging/progress refactor plan and trackers
pyproject.toml         project metadata, deps, ruff, basedpyright, pytest
uv.lock                locked application + dev dependencies
.python-version        3.14
run.sh                 checkout launcher (also via .agents/run.sh)
src/psammophis/        application package
  cli.py               top-level dispatcher + global options
  runtime/             events, journal, reporters, process/filesystem safety, roots
  medialib/            shared media helpers (walk, dotenv, presets, …)
  analyze/ artwork/ transcode/ envcheck/ mkvedit/
  organize/ compare/ subtitle/ trackstrip/
tests/                 pytest suite mirroring package layout
skills/<name>/SKILL.md skill definition (frontmatter + workflow instructions)
skills/<name>/reference/  supplementary docs SKILL.md points to
mcp_config.json        MCP server(s) skills expect (currently: stash-mcp-server)
mcp/<server>/*.json    reference tool-call schemas
```

Skills remain workflow and policy adapters. Deterministic execution lives in
`src/psammophis/`. `stash-app` is prompt/MCP-only and has no Python command.

### Canonical invocation

From a media library that symlinks this repo as `.agents`:

```bash
.agents/run.sh <command> ...
.agents/run.sh --reporter jsonl --progress-interval 30 transcode run ... --yes
```

From the checkout:

```bash
./run.sh <command> ...
uv run psammophis <command> ...
python -m psammophis <command> ...
```

Public commands: `analyze`, `artwork`, `compare`, `env-check`, `mkvedit`,
`organize`, `runs`, `subtitle`, `track-strip`, `transcode`.

`run.sh` sources `.envrc` (if present), sets `MEDIALIB_ROOT` when appropriate,
and `exec`s `uv run --project <repo> psammophis "$@"`. It does not construct
`PYTHONPATH` or special-case dependencies.

### `psammophis.medialib` -- shared helpers

`src/psammophis/medialib/` holds walkers, dotenv parsing, humansize, AV1
presets/backend/grain/SVT helpers, TMDB, and videoprobe. Feature packages own
their CLI surface and `_PREFIX`-scoped env vars. Before adding a new directory
walker or `.env` parser, check medialib first.

Library-root resolution no longer walks ancestors for a directory named
`.agents` inside Python. Prefer:

1. Explicit `--root`
2. Feature env (`TRANSCODE_ROOT`, `TRACKSTRIP_ROOT`, …)
3. `MEDIALIB_ROOT` (set by `run.sh` when appropriate)
4. Invocation cwd

### Progress and journals

- Progress/diagnostics go to **stderr**; command results stay on **stdout**.
- Reporters: `--reporter auto|tty|plain|jsonl|quiet`
- Durable journals (applied work): `<root>/.cache/psammophis/runs/<run-id>/`
- Transcode backups/logs live at `.cache/transcode/`. Track-strip state stays
  at `.cache/trackstrip/`.
- Inspect runs: `psammophis runs list|show|events`

For long encodes, skills should use JSONL, keep observing the same process, and
treat success only when `run.completed` and the exit code agree. Do not relaunch
work merely because an interval was quiet.

## Code quality

No CI -- whoever edits `src/psammophis/**/*.py` or `tests/**/*.py` runs from the
repo root before calling the change done:

```bash
uv sync --group dev
uv run ruff check .
uv run ruff format .
uv run basedpyright .
uv run pytest
```

- Config lives in root `pyproject.toml` -- no per-skill config files.
- Fix what these flag rather than suppressing unless a one-line reason is given.
- Runtime deps are declared under `[project] dependencies` (`guessit`, `rich`);
  dev tools under `[dependency-groups] dev`. Do not reintroduce
  `requirements.txt` as a second source of truth.
- External tools (ffmpeg, nvencc, …) remain env-check prerequisites, not Python deps.
- Type hints on function signatures; pure functions kept separate from CLI I/O.
- Any operation that mutates the real media library must default to dry-run,
  verify before touching an original, and prefer backup over delete. See
  `skills/track-strip/reference/incidents.md`.
- Nontrivial policy/parsing logic needs pytest under `tests/<feature>/`.
- **Python 3.14 required.** `ruff format` may emit 3.14 bare-comma `except`
  syntax; do not rewrite it back to tuple form.

## Adding a new skill

1. `skills/<name>/SKILL.md` with frontmatter (see `skills/track-strip/SKILL.md`).
2. If it needs code, add `src/psammophis/<package>/` and register the public
   command in `psammophis.cli._COMMANDS`.
3. Reuse `psammophis.medialib` / `psammophis.runtime` before inventing walkers,
   root resolution, or progress plumbing.
4. New Python dependency -> `pyproject.toml` + `uv lock`.
5. Tests under `tests/<feature>/`; run the quality gate above.
