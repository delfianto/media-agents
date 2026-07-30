# AGENTS.md

This repo is a collection of skills for maintaining a self-hosted Plex media library -- codec/language statistics, stripping non-English audio/subtitle tracks, and fixing codec-level playback incompatibilities. This file is the entry point for any coding agent working here; `CLAUDE.md` is a symlink to it.

## What's here

```
README.md              skill catalog and usage overview
pyproject.toml          ruff + basedpyright config for every Python script under skills/ and lib/
conftest.py             puts lib/ on sys.path for pytest, regardless of which subdirectory it's invoked against
requirements.txt        shared runtime deps for scripts that need them (guessit, for media-organizer)
requirements-dev.txt    test-only deps (pytest) for skills that ship a test suite
mcp_config.json         MCP server(s) this repo's skills expect to have configured (currently: stash-mcp-server)
mcp/<server>/*.json     reference tool-call schemas for an MCP server named in mcp_config.json
lib/medialib/*.py       shared, dependency-free helpers every skill's CLI imports (see below)
skills/<name>/SKILL.md         skill definition (frontmatter + instructions)
skills/<name>/scripts/*.py     the Python harness code a skill invokes, where one exists
skills/<name>/reference/*.md   supplementary docs (incident history, worked examples) SKILL.md points to rather than inlines
```

Each skill with real logic (`track-strip`, `av1-transcode`, `media-organizer`) is a proper multi-module package (`scripts/<pkg>/`), not a single flat script -- each has enough interdependent logic (policy decisions, multiple backends, external API clients, a CLI) to warrant it. Don't force a future skill into a single-file shape it doesn't fit; `scripts/` can hold whatever internal structure the skill actually needs. `env-check` is also a real package but a much smaller one (one `check_*` function per prerequisite plus pure report-formatting) -- simple logic doesn't need forcing into a single flat script either, it just doesn't need as many modules. Not every skill needs a `scripts/` directory at all -- `stash-app` (talks to a local Stash media server via the `stash-mcp` MCP server) is prompt/instruction-only.

### `lib/medialib` -- shared helpers, not per-skill reinvention

`lib/medialib/` holds the handful of things every skill's CLI independently needed: `walk.py` (directory walking with dot/skip-name/`exclude_dirs` pruning), `libroot.py` (`.agents`-checkout auto-detection for a default `--root`), `dotenv.py` (`.env` KEY=VALUE parsing), `humansize.py`. Each skill's own `cli.py`/`config.py` still owns its CLI surface, `_PREFIX`-scoped env vars, and any skill-specific defaults -- `lib/medialib` only owns mechanics duplicated verbatim across skills.

This split exists because a real bug (a live `os.walk` picking up its own freshly-written `--output-dir` output as a new source mid-run -- see `skills/av1-transcode/reference/incidents.md`) got fixed in one skill's hand-rolled walker while an identical, independently-written copy in another skill stayed silently broken. Before adding a new hand-rolled directory walker, `.env` parser, or `_find_library_root`-style function to a new or existing skill, check whether `lib/medialib` already has it.

A skill's own `scripts/<pkg>/__main__.py` entrypoint shim still needs a few lines of its own to put `lib/` on `sys.path` before it can `import medialib` (see any existing shim, e.g. `skills/av1-transcode/scripts/av1transcode/__main__.py`) -- this can't itself come from `medialib`, since it's what makes `medialib` importable in the first place. The shim lives *inside* its package as `__main__.py` rather than as a same-named sibling file next to it (`scripts/av1transcode.py` beside `scripts/av1transcode/`) -- that sibling shape isn't actually broken when run by direct path (a directly-executed script is `__main__` in `sys.modules`, never registered under the package's own name, so there's no real shadowing), but it reads as a footgun and isn't idiomatic, so every skill here uses `__main__.py` instead. Invocation is unchanged in spirit, just one path segment longer: `python3 <path-to-skill>/scripts/<pkg>/__main__.py ...`. This repo is commonly symlinked in as `.agents` under the real media library root, and finding it by name needs more care than `Path(__file__).resolve()` or even `.absolute()`: CPython itself silently absolutizes `__file__` via the OS's always-physical (symlink-resolved) cwd before any script code runs, so a relative invocation typed after `cd`-ing into `.agents` already has that symlink gone by the time any of this code sees `__file__` -- confirmed directly, not theoretical. `sys.argv[0]` is the raw, unresolved string the shell actually passed, so it's reconstructed against `$PWD` (verified via `os.path.samefile` against the physical cwd, not string equality) instead -- see `medialib.libroot.find_own_script_path`/`to_absolute_preserving_symlinks`, and the near-identical bootstrap duplicated in each shim (which can't import `medialib` to get this logic from there).

## Code quality for Python harness scripts

There's no CI here -- it's a private repo with no cross-repo dependents, so a build gate wasn't earning its keep. That means there's no pipeline catching a bad script later; it's on whichever agent writes or edits a `skills/<name>/scripts/*.py` (or `lib/medialib/*.py`) file to run these from the repo root before calling the change done:

```bash
ruff check .
ruff format .
basedpyright .
```

- Config lives in the root `pyproject.toml` -- don't add per-skill config files.
- Fix what these flag rather than suppressing it (`# noqa`, `# type: ignore`) unless the suppression comes with a one-line reason a future reader couldn't infer.
- Keep scripts stdlib-first. Adding any third-party dependency means updating `requirements.txt` too (or `requirements-dev.txt` if it's test-only).
- Match existing script conventions: type hints on function signatures, no comments beyond a one-line note for genuinely non-obvious behavior, small stdlib-first helpers over frameworks, pure functions (no I/O) kept separate from the CLI entry point so they stay unit-testable.
- Any operation that mutates the real media library must default to (or clearly document) a dry-run mode, verify before touching an original file, and prefer backing up over deleting -- this library runs against real, non-disposable, and in some cases irreplaceable media. See `skills/track-strip/reference/incidents.md` for two concrete cases where skipping one of these guarantees actually lost data (or would have, if not caught first).
- If the logic involves nontrivial parsing, threshold tuning, or ordering rules, add a pytest suite (`skills/<name>/scripts/test_*.py`, or `lib/test_*.py` for `medialib`) rather than eyeballing a few manual runs -- `track_policy.py`'s SDH size-ratio bounds and anime-detection track-count threshold are exactly this shape, and both had real false positives caught only by hand-auditing the whole library before the corresponding test suite existed. `pytest` lives in `requirements-dev.txt`, not `requirements.txt` -- it's never a runtime dependency of the skill itself. Run it with:
  ```bash
  pip install -r requirements.txt -r requirements-dev.txt
  pytest skills/<name>/scripts/   # or: pytest lib/   or just: pytest
  ```
  The root `conftest.py` puts `lib/` on `sys.path` automatically, so `import medialib` resolves inside a single-skill test run too -- no need to pass `lib/` alongside `skills/<name>/scripts/` for that to work.
  If there's no system `pip` (true on at least one machine this repo runs on -- `uv` is present instead), use a local project venv pinned to match `pyproject.toml`'s `target-version` instead of letting `uv` pick its own default (it'll happily pick an older interpreter than the code actually needs -- see the next bullet):
  ```bash
  uv venv --python 3.14 && uv pip install -r requirements.txt -r requirements-dev.txt
  .venv/bin/python3 -m pytest skills/<name>/scripts/
  ```
- **This codebase requires Python 3.14, not just "whatever the oldest dependency needs."** `ruff format` (target-version `py314` in `pyproject.toml`) rewrites multi-exception `except` clauses into Python 3.14's bare-comma grammar (`except A, B:` instead of `except (A, B):`) -- don't hand-fix these back to tuple-parens form, that's the formatter's deliberate, version-appropriate output, not a leftover Python-2-ism. It also means anything with a `uv run`-style PEP 723 inline script metadata block must pin `requires-python = ">=3.14"`, not a lower floor that merely covers its own dependencies -- confirmed the hard way once already: an under-pinned block let `uv run` silently provision 3.13, and the whole package failed to import with a `SyntaxError` on exactly this except-clause form.

## Adding a new skill

1. `skills/<name>/SKILL.md` with frontmatter (see `skills/track-strip/SKILL.md` for the shape).
2. Only add a `scripts/` directory if the skill needs actual code beyond prompt instructions.
3. Before writing a directory walker, `.env` parser, or default-`--root` detection from scratch, check `lib/medialib` -- see "`lib/medialib` -- shared helpers, not per-skill reinvention" above.
4. New script dependency -> add it to `requirements.txt` (or `requirements-dev.txt` if it's test-only).
5. Run the three commands above (and any pytest suite) before considering it done.
