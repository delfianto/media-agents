---
name: psm-abtest
description: Use when the user wants a scientific encoder A/B — compare two or more SVT-AV1 / svt-av1-hdr recipes on the same source by cutting short lossless clips, encoding each clip with every recipe, and scoring them with compare (VMAF/SSIMULACRA2). Use for preset audits, CRF/grain/tune bake-offs, or proving a settings change before editing av1_presets.py. Applied `--yes` work MUST use the global `--systemd` flag. Never mutates the original file.
allowed-tools:
    - Bash
metadata:
    author: "abtest"
    focus: "clip-aligned encoder recipe A/B using compare"
---

# abtest

Decide whether an encoder recipe is better than another by measuring both against the **same** lossless clip, not by seeking independently into two long muxes. Implementation: `src/psammophis/abtest/`. Scoring is `psammophis.compare.video.compare_video_files` — the same path as `compare`.

```bash
.agents/run.sh abtest \
  --source "studio/Title.mp4" \
  --variant current=2160p-film \
  --variant cand=2160p-film,crf=24,grain=0 \
  --at 55,326,620 \
  --clip-duration 20 \
  --compare-mode full
```

Defaults to a **dry-run** that prints windows, resolved settings, and one encode command. Pass `--yes` to extract FFV1 refs, encode, and score. **`--yes` runs on systemd** (global flag, before `abtest`) — same rule as transcode. Do not hold a chat-tool timeout over it.

```bash
.agents/run.sh --systemd --reporter jsonl abtest \
  --source "studio/Title.mp4" \
  --variant current=2160p-film \
  --variant cand=2160p-film,crf=24,grain=0 \
  --yes
```

Writes only under `--output-dir` (default `<root>/.cache/psammophis/abtest/<stem>-<utc>/`). Watch with `systemctl --user status psammophis-abtest.service` or `.agents/run.sh runs unit psammophis-abtest`.

## Variant specs

Repeat `--variant id=...` (minimum two):

- `id=2160p-film` — table preset as-is
- `id=2160p-film,crf=26,grain=0,denoise=0` — named preset plus overrides
- `id=t5=crf=30,preset=2,tune=5,grain=0` — raw encoder keys (`crf`, `preset`, `tune`, `grain`, `denoise`)

`--profile` supplies the table preset when a variant does not name one. CRF is the value passed to ffmpeg (svt-av1-hdr scale on this machine).

## Clip windows

- `--at 55,326,620` — explicit starts (seconds)
- otherwise `--clips N` stratified across the source (default 6)
- `--clip-duration` default 20s

Prefer `--at` on known hard timestamps (close-ups, grain, dark, motion). `--compare-mode full` (default) scores every frame of each short clip. Use `deep` only if clips are long.

## Integrity

1. Source is never remuxed, renamed, or replaced.
2. Every variant of a window is encoded from the **same** FFV1 reference. Do not A/B by `-ss` into the original and a finished encode separately — that is how earlier full-file VMAF tails went to 0.
3. Accept success only when the process exit code is 0. Encoder percent is not success.
4. Prefer VMAF **p5/min** and SSIMULACRA2 over the mean. Inspect worst stills before changing `av1_presets.py`.
5. Film-grain synthesis can tank full-reference scores while looking acceptable.

After `--yes`, read `rollup.json` and `reports/*.json`. Quiet intervals are not a reason to relaunch. Success is `run.completed` plus exit code 0, not encoder percent.

## Related

`compare` for a single already-encoded pair. `analyze` / `transcode probe` for what the current table would pick. Change presets only after an `abtest --yes` rollup you have actually read.
