# AGENTS.md

This repo is a collection of skills for maintaining a self-hosted Plex media library --
codec/language statistics, stripping non-English audio/subtitle tracks, and fixing codec-level
playback incompatibilities. This file is the entry point for any coding agent working here;
`CLAUDE.md` is a symlink to it.

## What's here

```
README.md              skill catalog and usage overview
pyproject.toml          ruff + basedpyright config for every Python script under skills/
requirements.txt        shared runtime deps for scripts that need them (guessit, for media-organizer)
requirements-dev.txt    test-only deps (pytest) for skills that ship a test suite
skills/<name>/SKILL.md         skill definition (frontmatter + instructions)
skills/<name>/scripts/*.py     the Python harness code a skill invokes, where one exists
skills/<name>/reference/*.md   supplementary docs (incident history, worked examples) SKILL.md points to rather than inlines
```

Each skill (`media-library`, `av1-transcode`, `media-organizer`) is a proper multi-module
package (`scripts/<pkg>/`), not a single flat script like the simpler skills in sibling repos --
each has enough interdependent logic (policy decisions, multiple backends, external API
clients, a CLI) to warrant it. Don't force a future skill into a single-file shape it doesn't
fit; `scripts/` can hold whatever internal structure the skill actually needs, same as
`cosplay-metadata` in `lychee-agents` holds multiple scripts plus a `reference/` directory.

## Code quality for Python harness scripts

There's no CI here -- it's a private repo with no cross-repo dependents, so a build gate wasn't
earning its keep. That means there's no pipeline catching a bad script later; it's on whichever
agent writes or edits a `skills/<name>/scripts/*.py` file to run these from the repo root before
calling the change done:

```bash
ruff check .
ruff format .
basedpyright .
```

- Config lives in the root `pyproject.toml` -- don't add per-skill config files.
- Fix what these flag rather than suppressing it (`# noqa`, `# type: ignore`) unless the
  suppression comes with a one-line reason a future reader couldn't infer.
- Keep scripts stdlib-first. Adding any third-party dependency means updating `requirements.txt`
  too (or `requirements-dev.txt` if it's test-only).
- Match existing script conventions: type hints on function signatures, no comments beyond a
  one-line note for genuinely non-obvious behavior, small stdlib-first helpers over frameworks,
  pure functions (no I/O) kept separate from the CLI entry point so they stay unit-testable.
- Any operation that mutates the real media library must default to (or clearly document) a
  dry-run mode, verify before touching an original file, and prefer backing up over deleting --
  this library runs against real, non-disposable, and in some cases irreplaceable media. See
  `skills/media-library/reference/incidents.md` for two concrete cases where skipping one of
  these guarantees actually lost data (or would have, if not caught first).
- If the logic involves nontrivial parsing, threshold tuning, or ordering rules, add a pytest
  suite (`skills/<name>/scripts/test_*.py`) rather than eyeballing a few manual runs --
  `track_policy.py`'s SDH size-ratio bounds and anime-detection track-count threshold are exactly
  this shape, and both had real false positives caught only by hand-auditing the whole library
  before the corresponding test suite existed. `pytest` lives in `requirements-dev.txt`, not
  `requirements.txt` -- it's never a runtime dependency of the skill itself. Run it with:
  ```bash
  pip install -r requirements.txt -r requirements-dev.txt
  pytest skills/<name>/scripts/
  ```
  If there's no system `pip` (true on at least one machine this repo runs on -- `uv` is present
  instead), use a local project venv pinned to match `pyproject.toml`'s `target-version` instead
  of letting `uv` pick its own default (it'll happily pick an older interpreter than the code
  actually needs -- see the next bullet):
  ```bash
  uv venv --python 3.14 && uv pip install -r requirements.txt -r requirements-dev.txt
  .venv/bin/python3 -m pytest skills/<name>/scripts/
  ```
- **This codebase requires Python 3.14, not just "whatever the oldest dependency needs."**
  `ruff format` (target-version `py314` in `pyproject.toml`) rewrites multi-exception `except`
  clauses into Python 3.14's bare-comma grammar (`except A, B:` instead of `except (A, B):`) --
  don't hand-fix these back to tuple-parens form, that's the formatter's deliberate, version-
  appropriate output, not a leftover Python-2-ism. It also means anything with a `uv run`-style
  PEP 723 inline script metadata block must pin `requires-python = ">=3.14"`, not a lower floor
  that merely covers its own dependencies -- confirmed the hard way once already: an
  under-pinned block let `uv run` silently provision 3.13, and the whole package failed to
  import with a `SyntaxError` on exactly this except-clause form.

## Adding a new skill

1. `skills/<name>/SKILL.md` with frontmatter (see `skills/media-library/SKILL.md` for the shape).
2. Only add a `scripts/` directory if the skill needs actual code beyond prompt instructions.
3. New script dependency -> add it to `requirements.txt` (or `requirements-dev.txt` if it's
   test-only).
4. Run the three commands above (and any pytest suite) before considering it done.
