---
name: env-check
description: Use when the user wants to know whether this machine has everything the other skills in this repo need before running them - missing binaries (ffmpeg, ffprobe, mkvmerge, mkvpropedit, nvencc, dovi_tool, hdr10plus_tool, stash-mcp), the wrong Python version, an AV1-incapable or absent GPU, missing dev tools (ruff, basedpyright, pytest), or missing Python packages/credentials (guessit, TMDB/OpenSubtitles API keys). Triggers on phrases like "is my machine set up right", "check dependencies", "what's missing", "am I ready to run av1-transcode/organize/mkvedit", "check for ffmpeg/mkvmerge/nvencc", or moving this repo to a new machine. Read-only - never installs or changes anything, only reports and suggests.
allowed-tools:
    - Bash
metadata:
    author: "env-check"
    focus: "Read-only hardware/software prerequisite audit across every skill in this repo"
---

# env-check

Every other skill here assumes a handful of external binaries, Python packages, and (for `av1-transcode`'s GPU path) specific hardware are already present on the machine it runs on -- and each one fails in its own way, at its own time, when one of those is missing (an `av1-transcode` run that dies partway through the first file because `nvencc` isn't installed, an `organize` run that errors on a missing `TMDB_API_KEY`, `mkvedit` failing without `mkvpropedit`, or `trackstrip apply` failing without `mkvmerge`). This skill checks all of it up front, in one pass, before any of that.

It only ever reads: `shutil.which`, `--version`/`-h`-style subprocess calls, `nvidia-smi`, and Python's own `import`/`os.environ`. It never installs, writes, or deletes anything -- every finding comes with a plain-language suggestion for what to install or configure, but acting on that suggestion is left to the user.

## Running it

```bash
.agents/scripts/run-skill env-check
```

Prints one line per check, grouped by which skill needs it, `OK` (found) / `!!` (missing and required) / `..` (missing but optional -- that skill still works without it, just with reduced functionality), followed by a summary line. Exits `0` if every *required* prerequisite was found, `1` otherwise (so it's usable as a pre-flight gate in a script, not just for a human to read).

```bash
.agents/scripts/run-skill env-check --category av1-transcode   # only that skill's checks
.agents/scripts/run-skill env-check --required-only             # hide optional/nice-to-have checks
```

## What gets checked, and why each one matters

| Category | Checks | Required? |
|---|---|---|
| `runtime` | Python >= 3.14, `pip` or `uv`, `ruff`, `basedpyright`, `pytest` | Python: yes (hard repo-wide requirement, see root `AGENTS.md`). The rest: no -- only needed for *developing* a skill, not running one. |
| `shared` | `ffmpeg`, `ffprobe` | Yes -- both `track-strip` and `av1-transcode` shell out to these directly. |
| `track-strip` | `mkvmerge` | Yes -- `apply`/`transcode`'s track-selection backend has no fallback. |
| `mkvedit` | `mkvmerge`, `mkvpropedit` | Yes -- inspection and in-place editing have no fallback. |
| `av1-transcode` | ffmpeg's `libsvtav1` and `av1_nvenc` encoders, an AV1-capable NVIDIA GPU, `nvencc`, `dovi_tool`, `hdr10plus_tool` | `libsvtav1`: yes (the only backend that can re-inject Dolby Vision RPU, and the CPU fallback path). Everything else: no -- GPU/`nvencc`/`dovi_tool`/`hdr10plus_tool` are all optional speed/metadata conveniences with a documented fallback (see `av1-transcode/reference/incidents.md`). |
| `organize` | `guessit`, `ORGANIZE_TMDB_API_KEY` or `TMDB_API_KEY` | Yes. |
| `artwork` | `ARTWORK_TMDB_API_KEY` or `TMDB_API_KEY` | Yes. |
| `subtitle` | `SUBTITLE_OPENSUBTITLES_API_KEY` | Optional until a fetch is requested. |
| `stash-app` | `stash-mcp` | No -- only relevant if that MCP server is actually configured for this session (see root `mcp_config.json`). |

The AV1-capable-GPU check reuses `av1-transcode`'s own capability probe (`medialib.gpu`, shared rather than re-implemented here -- see root `AGENTS.md`'s `lib/medialib` section for why duplicating this exact check would be the wrong move): it hands a real GPU a trivial `av1_nvenc` encode rather than guessing from the GPU's name, since this library's own machine has both an encode-capable RTX 4080 and a decode-only RTX 3060 side by side.

## Extending

`scripts/envcheck/checks.py` holds one `check_*` function per prerequisite, each doing its own I/O and returning a `CheckResult` (name, category, required, found, detail, install_hint); `all_checks()` is the single list every one of them is registered in. `report.py` is pure formatting/grouping logic over that list (no I/O), the only part of this skill with real unit-test coverage (`scripts/test_report.py`) -- most of the rest is one-line-per-check I/O that's hard to usefully unit test beyond what `lib/test_gpu.py` already covers for the shared GPU probe. Add a new check by writing one `check_*` function and appending its call to `all_checks()`; pick `required=True` only if the skill it belongs to cannot function at all without it (a slower fallback path, like GPU or `nvencc`, is not required).
