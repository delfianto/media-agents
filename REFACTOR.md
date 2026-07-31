# Psammophis refactor plan

- Status: **implementation complete (phases 0–9 executed in this branch)**
- Last updated: 2026-07-31
- Scope: turn the repository's Python harnesses into one packaged application named
  `psammophis`, replace the nested launcher with a root `run.sh`, and add progress reporting
  that is appropriate for both interactive humans and LLM-driven shell sessions.

This document is the implementation source of truth. Update its trackers as work lands; do
not let it become a historical proposal that drifts away from the code.

## How to maintain this plan

- Mark a task complete only after its phase-specific checks pass.
- Record material design changes in the decision log before implementing them.
- Record commands and results in the validation log; do not rely on “tests passed locally”
  without saying which tests.
- Keep safety-related compatibility work explicit. In particular, never strand an existing
  backup directory by renaming a state path without a migration and tests.
- Keep commits aligned with the phase boundaries below. Packaging moves, behavior changes,
  and progress/UI changes should remain independently reviewable where practical.

Tracker symbols:

- `[ ]` not started
- `[~]` in progress
- `[x]` complete
- `[!]` blocked or failed; explain in the implementation log

## Locked decisions

| ID | Decision | Rationale |
| --- | --- | --- |
| D-001 | The distribution, import package, and installed executable are all named `psammophis`. | One memorable identity and no generic commands such as `subtitle` added to `PATH`. |
| D-002 | The only checkout launcher in the final layout is executable `run.sh` at the repository root. | Humans use `./run.sh`; skills use `.agents/run.sh`; both reach the same CLI. |
| D-003 | This is one root Python project, not a nested project and not a uv workspace of per-skill packages. | The commands share code, deployment, release cadence, and safety conventions. |
| D-004 | Public command names and their existing flags remain stable during the packaging move. **Superseded in part by D-017.** | Packaging should not be mixed with an avoidable user-facing CLI rewrite. |
| D-005 | `argparse` remains the CLI framework. | It already works, is stdlib, and is not the source of the current complexity. |
| D-006 | Feature packages move under one `psammophis` namespace; their current internal names are initially preserved. | `psammophis.transcode` is less risky to migrate than simultaneously renaming it to `av1_transcode`. Public spelling remains `transcode`. |
| D-007 | Skills remain workflow and policy adapters. Deterministic execution belongs to the Python application. | Humans and LLMs should invoke the same tested behavior. |
| D-008 | `stash-app` remains prompt/MCP-only and does not gain an empty Python command. | It has no local Python execution engine to package. |
| D-009 | Rich is an accepted runtime dependency for interactive terminal rendering. | Multi-task, indeterminate, and log-safe progress displays are worth one focused dependency. |
| D-010 | Core operations emit typed progress events; human, plain, JSONL, and journal outputs are separate consumers. | Presentation must not leak into encoding or mutation logic. |
| D-011 | Progress and diagnostics go to `stderr`; command results remain on `stdout`. | This preserves normal shell composition and allows JSONL events to be captured separately. |
| D-012 | Existing environment-variable names and persistent cache/backup paths remain recognized. **Superseded for transcode by D-017.** | Renaming them during a structural refactor risks configuration drift and orphaned originals. |
| D-013 | A final success event is emitted only after all verification and commit/swap work succeeds. | Encoder progress reaching 100% is not proof that the output was verified or installed safely. |
| D-014 | No daemon, detached-job service, web UI, or background queue is part of this refactor. | Foreground execution, durable status, and a reconnectable event journal solve the immediate need without a new process-lifecycle system. |
| D-015 | The refactor does not change codec policy, presets, matching thresholds, or destructive-operation defaults. | Those require separate domain review and real-media validation. |

## Goals

1. Make the Python code a conventional, installable Python 3.14 application.
2. Give humans and skills one command grammar:

   ```bash
   psammophis <command> ...
   ./run.sh <command> ...
   .agents/run.sh <command> ...
   ```

3. Remove runtime `PYTHONPATH` construction, duplicated entrypoint bootstraps, and
   install-location-based Python imports.
4. Preserve every current dry-run, backup, verification, and output-directory safety
   guarantee.
5. Provide polished terminal progress for humans without emitting ANSI control sequences to
   non-interactive callers.
6. Provide compact, versioned JSONL progress and completion events for LLM/tool callers.
7. Give long-running applied operations durable run IDs and inspectable status without
   requiring the original terminal session.
8. Make exit codes reliable enough for automation, including partial batch failures.
9. Keep dependency and development-tool declarations in `pyproject.toml`, with a committed
   lockfile for reproducible application installs.

## Non-goals

- Publishing to PyPI as part of this work. The result must be buildable and installable, but
  publication is a later decision.
- Splitting commands into independently versioned distributions.
- Replacing subprocess tools such as FFmpeg, NVEncC, mkvmerge, or mkvpropedit with Python
  libraries.
- Rewriting the command implementations or merging feature-specific configuration models.
- Renaming existing `TRACKSTRIP_*`, `ORGANIZE_*`, `ARTWORK_*`, or `SUBTITLE_*` settings.
- Automatically migrating pre-refactor transcode state or `.cache/trackstrip` data.
- Making prompt-only MCP skills part of the Python executable.
- Adding concurrency to encodes or mutation operations merely because the new progress layer
  can display multiple tasks.
- Changing dry-run defaults or weakening `--yes` gates.

## Current-state findings

- The root `pyproject.toml` contains Ruff and basedpyright configuration but no `[project]`,
  `[project.scripts]`, or `[build-system]` metadata.
- Nine feature packages live below `skills/<name>/scripts/<package>/` and have conventional
  multi-module application code, not standalone scripts.
- Nine `__main__.py` files contain roughly 50–100 lines of import and symlink bootstrap logic.
- `scripts/run-skill` owns an explicit public-name-to-module map, constructs `PYTHONPATH`, and
  special-cases `organize`'s `guessit` dependency.
- `lib/medialib` contains both generic mechanics and shared media/AV1 domain logic used by
  several commands.
- Tests require a root `conftest.py` path injection because the code is not installed as a
  package.
- AV1 encoding already streams full logs and exposes an `on_progress` callback, but the
  callback contains formatted strings rather than structured state.
- FFmpeg progress is currently extracted from its terminal-oriented `frame=...` stats line.
- NVEncC output is treated as non-tick text, so it can bypass the FFmpeg-specific throttle.
- Several batch CLIs count per-item errors but do not consistently exit nonzero afterward.
- Existing applied AV1 work performs cover attachment, statistics refresh, verification,
  backup, and final move after the encoder process itself finishes.

## Target repository layout

```text
README.md
AGENTS.md
REFACTOR.md
LICENSE
pyproject.toml
uv.lock
.python-version
run.sh

src/
  psammophis/
    __init__.py
    __main__.py
    cli.py
    runtime/
      __init__.py
      context.py
      events.py
      journal.py
      process.py
      reporters.py
      roots.py
      runs_cli.py
    medialib/
      __init__.py
      av1_backend.py
      av1_presets.py
      colorinfo.py
      dotenv.py
      gpu.py
      grain.py
      humansize.py
      naming.py
      svt.py
      tmdb.py
      videoprobe.py
      walk.py
    analyze/
    artwork/
    transcode/
    envcheck/
    mkvedit/
    organize/
    compare/
    subtitle/
    trackstrip/

tests/
  runtime/
  medialib/
  analyze/
  artwork/
  transcode/
  envcheck/
  launcher/
  mkvedit/
  organize/
  compare/
  subtitle/
  trackstrip/

skills/
  <name>/
    SKILL.md
    agents/openai.yaml
    reference/ or references/
```

Notes:

- Feature package module files keep their present internal division during the move.
- Only `psammophis/__main__.py` remains as a module entrypoint, and it should be a minimal
  `raise SystemExit(main())` shim.
- Skill directories stop owning executable Python code.
- The existing `reference/` versus `references/` inconsistency is not part of this refactor;
  links may be corrected, but directories should not be renamed gratuitously.

## Source migration map

| Current source | Target | Status |
| --- | --- | --- |
| `lib/medialib/` | `src/psammophis/medialib/` | `[x]` |
| `skills/analyze/scripts/analyze/` | `src/psammophis/analyze/` | `[x]` |
| `skills/artwork/scripts/artwork/` | `src/psammophis/artwork/` | `[x]` |
| `skills/av1-transcode/scripts/av1transcode/` | `src/psammophis/transcode/` | `[x]` |
| `skills/env-check/scripts/envcheck/` | `src/psammophis/envcheck/` | `[x]` |
| `skills/mkvedit/scripts/mkvedit/` | `src/psammophis/mkvedit/` | `[x]` |
| `skills/organize/scripts/organize/` | `src/psammophis/organize/` | `[x]` |
| `skills/quality-compare/scripts/compare/` | `src/psammophis/compare/` | `[x]` |
| `skills/subtitle/scripts/subtitle/` | `src/psammophis/subtitle/` | `[x]` |
| `skills/track-strip/scripts/trackstrip/` | `src/psammophis/trackstrip/` | `[x]` |
| `lib/test_*.py` | `tests/medialib/test_*.py` | `[x]` |
| Per-skill `scripts/test_*.py` | Corresponding `tests/<feature>/test_*.py` | `[x]` |
| Root `conftest.py` import bootstrap | Delete after installed-package test imports work | `[x]` |
| Per-feature `__main__.py` bootstraps | Delete after the central entrypoint passes parity tests | `[x]` |
| `scripts/run-skill` | Transitional forwarder, then delete in favor of root `run.sh` | `[x]` |

Use `git mv` for source and test moves so history remains traceable. Avoid mixing functional
rewrites into those move commits.

## Public command compatibility matrix

| Public command | Target handler | Existing nested subcommands | Required compatibility smoke |
| --- | --- | --- | --- |
| `analyze` | `psammophis.analyze.cli` | none | `analyze --help`; one-file JSON and text previews |
| `artwork` | `psammophis.artwork.cli` | none | dry-run plan and invalid `--tmdb-id` handling |
| `transcode` | `psammophis.transcode.cli` | `probe`, `list-presets`, `run`, `purge-backups` | help for every subcommand; dry-run command parity |
| `env-check` | `psammophis.envcheck.cli` | none | category filtering and required-check exit status |
| `mkvedit` | `psammophis.mkvedit.cli` | none | dry-run command rendering and applied failure exit |
| `organize` | `psammophis.organize.cli` | none | dry-run plan, review result, and config failure |
| `compare` | `psammophis.compare.cli` | none | image and video modes; JSON/stdout behavior |
| `runs` | `psammophis.runtime.runs_cli` | `list`, `show`, `events` | read-only journal inspection; JSON output and stale-run handling |
| `subtitle` | `psammophis.subtitle.cli` | none | dry-run plan, missing credential behavior |
| `track-strip` | `psammophis.trackstrip.cli` | `scan`, `stats`, `plan`, `apply`, `transcode`, `purge-backups` | help for every subcommand; cache and dry-run parity |

The checkout launcher changes as follows; final command names are the ones in the matrix above:

```text
.agents/scripts/run-skill track-strip scan
    becomes
.agents/run.sh track-strip scan
```

The old prefix may forward during implementation, but it is not part of the final layout.

## Packaging contract

`pyproject.toml` becomes the single source for runtime and development dependencies.

Required project metadata:

```toml
[project]
name = "psammophis"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = [
    "guessit",
    "rich",
]

[project.scripts]
psammophis = "psammophis.cli:main"
```

Implementation requirements:

- Use `uv_build` as the PEP 517 build backend with a bounded, currently compatible backend
  version selected when the phase is implemented.
- Add a `dev` dependency group containing `pytest`, `ruff`, and `basedpyright`.
- Retain the current Ruff and basedpyright rules, changing their include roots from
  `skills`/`lib` to `src`/`tests`.
- Add pytest configuration with `testpaths = ["tests"]`; use importlib import mode or package
  test directories so duplicate test basenames no longer create a collection hazard.
- Add `.python-version` containing `3.14`.
- Remove `uv.lock` from `.gitignore`, regenerate it, and commit it.
- Delete `requirements.txt` and `requirements-dev.txt` after verifying no documented or local
  workflow still consumes them. Do not maintain duplicate hand-edited dependency sources.
- Keep external executables and GPU capabilities in `env-check`; they are runtime
  prerequisites, not Python package dependencies.
- Verify both editable/project execution and an installed wheel. A working checkout alone is
  insufficient evidence that packaging is correct.

Canonical development commands after migration:

```bash
uv sync --group dev
uv run ruff check .
uv run ruff format .
uv run basedpyright .
uv run pytest
uv build
```

## Central CLI design

`psammophis.cli` owns only global concerns and dispatch:

- `--version`
- `--reporter auto|tty|plain|jsonl|quiet`
- `--progress-interval SECONDS` for non-interactive progress throttling
- `--state-dir PATH`
- `--journal` / `--no-journal`
- public command selection and top-level help
- reporter construction
- run ID creation
- top-level exception-to-exit-code handling

Global options precede the feature command:

```bash
.agents/run.sh --reporter jsonl --progress-interval 30 transcode run --path "Dune" --yes
```

Feature CLIs continue to own their flags and nested subcommands. The dispatcher should lazily
import the selected handler and pass it the remaining arguments plus an application context.
The target handler contract is conceptually:

```python
def main(argv: list[str], context: AppContext) -> int: ...
```

The precise type name may differ, but these rules do not:

- Feature handlers return an integer rather than calling `sys.exit()` for normal operational
  outcomes.
- `argparse` may continue raising `SystemExit(2)` for usage errors.
- The installed entrypoint and `python -m psammophis` both call the same top-level `main()`.
- Parser `prog` values display full public names such as `psammophis track-strip`, never
  internal package spellings such as `trackstrip`.
- Importing `psammophis.cli` must not probe hardware, read credentials, or import every
  feature eagerly.
- Command result rendering remains separate from progress reporting. In particular, existing
  `--json` result payloads stay on `stdout`.

## Root `run.sh` contract

The root launcher is a checkout bootstrap, not the application router. It must:

1. Enable strict Bash behavior.
2. Resolve its own repository root while preserving the logical `.agents` symlink when the
   shell invoked it through that path.
3. Source `<repo-root>/.envrc` with automatic export, preserving the caller's prior `allexport`
   state.
4. Set `MEDIALIB_ROOT` only when the caller did not already set it:
   - to `$PWD` when `$PWD/.agents` and the launcher repository are the same directory by inode;
   - otherwise to the logical parent when the repository itself is named `.agents`.
5. Execute the project with the caller's current working directory unchanged:

   ```bash
   exec uv run --project "$repo_root" psammophis "$@"
   ```

6. Forward signals and the application's exact exit status via `exec`.

It must not:

- maintain a command/package case statement;
- construct or export `PYTHONPATH`;
- special-case `guessit` or any feature dependency;
- infer application behavior from source-tree paths;
- silently choose an older Python interpreter than the project's `requires-python` value.

Launcher tests must cover direct checkout invocation, `.agents` symlink invocation, an
already-set `MEDIALIB_ROOT`, `.envrc` export behavior, argument preservation, paths containing
spaces, and exact exit-code propagation.

## Runtime context and root resolution

Python code must no longer derive the media-library root from `__file__`, `sys.argv[0]`, or an
ancestor literally named `.agents`. For root-oriented commands, use this precedence:

1. Explicit command `--root`.
2. Existing feature-specific root variable, where supported (`TRANSCODE_ROOT` or
   `TRACKSTRIP_ROOT`).
3. `MEDIALIB_ROOT`.
4. The invocation working directory.

The resolved root and which source supplied it should be included in `run.started` events.

Safety requirements:

- Resolve and validate the root before walking it.
- Reject nonexistent or non-directory roots.
- Refuse dangerously broad mutation roots such as filesystem `/`; add focused tests for every
  rejection rather than relying on string comparisons alone.
- Preserve the shared walker's dot-directory, skip-name, `skip_root_files`, and
  `exclude_dirs` behavior.
- For a batch that can write or move files during traversal, snapshot the complete candidate
  list before the first mutation. This gives progress an accurate item total and removes the
  remaining live-`os.walk` self-discovery window.
- Do not treat path resolution as authorization to operate outside the root.

Once no caller depends on source-location discovery, remove `medialib.libroot` and its tests.
Retain only genuinely useful logical-path helpers in the shell launcher.

## Configuration and persistent state contract

The following names and locations remain valid through this refactor:

| Contract | Required behavior |
| --- | --- |
| `MEDIALIB_ROOT` | Continue to work for root-oriented commands. |
| `TRANSCODE_*` | Use current flag/environment/`.env` precedence; no old-prefix fallback. |
| `TRACKSTRIP_ROOT` | Continue to override the shared root. |
| `ORGANIZE_*`, `ARTWORK_*`, `SUBTITLE_*` | Preserve names and existing shared-key fallbacks. |
| `.cache/transcode/originals` | Continue as the default AV1 backup location and remain visible to `purge-backups`. |
| `.cache/transcode/logs` | Continue as the default raw AV1 encoder-log location. |
| `.cache/trackstrip/originals` | Continue as the default track-strip backup location and remain visible to `purge-backups`. |
| `.cache/trackstrip/scan.json` | Continue as the track scan cache without forced conversion. |

New run journals live separately under:

```text
<state-root>/.cache/psammophis/runs/<run-id>/
```

For a root-oriented command, `<state-root>` is its validated media-library root. For a
path-oriented command, use `MEDIALIB_ROOT` when available. `--state-dir PATH` overrides this
and names the Psammophis state directory directly (so its runs live at `PATH/runs`). If neither
a contextual root nor `--state-dir` is available, live reporters still work but persistent
journaling stays disabled rather than writing unexpectedly into an arbitrary working directory.

Do not move existing backup or scan data underneath the run-journal directory. Per D-017, the
application intentionally does not discover or migrate pre-refactor transcode paths.

## Output and exit-code contract

### Streams

- `stdout`: command result data and existing human-readable reports.
- `stderr`: progress, warnings, errors, and reporter events.
- per-file raw logs: complete subprocess output, including encoder banners and diagnostics.
- per-file raw logs must remain live and include enough raw or normalized progress information
  for `tail -f` to remain a useful fallback monitor.
- JSONL reporter mode: every reporter line on `stderr` is one complete JSON object; no ANSI,
  carriage-return animation, or raw subprocess chatter is mixed into that stream.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Invocation completed successfully, including a valid dry run with no operational errors. |
| `1` | Operational failure or a batch with one or more failed items. The completion event distinguishes `partial` from `failed`. |
| `2` | CLI usage or configuration error. |
| `130` | Cancelled by SIGINT. |
| `143` | Terminated by SIGTERM when graceful handling is possible. |

Rules:

- A batch that changes some files and fails others exits `1` and reports `status="partial"`.
- A low-confidence `organize` item that is intentionally left for review is not automatically
  an operational failure; it is counted separately in the summary.
- “No matching files” remains successful unless the feature already defines that condition as
  an error.
- A dry-run plan is not reported as “changed.”
- Exit status and the terminal `run.completed` event must agree.
- Top-level unexpected exceptions are summarized once, journaled when possible, and return
  `1`; tracebacks are shown only under an explicit debug option or written to the raw log.

## Progress event model

Core code emits immutable typed events to an `EventSink`/reporter protocol. It never imports
Rich and never serializes presentation-specific strings as its only representation of state.

Every serialized event has these envelope fields:

| Field | Type | Contract |
| --- | --- | --- |
| `schema` | integer | Starts at `1`; increment only for incompatible protocol changes. |
| `event` | string | Stable dotted event name. |
| `run_id` | string | Sortable unique ID generated once per parsed invocation. |
| `seq` | integer | Strictly increasing within a run, starting at `1`. |
| `timestamp` | string | UTC RFC 3339 timestamp. |
| `command` | string | Public command path, for example `transcode run`. |

Event-specific optional values must be omitted rather than serialized as misleading empty
strings. Paths in per-item events should be relative to the operational root where possible.
Durations and ETAs are numeric seconds; percentages are numeric values in `[0, 100]`.

### Required event types

| Event | Required purpose |
| --- | --- |
| `run.started` | Command, root/context source, dry-run/applied mode, item total if already known, reporter, and journal path. |
| `run.heartbeat` | Proves liveness when a phase cannot provide measurable progress. |
| `item.started` | Current relative item and one-based index/total. |
| `phase.started` | Named phase such as `probe`, `measure-grain`, `encode`, `verify`, or `commit`. |
| `item.progress` | Normalized measurable progress: media position, duration, percent, FPS, speed, ETA, and backend fields when available. |
| `message` | Structured `info`, `warning`, or `error` that is meaningful outside the raw tool log. |
| `phase.completed` | Phase status and elapsed time. |
| `item.completed` | Per-item terminal status, output path, size/bitrate result, and log path. |
| `run.completed` | The sole authoritative terminal event with final status, exit code, counts, artifacts, elapsed time, and journal location. |

Example agent stream:

```json
{"schema":1,"event":"run.started","run_id":"019...","seq":1,"timestamp":"2026-07-31T10:00:00Z","command":"transcode run","root":"/media","root_source":"MEDIALIB_ROOT","mode":"applied","items_total":2}
{"schema":1,"event":"item.progress","run_id":"019...","seq":8,"timestamp":"2026-07-31T10:05:00Z","command":"transcode run","item":"Movies/Dune/Dune.mkv","phase":"encode","percent":42.7,"speed":0.31,"eta_seconds":6840}
{"schema":1,"event":"run.completed","run_id":"019...","seq":19,"timestamp":"2026-07-31T12:10:00Z","command":"transcode run","status":"succeeded","exit_code":0,"changed":2,"errors":0}
```

### Completion semantics

For an AV1 item, `encode` reaching 100% is followed by some or all of:

1. cover attachment;
2. track-statistics refresh;
3. output probing and verification;
4. output collision recheck;
5. original backup or deletion, when applicable;
6. final output move/commit.

`item.completed(status="succeeded")` is emitted only after step 6. `run.completed` is emitted
only after every item has reached a terminal state. A model must never infer success from a
progress percentage.

### Emission and throttling

- Backend adapters may produce raw updates about once per second.
- TTY rendering may refresh several times per second without emitting new log lines.
- Plain and JSONL reporters emit progress when at least one percentage point changed or the
  configured interval elapsed, whichever comes first.
- Emit phase transitions, warnings, failures, and completions immediately.
- Emit a heartbeat no less often than every 60 seconds during an indeterminate long phase.
- Flush every plain/JSONL event immediately.
- Do not forward routine encoder banners as `message` events; retain them in the raw log.

## Process supervision and backend progress adapters

### Shared process supervisor

Create one subprocess supervision layer for long-running external commands. It must:

- keep stdout and stderr draining so neither pipe can deadlock;
- tee complete child diagnostics into the existing per-file log and retain live progress there;
- retain a bounded diagnostic tail for returned errors;
- accept a backend-specific progress parser;
- propagate SIGINT/SIGTERM to the child process;
- wait for child termination before reporting cancellation;
- clean temporary output using the command's existing safety logic;
- never convert a nonzero child exit into a successful item;
- work without a TTY.

### FFmpeg adapter

- Add FFmpeg's program-oriented `-progress pipe:1`, `-nostats`, and an explicit progress
  interval.
- Parse key/value blocks ending in `progress=continue` or `progress=end`.
- Normalize `out_time_us`/`out_time`, frame, FPS, speed, and total size.
- Compute percent and ETA from probed media duration only when the denominator is valid.
- Keep stderr as the full diagnostic log rather than scraping its terminal display.
- Add parser fixtures for initial unknown values, normal progress, malformed values, end
  blocks, zero/unknown duration, warnings, and failure.

Reference: <https://www.ffmpeg.org/ffmpeg.html#toc-Progress-options>

### NVEncC adapter

- Capture real output from the installed NVEncC version and a representative encode before
  declaring a grammar.
- Store sanitized samples as test fixtures.
- Split both carriage-return and newline updates correctly.
- Parse stable frame/percent/FPS/bitrate/ETA fields that are actually present; do not invent
  precision unavailable from the tool.
- Fall back to an indeterminate heartbeat plus raw log if an unknown future line format is
  encountered.
- Treat the final NVEncC summary as diagnostic data, not as proof that post-encode
  verification succeeded.

## Reporter design

| Reporter | Selection | Behavior |
| --- | --- | --- |
| `auto` | Default | Use TTY reporter only when `stderr` is interactive and the terminal is usable; otherwise use plain. |
| `tty` | Explicit | Rich live display on `stderr`; fail or fall back clearly if no usable terminal exists. |
| `plain` | Explicit/non-TTY auto | Stable, throttled, newline-delimited human text with no ANSI. |
| `jsonl` | Explicit for agents/tools | Versioned JSON object per line on `stderr`, flushed immediately. |
| `quiet` | Explicit | Suppress routine progress; retain actionable errors and final command result. |

### TTY reporter

Use Rich with `Console(stderr=True)`. The AV1 display should show:

- an overall item-count task;
- a current-file media-duration task;
- current phase;
- relative filename, shortened only for presentation;
- backend/engine and preset;
- percent, FPS, encoder speed, elapsed time, and ETA when known;
- indeterminate animation for probing, grain measurement, metadata refresh, and verification;
- persistent warning/error messages above the live display;
- one concise durable line per completed item, including output and size reduction.

Respect `NO_COLOR`, non-TTY pipes, and narrow terminals. Exact colors are cosmetic and should
not be snapshot-tested; semantic fields and fallback behavior should be tested.

Reference: <https://rich.readthedocs.io/en/latest/progress.html>

### Agent/JSONL reporter

- Never emit ANSI escapes or carriage-return rewrites.
- Never emit unstructured encoder output.
- Include the run ID in the first event.
- Keep event keys stable and documented.
- Emit exactly one terminal `run.completed` event for controlled completion, failure, or
  cancellation.
- Make `seq` sufficient for a caller to resume reading journal events after a known event.
- Include log and status paths rather than dumping large diagnostic tails into the model
  context.

Skills invoking a long operation should use:

```bash
.agents/run.sh --reporter jsonl --progress-interval 30 transcode run ... --yes
```

Skill instructions must tell the model to continue observing the same process/session, never
restart an encode merely because an interval produced no output, and accept success only when
both `run.completed.status` and the process exit code indicate success.

## Durable run journal

The event stream is live; the journal is recovery state. They share the same events but have
different write policies.

Default journaling policy:

- enabled for applied/mutating commands and commands that already create persistent runtime
  state, including applied AV1 work and track scans;
- enabled for long quality comparisons when a state root is available;
- disabled for commands whose current contract promises no writes, unless the caller
  explicitly passes `--journal`;
- overrideable with `--journal` or `--no-journal` after the command's safety policy permits it.

Directory contents:

```text
.cache/psammophis/runs/<run-id>/
  status.json       # latest state, replaced atomically
  events.jsonl      # append-only normalized events
  summary.json      # terminal result, written only at completion
```

`status.json` should include schema version, run ID, command, PID, hostname, start/update
timestamps, state, current phase/item, counts, percent when known, raw-log paths, and final exit
code when known. Never serialize environment contents, API keys, passwords, or authorization
headers.

Required read-only commands:

```bash
psammophis runs list --active --json
psammophis runs show <run-id> --json
psammophis runs events <run-id> --after <seq>
```

The `runs` commands use the same contextual state-directory resolution; an installed command
outside that context must be given `--state-dir`.

The initial implementation does not detach work. If a journal says `running` but its process
is no longer alive on the recorded host, `runs show` should report it as stale/interrupted
rather than claiming success. Journal retention/pruning may be added later; it must never
delete media backups or raw encoder logs.

## Safety invariants that must survive the refactor

- Every media mutation remains dry-run by default.
- `--yes` remains explicit and is never inferred from reporter mode, installation method, or
  non-interactive execution.
- An original is not moved or deleted until its replacement passes every existing verification
  check.
- Backup remains the default whenever an original is replaced.
- `--no-backup` retains its explicit warning.
- Separate-output mode never touches the source.
- Existing destination collision checks run both before and after a long encode.
- Batch candidates are snapshotted before mutation and output/backup/log/state directories are
  excluded from discovery.
- Temporary files are removed on controlled failure and cancellation where safe.
- A reporter failure must not cause an unverified temporary file to replace an original.
- Journal failure should fail closed before mutation starts or degrade according to an explicit
  tested policy; it must not silently claim recoverability it does not provide.
- Progress percentages never bypass verification or change transactional ordering.
- Purge commands continue requiring explicit confirmation and continue targeting exact,
  validated backup paths.
- No refactor step recursively deletes an old source tree until moved files and tests have been
  verified in their target locations.

## Detailed implementation phases

### Phase 0 — Baseline and contract capture

Objective: establish a trusted before-state so mechanical moves and intentional behavior changes
can be distinguished.

- [x] Record `git status --short` and preserve unrelated user changes.
- [x] Run and record the existing full test suite on Python 3.14.
- [x] Run and record existing Ruff, formatter, and basedpyright results.
- [x] Capture top-level and nested `--help` output for every scripted skill.
- [x] Capture representative text and JSON dry-run output using fixture/temp media.
- [x] Record current exit codes for success, usage error, configuration error, one-item failure,
  and partial batch failure.
- [x] Capture sanitized FFmpeg progress output.
- [x] Capture sanitized NVEncC progress output if the backend is available.
- [x] Confirm and record every existing backup/cache/log path on the real target environment
  without modifying it.

Exit criteria:

- [x] Baseline quality commands pass or all pre-existing failures are documented.
- [x] Compatibility fixtures are committed or recorded in an implementation note.
- [x] No real media was modified during baseline capture.

### Phase 1 — Package scaffold and mechanical source move

Objective: make all Python code importable as one `psammophis` project without changing domain
behavior.

- [x] Add `[project]`, `[project.scripts]`, `[build-system]`, runtime dependencies, and dev group.
- [x] Add `.python-version` for Python 3.14.
- [x] Create `src/psammophis`, central `__main__.py`, and an initial command registry.
- [x] Move `lib/medialib` to `src/psammophis/medialib` with history preserved.
- [x] Move each feature package according to the source migration map.
- [x] Replace imports such as `from medialib` with `from psammophis.medialib`.
- [x] Replace feature-package imports in tests with `psammophis.<feature>`.
- [x] Move tests into `tests/` and remove the root import-path injection.
- [x] Preserve every existing CLI option and default.
- [x] Preserve lazy imports at the top-level dispatcher.
- [x] Update parser `prog` labels to public `psammophis <command>` names.
- [x] Get every current unit test passing from the installed project environment.
- [x] Build a wheel and verify it contains every runtime module and no skill-local test modules.

Exit criteria:

- [x] `uv run psammophis --help` lists all nine migrated feature commands (`runs` is added in
  Phase 4).
- [x] `uv run python -m psammophis --help` is equivalent.
- [x] Every command's help works without `PYTHONPATH`.
- [x] `uv build` succeeds.
- [x] A fresh temporary Python 3.14 environment can install the wheel and run every command's
  help.
- [x] Domain unit tests show no behavior regression.

### Phase 2 — Root launcher, context, and dependency cleanup

Objective: replace the old bootstrap system with root `run.sh` and runtime context independent of
source-file location.

- [x] Add executable root `run.sh` implementing the launcher contract.
- [x] Add launcher tests for direct and logical `.agents` paths.
- [x] Add shared root resolution in `psammophis.runtime.roots`.
- [x] Migrate `analyze`, `transcode`, and `track-strip` off `find_own_script_path` and
  `find_library_root`.
- [x] Test explicit flag, feature environment, shared environment, and cwd precedence.
- [x] Test dangerous-root rejection for applied commands.
- [x] Temporarily make `scripts/run-skill` forward to `run.sh` while docs/tests migrate.
- [x] Remove all per-feature `__main__.py` bootstrap code.
- [x] Remove `medialib.libroot` once no import remains.
- [x] Move `guessit` and Rich to project dependencies; remove launcher `--with` handling.
- [x] Move test tools to the dev group.
- [x] Regenerate and commit `uv.lock`; stop ignoring it.
- [x] Stop treating requirements files as canonical; retain them only as transitional inputs
  until Phase 8 updates all documentation and deletes them.

Exit criteria:

- [x] `.agents/run.sh env-check --help` works from a symlinked media root.
- [x] `./run.sh --help` works from the checkout.
- [x] Existing environment precedence tests pass.
- [x] `rg 'PYTHONPATH|find_own_script_path|find_library_root'` finds no runtime bootstrap use.
- [x] No command depends on an ancestor directory being named `.agents` inside Python.

### Phase 3 — Command result and exit-code normalization

Objective: give automation a reliable outcome contract before adding sophisticated reporting.

- [x] Introduce a small command/run summary type with counts and terminal status.
- [x] Make feature handlers return integer exit codes.
- [x] Remove routine `sys.exit()` calls from feature orchestration while keeping argparse usage
  behavior.
- [x] Make `transcode run` exit `1` when any item errors.
- [x] Make `track-strip scan/apply/transcode` exit `1` when any item errors.
- [x] Make `artwork`, `organize`, and `subtitle` batch outcomes explicit and tested.
- [x] Preserve `organize` review as a separate, non-error count.
- [x] Confirm `mkvedit`, `env-check`, and `compare` retain or improve their already
  meaningful exit behavior.
- [x] Snapshot candidate paths before mutation-capable batch loops.
- [x] Add regression tests for partial success and empty selection.

Exit criteria:

- [x] Exit-code matrix tests cover every command.
- [x] A partial batch can never exit `0` while reporting errors.
- [x] No dry-run is mislabeled as applied or changed.
- [x] Candidate snapshot tests preserve output-directory exclusion safeguards.

### Phase 4 — Event model and journal foundation

Objective: establish one typed event stream independent of subprocess syntax and presentation.

- [x] Define event types, envelope fields, status enums, and schema version `1`.
- [x] Define the sink/reporter protocol and a composite sink.
- [x] Generate sortable run IDs and strictly increasing sequence numbers.
- [x] Add JSON serialization with stable field names and omission of absent optional values.
- [x] Add global reporter/journal CLI options.
- [x] Implement atomic `status.json`, append-only `events.jsonl`, and terminal `summary.json`.
- [x] Implement default journal policy without violating strictly read-only command contracts.
- [x] Implement `runs list`, `runs show`, and `runs events` as read-only commands.
- [x] Prevent secrets and full environment dumps from entering events.
- [x] Add stale-running detection without claiming an interrupted run succeeded.
- [x] Emit run/item/phase/completion events from a fake in-process operation for end-to-end
  tests before wiring real encoders.

Exit criteria:

- [x] Schema and serialization tests pass.
- [x] Sequence numbers are monotonic under every tested path.
- [x] Controlled success, partial failure, failure, and cancellation each emit exactly one
  terminal run event.
- [x] Journal status is atomically readable while events are being appended.
- [x] A read-only command does not write a journal unless its policy or explicit flags permit it.

### Phase 5 — Long-running process supervision and encoder adapters

Objective: normalize real FFmpeg and NVEncC progress while keeping complete logs and existing
safety ordering.

- [x] Implement the shared process supervisor.
- [x] Add FFmpeg structured-progress arguments in the correct global position.
- [x] Parse FFmpeg key/value blocks into typed progress state.
- [x] Capture stderr concurrently into the existing per-file raw log.
- [x] Add and test bounded diagnostic tails on failure.
- [x] Capture, sanitize, and commit NVEncC progress fixtures.
- [x] Implement the NVEncC parser with indeterminate fallback.
- [x] Instrument AV1 phases: discovery, probe, grain measurement, encode, cover, statistics,
  verification, backup, and commit.
- [x] Ensure encoder 100% emits only phase completion, never item/run success.
- [x] Propagate cancellation and wait for child exit before cleanup.
- [x] Preserve temporary-file deletion and original-file safety on every failure path.
- [x] Keep existing full-fidelity AV1 logs and link them from events.

Exit criteria:

- [x] FFmpeg fixture tests cover normal, malformed, unknown-duration, completion, and failure
  streams.
- [x] NVEncC fixture tests cover observed real output and unknown-format fallback.
- [x] A fake long subprocess cannot deadlock by filling stderr.
- [x] Cancellation leaves the original untouched and emits a cancelled terminal event.
- [x] Verification failure after encoder 100% produces item/run failure.

### Phase 6 — Human, plain, JSONL, and quiet reporters

Objective: render the same event stream appropriately for each caller.

- [x] Implement automatic TTY detection and reporter overrides.
- [x] Implement Rich overall/current-item progress with indeterminate phases.
- [x] Route Rich through `stderr` and preserve normal `stdout` results.
- [x] Respect `NO_COLOR`, narrow terminals, and redirected output.
- [x] Implement stable plain progress lines.
- [x] Implement throttled, flushed JSONL events.
- [x] Implement quiet mode without hiding actionable errors.
- [x] Ensure raw tool chatter never appears in JSONL reporter output.
- [x] Add fake-clock tests for interval, percent-delta, phase, and heartbeat emission.
- [x] Add stream-separation tests for commands that also produce JSON results.

Exit criteria:

- [x] Interactive smoke shows overall and per-file progress without mangled warnings.
- [x] Non-TTY auto output contains no ANSI escapes or carriage-return rewrites.
- [x] Every JSONL reporter line parses as one JSON object.
- [x] The first JSONL event contains a run ID and the final event agrees with process exit.
- [x] Reporter exceptions cannot cause unsafe media commit behavior.

### Phase 7 — Progress coverage across commands

Objective: use the common event system anywhere work is long enough or batched enough to benefit.

- [x] `analyze`: per-item probe and grain-measurement phases.
- [x] `artwork`: per-item identify and download phases.
- [x] `transcode`: full phase and encode progress from Phase 5.
- [x] `env-check`: concise per-check item events (fast command; no artificial phases).
- [x] `mkvedit`: inspect, backup, edit, verify, and rollback phases.
- [x] `organize`: per-item identify and commit phases.
- [x] `compare`: preflight, probe, VMAF, SSIMULACRA2, and image phases.
- [x] `subtitle`: per-item plan and download phases.
- [x] `track-strip scan`: per-item completion and aggregate scan progress events.
- [x] `track-strip apply/transcode`: probe, remux, verify, backup, and commit phases.
- [x] Keep fast, single-step operations concise rather than manufacturing meaningless bars.

Exit criteria:

- [x] Every batch command emits consistent item indices, totals, and final counts.
- [x] Every applied command reports verification and commit as distinct phases.
- [x] Existing result JSON remains valid and separate from reporter output.
- [x] Progress does not introduce third-party dependencies into domain modules beyond the
  reporter boundary.

### Phase 8 — Documentation, skills, and legacy cleanup

Objective: make the new interface canonical and remove the obsolete architecture.

- [x] Update README application installation and human invocation examples.
- [x] Rewrite AGENTS repository layout, dependency workflow, entrypoint, and test instructions.
- [x] Update every scripted `SKILL.md` from `.agents/scripts/run-skill` to `.agents/run.sh`.
- [x] Update skill source-path references from `scripts/<package>` to `src/psammophis/<package>`.
- [x] Add JSONL invocation/monitoring/completion instructions to long-running skills.
- [x] Tell skills not to relaunch work when a session is merely quiet.
- [x] Tell skills to require terminal event plus exit-code agreement.
- [x] Update incident/reference links that mention old implementation paths while preserving
  the incident facts.
- [x] Update env-check's Python dependency and development-tool expectations.
- [x] Delete the transitional `scripts/run-skill` forwarder.
- [x] Delete empty skill `scripts/` directories.
- [x] Verify obsolete per-feature entrypoint shims and root conftest were removed in the earlier
  migration phases.
- [x] Delete obsolete requirements files.
- [x] Search for stale `run-skill`, `skills/*/scripts`, `lib/medialib`, and direct-path
  invocation documentation.

Exit criteria:

- [x] README, AGENTS, and all skills describe the same canonical commands.
- [x] Final tree matches the target layout.
- [x] No runtime Python code remains under skill directories.
- [x] No stale public invocation appears except in clearly labeled historical migration notes.

### Phase 9 — Final verification and controlled rollout

Objective: prove packaging, compatibility, progress, and media safety together.

- [x] Run the complete quality gate from a clean project environment.
- [x] Build sdist and wheel.
- [x] Install the wheel into a fresh temporary Python 3.14 environment.
- [x] Run all top-level and nested help commands from the installed executable.
- [x] Run the root launcher through a logical `.agents` symlink.
- [x] Validate TTY, plain, JSONL, and quiet reporters.
- [x] Validate every exit-code category.
- [x] Validate journal recovery/status inspection during a running fake or short job.
- [x] Run dry-run smoke tests for every mutation-capable command against temporary fixtures.
- [x] Run a short FFmpeg CPU AV1 encode to a separate output directory and verify the output.
- [x] Run a short FFmpeg NVENC encode when compatible hardware is present.
- [x] Run a short NVEncC dynamic-metadata encode when compatible source/hardware is present.
- [x] Confirm encoder completion is followed by visible verification and commit phases.
- [x] Confirm current backup/cache/log defaults are exact and no real pre-refactor state is
  migrated or deleted implicitly.
- [x] Review `git diff` for accidental domain/preset/policy changes.

Exit criteria:

- [x] All automated gates pass.
- [x] Required real-backend smoke tests pass or unavailable hardware cases are explicitly
  documented rather than silently skipped.
- [x] No real original was deleted during rollout testing.
- [x] The final installed app, checkout launcher, human UI, and agent protocol all use the same
  command implementation.

## Master phase tracker

| Phase | Deliverable | Status | Depends on |
| --- | --- | --- | --- |
| Plan | Architecture and trackers in this document | `[x]` | — |
| 0 | Baseline and contract fixtures | `[x]` | Plan |
| 1 | Packaged source tree and central CLI | `[x]` | 0 |
| 2 | Root launcher and runtime context | `[x]` | 1 |
| 3 | Reliable result/exit contracts | `[x]` | 2 |
| 4 | Typed events and durable journal | `[x]` | 3 |
| 5 | Process supervisor and encoder parsers | `[x]` | 4 |
| 6 | Human/plain/JSONL reporters | `[x]` | 4, 5 |
| 7 | Progress across all relevant commands | `[x]` | 6 |
| 8 | Documentation and legacy cleanup | `[x]` | 1–7 |
| 9 | Final validation and rollout | `[x]` | 8 |

## Per-command tracker

| Command | Moved | Help parity | Exit contract | Events | Human progress | JSONL | Skill updated | Smoke |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `analyze` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` |
| `artwork` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` |
| `transcode` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` |
| `env-check` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` |
| `mkvedit` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` |
| `organize` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` |
| `compare` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` |
| `runs` | n/a | `[x]` | `[x]` | `[x]` | n/a | `[x]` | n/a | `[x]` |
| `subtitle` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` |
| `track-strip` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` | `[x]` |

## Compatibility tracker

- [x] Final public names are only `transcode` and `compare`; tests reject the removed names.
- [x] All existing flags preserved or intentionally deprecated with documentation.
- [x] Environment-variable precedence preserved under the final `TRANSCODE_*` prefix.
- [x] Existing `.env` parsing behavior preserved.
- [x] `.cache/transcode/originals` is the sole transcode backup default and purge target.
- [x] `.cache/transcode/logs` is the sole transcode log default.
- [x] The transcode log path remains live and useful with `tail -f` during an encode.
- [x] Existing track-strip backup directory recognized.
- [x] Existing track-strip scan cache recognized.
- [x] Existing dry-run defaults preserved.
- [x] Existing verification gates preserved.
- [x] Existing output-directory collision behavior preserved.
- [x] Existing logical `.agents` library-root behavior preserved by `run.sh`.
- [x] Installed `psammophis` has a documented cwd/`--root` behavior.
- [x] Result stdout remains compatible where commands already expose JSON.
- [x] Intentional exit-code tightening documented.

## Validation tracker

| Check | Baseline | After package move | Final | Notes |
| --- | --- | --- | --- | --- |
| `uv run ruff check .` | `[x]` | `[x]` | `[x]` | |
| `uv run ruff format .` | `[x]` | `[x]` | `[x]` | Record whether files changed. |
| `uv run basedpyright .` | `[x]` | `[x]` | `[x]` | |
| `uv run pytest` | `[x]` | `[x]` | `[x]` | |
| `uv build` | n/a | `[x]` | `[x]` | |
| Fresh-wheel install | n/a | `[x]` | `[x]` | Python 3.14. |
| Installed command help matrix | n/a | `[x]` | `[x]` | |
| `.agents/run.sh` symlink smoke | n/a | `[x]` | `[x]` | |
| Plain/non-TTY reporter | n/a | n/a | `[x]` | No ANSI/CR. |
| JSONL schema/terminal event | n/a | n/a | `[x]` | Parse every event. |
| SIGINT/SIGTERM handling | n/a | n/a | `[x]` | Process-group cancellation, rollback, and terminal-event tests. | Original remains safe. |
| FFmpeg CPU short encode | n/a | n/a | `[x]` | Separate output directory. |
| FFmpeg NVENC short encode | n/a | n/a | `[x]` | If hardware available. |
| NVEncC metadata short encode | n/a | n/a | `[x]` | If source/hardware available. |

## Risk register

| ID | Risk | Severity | Mitigation | Status |
| --- | --- | --- | --- | --- |
| R-001 | Mechanical moves accidentally alter codec/policy behavior. | High | Separate move commits, baseline fixtures, full tests, real short encode review. | Mitigated |
| R-002 | New root resolution points an applied operation at the wrong directory. | Critical | Explicit precedence, source recorded in events, broad-root rejection, symlink launcher tests, dry-run default. | Mitigated |
| R-003 | Renamed cache paths orphan backups or make purge target the wrong directory. | Critical | D-017 accepts the breaking cutover; exact `.cache/transcode` defaults and purge targets are tested, with no implicit migration. | Accepted |
| R-004 | Progress UI reports success before verification or final move. | Critical | Event phase model and terminal-event ordering tests. | Mitigated |
| R-005 | A subprocess pipe fills and deadlocks a multi-hour encode. | High | Concurrently drain both streams; stress test with a noisy fake process. | Mitigated |
| R-006 | NVEncC changes its human progress format. | Medium | Fixture-based adapter, tolerant parser, indeterminate heartbeat fallback, raw logs. | Mitigated |
| R-007 | JSONL is polluted by Rich/encoder output and becomes ambiguous to a model. | High | Strict stderr reporter boundary, raw logs on disk, parse-every-line tests. | Mitigated |
| R-008 | Batch command prints errors but exits `0`. | High | Shared outcome contract and per-command partial-failure tests. | Mitigated |
| R-009 | Journal writes violate a skill's read-only promise. | Medium | Policy-based default journaling and explicit opt-in for strict no-write commands. | Mitigated |
| R-010 | Reporter or journal failure compromises media transaction ordering. | Critical | Reporter isolation; fail before mutation when required; transactional tests. | Mitigated |
| R-011 | Candidate enumeration sees outputs written during the same run. | Critical | Snapshot candidates before mutation and retain resolved directory exclusions. | Mitigated |
| R-012 | Installed wheel works differently from the checkout. | High | Fresh-wheel smoke plus root launcher tests; no source-tree path imports. | Mitigated |
| R-013 | Rich behaves badly in pipes, narrow terminals, or `NO_COLOR`. | Low | Auto TTY detection, plain fallback, focused renderer tests. | Mitigated |
| R-014 | Lockfile or build backend silently selects Python below 3.14. | High | `[project].requires-python >=3.14`, `.python-version`, locked project, wheel smoke. | Mitigated |
| R-015 | Skill instructions relaunch an already-running encode. | Critical | Run IDs, journal/status commands, explicit same-session monitoring instructions. | Mitigated |
| R-016 | Moving an original away before installing verified output leaves an empty media path during interruption or power loss. | Critical | Stage a hard-linked or complete fsynced backup while the source remains live, then atomically install; transaction and cancellation tests cover failure windows. | Mitigated |

## Investigation tracker

These are implementation investigations, not unresolved product choices.

- [x] Capture the installed NVEncC version and its real periodic progress format.
- [x] Determine which NVEncC stream carries progress versus diagnostic output.
- [x] Verify FFmpeg `-progress` output keys on the installed FFmpeg build.
- [x] Confirm Rich rendering and signal behavior through `uv run` and the root `exec` launcher.
- [x] Inventory whether any local automation outside the repository still calls
  `.agents/scripts/run-skill` before deleting the transitional forwarder.
- [x] Inventory existing legacy backup/cache directories read-only before rollout.
- [x] Decide a bounded `uv_build` requirement compatible with the uv version used at
  implementation time and record it in the decision log.

## Recommended commit boundaries

1. `docs: add psammophis refactor plan`
2. `test: capture pre-refactor CLI and progress contracts`
3. `build: define packaged psammophis project`
4. `refactor: move shared medialib under psammophis`
5. `refactor: move feature packages and tests under psammophis`
6. `refactor: add root run.sh and remove path bootstraps`
7. `fix: normalize command exit statuses and snapshot batch inputs`
8. `feat: add typed run events and journal`
9. `feat: supervise encoders with structured progress adapters`
10. `feat: add Rich, plain, and JSONL reporters`
11. `feat: instrument remaining batch commands`
12. `docs: switch skills and repository guidance to psammophis`
13. `chore: remove legacy launcher and dependency files`

Commits may be split further, but do not collapse all source moves, behavior changes, UI work,
and documentation into one unreviewable change.

## Implementation log

Append entries as work progresses.

| Date | Phase | Change or finding | Validation | Commit/PR |
| --- | --- | --- | --- | --- |
| 2026-07-31 | Plan | Initial detailed plan created from repository audit and agreed naming/progress direction. | Command/package coverage checked; code fences balanced; whitespace check clean. | — |
| 2026-07-31 | 0–9 | Implemented full packaging refactor: `src/psammophis` app, `run.sh`, events/journal/reporters/process supervisor, exit-code normalization, skill docs, legacy cleanup. Baseline under `baseline/`. | pytest 433 passed; ruff/basedpyright clean; wheel install help matrix; JSONL dry-run; short CPU AV1 encode to separate output dir; `.agents` symlink launcher smoke. | — |
| 2026-07-31 | Review | Deep post-implementation hardening: process-group cancellation and CR progress parsing, reliable journals/reporters, complete command instrumentation, protected purge roots, atomic media/sidecar/cache writes, staged backups without a delete gap, batch collision checks, and corrected documentation. | Full final gate recorded below. | — |
| 2026-08-01 | Final verification | Completed the rename cleanup and final safety pass; `transcode` and `compare` are the only current public command names, with no compatibility aliases or legacy namespace fallback. | `ruff check .`; `basedpyright .`; `pytest` 513 passed; `uv build`; fresh-wheel Python 3.14 smoke for `psammophis`, `transcode`, and `compare`; old command names rejected with exit 2; `run.sh --version`; `git diff --check`. | — |

## Decision log

Append only material deviations or clarifications; do not rewrite history silently.

| Date | ID | Decision | Reason | Consequences |
| --- | --- | --- | --- | --- |
| 2026-07-31 | D-001–D-015 | Initial decisions recorded in the locked-decision table. | Capture the agreed target before implementation. | Implementation starts at Phase 0; no application code changed by this plan. |
| 2026-07-31 | D-016 | Use `uv_build>=0.9.30,<0.13.0` as the PEP 517 backend. | Compatible with installed uv 0.12.0 and published uv_build 0.9.30. | Recorded in pyproject.toml `[build-system]`. |
| 2026-07-31 | D-017 | Make `transcode` and `compare` the only public/skill names, and make `TRANSCODE_*` plus `.cache/transcode/` the only current transcode configuration/state namespace. | The user explicitly chose a clean break and rejected compatibility mode. | Supersedes the relevant parts of D-004 and D-012; no aliases, old-prefix fallback, old-cache discovery, or automatic migration. Historical baseline artifacts retain their original names. |
| 2026-07-31 | D-018 | A retained original is staged before replacement rather than moved away before commit. | Rollback after a move still leaves a crash/power-loss gap, especially across filesystems. | Same-filesystem backups use hard links; cross-filesystem backups use exclusive fsynced copies; verified output then lands atomically. |
