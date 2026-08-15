# Source-class and preset audit (2026-08)

Evidence log for the three-axis encoder table: resolution × film/anime ×
**source class** (master / webrip / stream / lowest). The executable numbers
live in `src/psammophis/medialib/av1_presets.py` and
`src/psammophis/medialib/sourceclass.py`. This note is the measured story
behind those numbers — remux anime, grain-free 4K film, mid AVC webrips, and
Video DownloadHelper webcam captures — and what still needs a follow-up
before we touch a delta again.

Related: `notes/av1-encoding.md` (policy + current table),
`notes/quality-comparison.md` (how `compare` scores).

## Success criterion

The pass/fail bar is **transparent or near-transparent against the source
you actually have**. Size is a consequence, not the goal.

| Source you have | What "transparent" means | What failure looks like |
| --- | --- | --- |
| UHD remux (HEVC 10-bit, 40–80+ Mb/s) | Remux-transparent: painted texture, sparkle, lace hold at 1:1 | CQ 24 on Violet Evergarden: 1.46 Mb/s, grass/water gone |
| 4K AVC webrip (~20 Mb/s) | Transparent *to that AVC*. You cannot invent remux detail | Uncapped CQ 22 wanted 40–60 Mb/s from a 21 Mb/s file |
| Streaming / CBP rung | Do not make the mush worse, do not spend remux bits | Master CRF/CQ on Constrained Baseline 1080p |
| Webcam / cam / vintage / legacy | Same as stream, cheaper. Source *is* the ceiling | Treating 480×854 HLS as 1080p remux |

Judge with `compare` / `abtest` on multiple windows. Prefer VMAF **p5/min**
and SSIMULACRA2, then 1:1 stills of the worst frame. Mean VMAF alone has
hidden every failure in this audit.

Do **not**:

- treat source bitrate as a quality target (62 Mb/s remux is "what the disc
  stored," not "what the picture needs")
- treat a tiny CQ-driven bitrate as efficiency if the stills lost texture
- change a table cell from a single window or a single title
- relaunch work because a JSONL interval was quiet

## How we measure

- Lossless FFV1 windows first. Every variant of a window is encoded from the
  **same** reference. Independent `-ss` into a remux and a finished encode
  is how earlier full-file VMAF tails went to 0.
- `--mode full` on the short clips (every frame). `deep` only if clips are
  long.
- SDR VMAF (`vmaf_v0.6.1`) is subjectively calibrated. Native PQ VMAF is a
  repeatable regression signal but **not** subjectively calibrated — reports
  must say so. Isolated SSIMULACRA2 mins of 17–34 with mid-95s VMAF at the
  same timestamp are still-seek cut mismatches, not encoder collapse.
- Grain score is `1 − hqdn3d denoise-diff SSIM` (`medialib.grain`).
  Threshold `0.012` routes `auto` to CPU. Webcam captures in this set scored
  0.006–0.008 — compression mush, not film grain.

Artifacts:

| Audit | Path |
| --- | --- |
| Violet Evergarden remux vs NVENC | `.cache/psammophis/compare/ve-remux-vs-nvenc/` |
| WowGirls / MetArtX NVENC + CPU | `.cache/psammophis/compare/nvenc-tier-audit/` |
| Lab Mutation CRF / grain / tune | `.cache/psammophis/compare/ab-lab-mutation/` |
| VDH lowest-class probe + ladder | `.cache/psammophis/compare/vdh-lowest/` |

## Three axes

1. **Resolution tier** — `resolution_tier(height)`: `≥1600 → 2160p`,
   `≥800 → 1080p`, `≥540 → 720p`, else `sd`. Written for landscape
   movies/TV. Portrait webcam height inflates the tier (see VDH).
2. **Content profile** — explicit `film` / `anime` / `*-fast`. Not
   auto-detected (Japanese audio is not animation).
3. **Source class** — auto from codec / profile / bitrate vs the tier's
   floors. Override with `--source-class`. This is the axis that was
   missing when one 4K-film CQ was asked to serve remuxes, 21 Mb/s AVC, and
   HLS recordings.

HDR changes signaling and metadata, not CRF/CQ.

## Current master table

Calibrated against remux / clean-downscale pictures. Non-master classes add
the deltas in the next section.

| Source | SVT preset | Mainline CRF | `svt-av1-hdr` CRF | Tune | FGS | NVENC CQ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2160p film | 5 | 19 | 26 | 0 | 0 | 22 |
| 2160p film-fast | 6 | 19 | 26 | 0 | 0 | 22 |
| 2160p anime | 5 | 22 | 29 | 1 | 0 | **16** |
| 2160p anime-fast | 6 | 22 | 29 | 1 | 0 | **16** |
| 1080p film | 4 | 13 | **20** | 0 | 0 | **22** |
| 1080p anime | 4 | 25 | 32 | 1 | 0 | 27 |
| 720p film | 5 | 15 | **22** | 0 | 0 | **24** |
| 720p anime | 5 | 27 | 34 | 1 | 0 | 29 |
| SD film | 6 | 17 | **24** | 0 | 0 | **24** |
| SD anime | 6 | 28 | 35 | 1 | 0 | 31 |

Bold cells moved in this audit. 1080p/720p/sd **anime** CQ/CRF were not
re-measured — do not copy the film step onto anime.

GPU: `av1_nvenc` p7, QVBR (`-rc vbr -cq N -b:v 0`), multipass fullres,
lookahead 32, `b_ref_mode=middle`, spatial/temporal AQ from the preset.
QVBR `--qvbr N` is `--vbr 0 --vbr-quality N` — a 0–63 quality stop, **not**
a fraction of the remux bitrate. That is why a 62 Mb/s HEVC remux can
legitimately become 1.5 Mb/s AV1 if CQ is too high: the encoder stopped
because it thought it hit the score, not because it spent 2% of 62.

NVEncC (Dolby Vision / HDR10+) must carry the same tune / lookahead / AQ /
B-ref extras as the ffmpeg path. Omitting them is how Violet Evergarden
logged `Lookahead off / AQ off / Tuning hq`.

Default video cap is 85% of source video bitrate (stream 70%, lowest 60%).
On a 40–80 Mb/s remux the cap does not bind. On mid AVC it does.

## Source-class deltas (starting points)

| Class | Detection | CRF Δ | CQ Δ | Cap |
| --- | --- | ---: | ---: | ---: |
| master | 10-bit HEVC/AV1 (or very high-bitrate AVC) at/above floor | 0 | 0 | 85% |
| webrip | between stream ceiling and master floor | +3 | +4 | 85% |
| stream | Baseline/Constrained, or bitrate ≤ stream ceiling | +6 | +8 | 70% |
| lowest | legacy codec, or bitrate ≤ lowest ceiling | +10 | +12 | 60% |

Floors/ceilings (video bps):

| Tier | Master floor | Stream ceiling | Lowest ceiling |
| --- | ---: | ---: | ---: |
| 2160p | 35 Mb/s | 8 Mb/s | 3 Mb/s |
| 1080p | 12 Mb/s | 3.5 Mb/s | 1.5 Mb/s |
| 720p | 8 Mb/s | 1.8 Mb/s | 0.8 Mb/s |
| sd | 4 Mb/s | 1.2 Mb/s | 0.6 Mb/s |

Real-file classifications that drove the axis:

| File | Probe | Class |
| --- | --- | --- |
| Violet Evergarden UHD remux (HEVC 10-bit ~62 Mb/s) | ≥ 35 Mb/s + 10-bit HEVC | **master** → CQ 16 |
| WowGirls 4K AVC High 8-bit ~21 Mb/s | between 8 and 35 | **webrip** → CQ 22+4 = 26 |
| MetArtX 4K AVC High ~25 Mb/s | same | **webrip** → CQ 26 |
| MetArtX native 1080p Constrained Baseline ~5 Mb/s | Baseline | **stream** → CQ 22+8 = 30 |
| MetArtX native 480p ~1 Mb/s | ≤ stream ceiling | **stream** → CQ 24+8 = 32 |
| VDH 480×854 ~0.95 Mb/s (height-tier 1080p) | ≤ 1.5 Mb/s 1080p | **lowest** → CQ 22+12 = 34 |
| VDH 1080×1920 ~3.5 Mb/s (height-tier 2160p) | ≤ 8 Mb/s 2160p | **stream** → CQ 22+8 = 30 |

webrip +4 CQ is evidenced by the WowGirls cap-bind (uncapped CQ 22 overspends
the 21 Mb/s source; +4 aims under the source while remaining
transparent-to-that-AVC). stream / lowest deltas were reasoned from "do not
spend master bits reconstructing mush" and are what the VDH ladder below
tests.

---

## 1. Violet Evergarden — 4K anime remux vs NVENC

**Reference:** `ingest/` HEVC remux, 3840×2160 coded, crop 276+276 →
3840×1608 active, ~62.1 Mb/s, HDR / DV, 23.976 fps.

**Distorted (shipped):** `anime/Violet Evergarden The Movie (2020).mkv`,
NVEncC `--qvbr 24`, video **1.46 Mb/s**, baked 3840×2160 canvas. Compare
crops NVENC to `3840:1608:0:276` so only the active picture is scored.
First-frame PSNR ~58 dB confirmed alignment after that crop.

Six random 20 s windows (seed 20260815), `--mode full`.

### Shipped CQ 24 (failed)

| Window | Start | VMAF mean | p5 | S2 mean |
| --- | ---: | ---: | ---: | ---: |
| r01 | 841.98 | 95.74 | 95.21 | 77.49 |
| r02 | 3731.19 | 96.00 | 94.81 | 81.48 |
| r03 (painted cliff) | 4119.21 | **95.03** | **93.70** | **75.36** |
| r04 | 4429.88 | 95.61 | 94.97 | 81.42 |
| r05 | 6314.76 | 95.74 | 95.39 | 82.69 |
| r06 | 7470.58 | 95.86 | 95.22 | 80.39 |
| **mean** | | **95.66** | **94.88** | **79.80** |

1:1 stills lost grass brushwork and water sparkle on the cliff window. The
preset **failed** the remux-transparency bar even though the file was ~40×
smaller. Tiny bitrate is not a quality win.

### Why 1.46 Mb/s from 62 Mb/s is not a paradox

AV1 NVENC CQ is a quality stop, not "spend N% of source." Anime + letterbox
+ flat paint + CQ 24 → the encoder is done at ~1.5 Mb/s. The 62 Mb/s remux
is disc-wasteful HEVC, not a picture that *needs* 62.

Turning AQ/lookahead/uhq **on at CQ 24** only moved cliff S2 75 → 78 and
made the worst-frame tail worse. Extras are not a substitute for the CQ
number. They *are* required so NVEncC matches the ffmpeg path.

### CQ ladder on the cliff + six-window CQ 16

Same cliff clip: CQ 20 → VMAF 95.83 / S2 79.24 (still short). CQ 16 →
VMAF 96.28 / p5 95.33 / S2 82.00. 1:1 grass and cliff hold the remux
paint. Matches the CPU anime remux-bar VMAF (~96.25) and is within ~2 S2 of
that CPU recipe (83.94).

CQ 16 + AQ/lookahead/uhq on all six windows (the `cq-cand` set):

| Window | VMAF mean | p5 | min | S2 mean | encode bps |
| --- | ---: | ---: | ---: | ---: | ---: |
| r01 | 96.77 | 96.22 | 95.57 | 82.56 | 4.08 Mb/s |
| r02 | 96.83 | 95.90 | 94.33 | 85.66 | 3.11 |
| r03 | 96.37 | 95.35 | 90.92 | 81.07 | 4.28 |
| r04 | 96.66 | 96.25 | 95.01 | 86.45 | 3.35 |
| r05 | 96.79 | 96.46 | 95.05 | 86.66 | 4.03 |
| r06 | 96.53 | 95.61 | 94.65 | 84.73 | 6.02 |
| **mean** | **96.66** | **95.97** | | **84.52** | **~4.1** |

Isolated S2 mins of 17–34 are still-seek cut mismatches (VMAF at those
timestamps stays mid-95s).

**Decision:** 2160p-anime / anime-fast GPU CQ **16**. Expected video rate on
this title is roughly 3–5 Mb/s, not 1.5. Class = master.

---

## 2. Lab Mutation — film CRF, grain, tune 5

Six-clip full-frame A/B on a digital 4K close-up (Hentaied *The Lab
Mutation*). Fair VMAF was already 95+ on the old recipe.

- `film-grain=10` + `film-grain-denoise=1` smeared pores / specularity.
  That was the smear, not CRF 27 by itself.
- CRF 26 (svt-av1-hdr) / 19 (mainline) on 2160p film is one point more
  transparent than the previous 27/20 pair, with more residual detail.
- Film-grain synthesis stays **off** by default. Do not turn FGS back on
  as a size trick without a per-title grain check.
- Doom9 May 2025 "use tune 3" is stale. In that first HDR-fork post, tune 3
  *was* the film-grain retention tune. After MS_SSIM it was renumbered to
  **tune 5**; tune 3 is now IQ / still-image / AVIF. Tune 5 + CRF 30 +
  preset 2 was slower and larger with worse SSIMULACRA2 than tune 0 / CRF 26
  / no FGS on this digital 4K.

Preset-speed sweeps (separate, 3-minute clips) raised 2160p film and anime
from SVT preset 4 → 5, added `*-fast` at 6, and rejected 7 (worse
bits-per-quality). Not extrapolated to 1080p/720p/sd.

---

## 3. WowGirls / MetArtX — grain-free film, every resolution

Sources (both AVC High 8-bit, grain below the CPU-routing threshold):

- WowGirls *Hypnotic Whisper Of Euphoria* — 3840×2160, ~21 Mb/s, 59.94 fps,
  grain 0.0106.
- MetArtX *Kitchen Cravings 2* 4K — 3840×2160, ~25 Mb/s, 29.97 fps, grain
  0.0074.

Native MetArtX 1080p is Constrained Baseline ~5 Mb/s and native 480p is
~1 Mb/s — already-crushed web rungs, **not** masters. 1080p / 720p / 480p
CQ and CRF were judged on **lanczos downscales of the 4K windows** so the
source picture was still clean.

Four 10 s windows per title. Uncapped `av1_nvenc` / `libsvtav1`. SDR VMAF.

### NVENC

Means across the four MetArtX windows (WowGirls 2160p CQ 22 also listed):

| Tier | Old CQ | VMAF / p5 / S2 | New CQ | VMAF / p5 / S2 |
| --- | ---: | --- | ---: | --- |
| 2160p (WowGirls) | 22 | 99.13 / 98.74 / 81.85 | **22 keep** | — |
| 2160p (MetArtX) | 22 | 99.80 / 99.00 / 82.88 | **22 keep** | — |
| 1080p | 26 | 98.51 / 96.14 / 81.21 | **22** | 99.00 / 96.82 / 83.01 |
| 720p | 28 | 97.80 / 95.31 / 82.17 | **24** | 98.55 / 96.18 / 84.21 |
| sd | 30 | 96.86 / 94.56 / 80.83 | **24** | 98.31 / 96.17 / 84.71 |

2160p CQ 22 holds lace/skin at 1:1. Old 1080p/720p/sd CQ was short on
sweater fuzz and low-percentile VMAF. New stops clear the bar.

Caveat: uncapped CQ 22 on WowGirls 60 fps 4K wanted **~40–60 Mb/s** against
a 21 Mb/s source. The 85% cap **will bind** and will pull quality below
these uncapped scores. That is a source-class problem, not a reason to
raise CQ on remuxes.

### CPU (`svt-av1-hdr`)

| Tier | Old CRF | Result | New CRF (hdr / mainline) | Result |
| --- | ---: | --- | ---: | --- |
| 2160p | 26 | VMAF 97.3–99.7 / p5 ≥96.3 / S2 76–80. Lace slightly softer than NVENC CQ 22. CRF 20 only +~2 S2 for +50% size | **26 / 19 keep** | diminishing returns vs a 21 Mb/s 60 fps AVC "master" |
| 1080p | 31 | p5 ~93 / S2 ~75 — fail | **20 / 13** | p5 96.3 / S2 79.5 (MetArtX crf20) |
| 720p | 33 | p5 ~92 / S2 ~75 — fail | **22 / 15** | p5 95.9 / S2 80.2 |
| sd | 35 | p5 ~91 / S2 ~74 — fail | **24 / 17** | p5 95.7 / S2 80.4 |

The old 1080p/720p/sd film CRF values were catalog leftovers and **failed**
the transparency bar on a clean downscale. 2160p CRF 26 still stands: the
S2 lag versus NVENC CQ 22 on this grain-free AVC is not worth 50% more
bits. On a real 60 Mb/s remux the Lab Mutation A/B is the better 2160p
anchor.

---

## 4. VDH — lowest-class webcam corpus

Directory: `/mnt/exos1/downloads/VDH`. Video DownloadHelper captures of
portrait webcam / live-show HLS. Not studio masters. Seed `20260815`.

### Probe (75 MP4s, 0 errors)

| | |
| --- | --- |
| Files | 75, all `h264`, 8-bit (`yuv420p` 71 / `yuvj420p` 3 / unknown 1) |
| Profiles | High 52, Main 22, none 1. **No Baseline** — class is bitrate-driven |
| Orientation | **75 / 75 portrait** |
| Size / duration | 12.8 GiB, 16.3 hours |
| Bitrate | 0.69 – 8.28 Mb/s, median **1.40 Mb/s** |

Height-tier (what `resolution_tier` does today) vs min-side tier (honest
for portrait):

| | 2160p | 1080p | 720p | sd |
| --- | ---: | ---: | ---: | ---: |
| Height-based (current) | 12 | 60 | 3 | 0 |
| `min(w,h)` | 0 | 12 | 15 | 48 |

**Every file mismatches.** `resolution_tier` documents this assumption
explicitly: *"this library is movies/TV, never portrait video, so width is
never the constraining dimension."* VDH is the counter-example.

Resolution clusters:

| WxH | n | Typical bps | Height tier | Min-side tier |
| --- | ---: | --- | --- | --- |
| 480×854 | 27 | ~0.95 Mb/s | 1080p | sd |
| 608×1080 | 10 | ~1.4 Mb/s | 1080p | 720p |
| 1080×1920 | 10 | ~2.3–3.5 Mb/s | 2160p | 1080p |
| 396×858 | 7 | ~1.6–5.6 Mb/s | 1080p | sd |
| 480×1040 | 4 | ~0.95 Mb/s | 1080p | sd |
| 368×720 / 354×720 / 406×720 | 3 | ~0.69–0.71 Mb/s | 720p | sd |

Auto class with **height-based** ceilings (what `analyze` prints today):

| Class | n | Bitrate range | Why |
| --- | ---: | --- | --- |
| master | 0 | — | nothing is a remux |
| webrip | 6 | 5.2–8.3 Mb/s | 396×858 @ 5.6 and a few 616–1080-wide captures sit between 3.5 and 12 |
| stream | 23 | 1.5–3.8 Mb/s | mostly 1080×1920 ~3 Mb/s scored as 2160p stream |
| lowest | 46 | 0.69–2.8 Mb/s | 480×854 cluster under the 1080p lowest ceiling (1.5 Mb/s) |

So the heuristic lands most of this folder in `lowest`, which is the
**correct content class** (recorded HLS) — but often for the wrong reason
(portrait height inflating the tier until a 0.95 Mb/s 480-wide file is
judged against a 1080p ceiling).

### Do not flip to min-side ceilings blindly

Re-running the same bitrate rules with `min(w,h)` as the tier:

| | master | webrip | stream | lowest |
| --- | ---: | ---: | ---: | ---: |
| Height-based (now) | 0 | 6 | 23 | 46 |
| Min-side ceilings | **2** | 21 | 52 | **0** |

62 of 75 files change class. Two 396×858 files at 5.58 Mb/s become
**master** because the SD master floor is 4 Mb/s. That is worse than the
status quo: a phone-shaped HLS capture is not a Blu-ray remux.

If portrait is ever taught to `resolution_tier`, the bitrate floors must be
re-tuned (or recordings need a separate origin signal). Using min-side
**only** for preset extras (uhq vs hq, AQ) is safer than using it for class.

### Five-file sample (seed 20260815, 3×10 s each)

| Id | WxH | fps | Source Mb/s | Height class | Grain |
| --- | --- | ---: | ---: | --- | ---: |
| Gebby 26.07.25-1 | 480×854 | 25 | 0.95 | lowest (1080p) | 0.0073 |
| Cassandra 20260725 | 608×1080 | 15 | 1.36 | lowest (1080p) | 0.0068 |
| Goddess_2026-07-24 | 1080×1920 | ~30 | 2.27 | lowest (2160p) | 0.0059 |
| Biawak_2026-07-15 | 1080×1920 | 30 | 3.52 | **stream** (2160p) | 0.0084 |
| ryn_051159_HOT51 | 368×720 | ~24 | 0.69 | lowest (720p) | 0.0084 |

All five are well under the 0.012 grain threshold. `auto` would stay on
NVENC. The noise here is encoder mush, not 35mm grain.

Ladder (uncapped, same extras as `transcode`):

- NVENC on every window: master / stream / lowest CQ for the **height**
  tier (what the system would pick).
- Extra min-side lowest CQ on the middle window when the CQ actually
  changes (Gebby 36 vs 34, Cassandra 36 vs 34).
- CPU `svt-av1-hdr` master vs lowest CRF on the middle window only.

CQ actually used:

| File | Height tier | master | stream | lowest |
| --- | --- | ---: | ---: | ---: |
| Gebby / Cassandra | 1080p | 22 | 30 | 34 |
| Goddess / Biawak | 2160p | 22 | 30 | 34 |
| ryn | 720p | 24 | 32 | 36 |

### Bitrate vs source (uncapped, 10 s windows)

Approximate encode bitrate = `8 × bytes / 10 s`. Master CQ on the
1080×1920 files wants **2–3× the source**. Lowest lands near or under it.

| File (src Mb/s) | master CQ | stream CQ | lowest CQ |
| --- | --- | --- | --- |
| Gebby 0.95 | 1.2–2.7 | 0.50–1.5 | 0.31–1.1 |
| Cassandra 1.36 | 1.4–1.8 | 0.62–0.85 | 0.42–0.59 |
| Goddess 2.27 | 6.1–7.0 | 2.6–3.4 | 1.8–2.3 |
| Biawak 3.52 | 7.7–9.4 | 3.7–4.7 | 2.5–3.3 |
| ryn 0.69 | 0.93–1.3 | 0.41–0.60 | 0.26–0.42 |

This is the same overspend shape as WowGirls, just at webcam scale. The
60% lowest cap / 70% stream cap would bind on the master and often the
stream stops.

### Quality (NVENC, `--mode full`, all 15 windows)

Means of three windows per file. Bitrate is `8 × bytes / 10 s`.

| File (src) | Class stop | VMAF | p5 | S2 | Encode | vs src |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Gebby 0.95 | master CQ22 | 97.54 | 96.36 | 83.93 | 2.01 | 2.11× |
| | stream CQ30 | 95.83 | 94.63 | 77.16 | 1.00 | 1.05× |
| | lowest CQ34 | 94.32 | 92.88 | 72.36 | 0.68 | 0.72× |
| Cassandra 1.36 | master CQ22 | 99.35 | 97.51 | 81.45 | 1.56 | 1.14× |
| | stream CQ30 | 98.47 | 95.97 | 76.18 | 0.72 | 0.53× |
| | lowest CQ34 | 97.43 | 94.68 | 72.59 | 0.50 | 0.36× |
| Goddess 2.27 | master CQ22 | 96.59 | 95.49 | 82.04 | 6.46 | 2.84× |
| | stream CQ30 | 95.53 | 94.37 | 78.45 | 2.96 | 1.30× |
| | lowest CQ34 | 94.58 | 93.17 | 75.74 | 1.99 | 0.88× |
| Biawak 3.52 (auto=stream) | master CQ22 | 96.17 | 93.51 | 80.05 | 8.58 | 2.44× |
| | stream CQ30 | 94.52 | 91.92 | 75.38 | 4.17 | 1.19× |
| | lowest CQ34 | 93.05 | 90.27 | 71.80 | 2.93 | 0.83× |
| ryn 0.69 | master CQ24 | 98.06 | 96.46 | 82.54 | 1.04 | 1.52× |
| | stream CQ32 | 96.04 | 94.06 | 76.86 | 0.48 | 0.70× |
| | lowest CQ36 | 94.38 | 92.30 | 72.75 | 0.32 | 0.47× |
| **all 15 windows** | **master** | **97.54** | **95.87** | **82.00** | | **2.01×** |
| | **stream** | **96.08** | **94.19** | **76.81** | | **0.95×** |
| | **lowest** | **94.75** | **92.66** | **73.05** | | **0.65×** |

Worst single windows (the ones that decide the bar):

| Window | Why it is hard | master p5/S2 | stream | lowest |
| --- | --- | --- | --- | --- |
| Gebby w1 | 25 fps, texture, 0.95 Mb/s source | 95.96 / 82.85 | 93.55 / 73.92 | **90.60 / 67.40** |
| Biawak w2 | 30 fps handheld motion blur | 92.34 / 80.74 | 91.10 / 76.65 | 89.75 / 73.58 |
| Goddess w3 | 1080×1920, more motion | 95.09 / 80.85 | 93.67 / 76.60 | 92.24 / 73.45 |
| ryn w1 | 368-wide, 0.69 Mb/s | 96.41 / 82.20 | 93.60 / 75.06 | 91.38 / 70.13 |

Biawak is the honesty check: even **master** only reaches p5 92.3 on the
shaky window. The source is already temporally destroyed; full-reference
scores cannot invent a remux. Stills of that window look identical at
every stop.

### CPU (middle window only)

Height-tier CRF, so Goddess/Biawak used the **2160p** film table
(CRF 26 / 36) on a 1080-wide picture — another portrait-tier tax.

| File | master CRF | VMAF/p5/S2 / MB | lowest CRF | VMAF/p5/S2 / MB |
| --- | --- | --- | --- | --- |
| Gebby | 20 | 98.09 / 96.34 / 81.51 / 1.89 | 30 | 96.58 / 94.77 / 76.08 / 0.95 |
| Cassandra | 20 | 98.93 / 96.99 / 79.90 / 1.31 | 30 | 98.47 / 96.37 / 76.16 / 0.75 |
| Goddess | 26 | 95.27 / 94.52 / 79.99 / 3.18 | 36 | 93.54 / 92.13 / 76.15 / 1.70 |
| Biawak | 26 | 94.04 / 92.49 / 78.40 / 4.46 | 36 | 91.65 / 89.62 / 73.75 / 2.63 |
| ryn | 22 | 96.73 / 95.48 / 80.58 / 0.79 | 32 | 94.83 / 93.33 / 76.35 / 0.42 |

On the small files CPU lowest beats NVENC lowest on bits-per-quality. On
Goddess/Biawak, NVENC master is both better and (uncapped) larger; CPU
master lands near NVENC stream size with slightly worse VMAF. Grain is
0.006–0.008 — `auto` should stay on NVENC. Not a reason to flip routing.

Min-side lowest (CQ 36 on Gebby/Cassandra/ryn, same CQ 34 extras on
1080-wide files) moved Gebby w2 by about −1 VMAF / −2 S2 versus height
CQ 34. Not the lever.

### Stills

At casual 1:1 viewing, Gebby / Goddess / Biawak / ryn lowest vs ref look
close because the **source is already mushy** (HLS blocking, motion blur,
player chrome). Lowest adds another generation: curtain banding and
softer hair on Gebby, slightly more plastic skin on Goddess. Biawak w2
is motion-blurred in the source; lowest does not invent a new failure
mode.

Metrics are harsher than the stills. Full-reference scores punish extra
smoothing of already-smoothed webcam skin. Prefer p5 + S2 + stills, not
the mean.

### What this means for +12 CQ

- **Master CQ on VDH is wrong.** Across 15 windows it is the only stop
  that clears a remux-style p5 (~95.9) and it spends **2.01×** the
  source. The 85% remux cap would still leave most of these files
  larger than the file we are shrinking.
- **Stream +8 CQ is the VDH sweet spot.** Mean VMAF 96.1 / p5 94.2 /
  S2 76.8 at **0.95×** source. Near-transparent to this HLS on the
  easy and mid windows; short on Gebby w1 and anything as motion-broken
  as Biawak (where master is also short).
- **Lowest +12 CQ is size-sane** (0.65× source) and visually
  "good enough" on stills, but it is a second generation on the harder
  windows (Gebby w1 p5 90.6 / S2 67). That fails a strict reading of
  "don't make the mush worse." It is doing what the delta was sold as:
  refuse remux bits on recordings.
- For VDH-like HLS, `--source-class stream` is the better override when
  the heuristic said lowest only because portrait height inflated the
  tier.

**Do not change `LOWEST_NVENC_CQ_DELTA` from 12 on this evidence
alone.** VDH is 100% portrait HLS, not the whole `lowest` class
(MPEG-2 DVD, DivX, camcorder NTSC, VHS capture are still unmeasured).
If we ever want lowest to mean "near-transparent to a 1 Mb/s webcam"
rather than "smaller than the webcam," the candidate is to classify
these files as **stream**, not to invent a fifth table.

---

## 5. Classifier bugs and follow-ups

1. **Portrait height inflates the tier.** 480×854 → 1080p, 1080×1920 →
   2160p. Affects both the preset extras (uhq + AQ-10 on a 480-wide
   file) and the bitrate ceilings (1.5 Mb/s 1080p lowest vs 0.6 Mb/s SD
   lowest). Fix is `resolution_tier(min(width, height))` **plus**
   retuned floors, or an explicit portrait branch. Do not ship min-side
   ceilings with the current SD master floor of 4 Mb/s.
2. **`lowest` by bitrate ≠ `lowest` by origin.** A 5.6 Mb/s 396-wide
   capture classified webrip (or master under min-side). A 2.3 Mb/s
   1080×1920 capture classified lowest only because it was scored as
   2160p. Content origin (VDH / HLS / camcorder / MPEG-2) is the real
   label; bitrate is a proxy.
3. **webrip +4 CQ is the only non-master delta with a binding
   overspend measurement** (WowGirls). stream +8 / lowest +12 are
   now bounded by VDH but not by a landscape catalog title.
4. **1080p/720p/sd anime CQ/CRF** still unmeasured.
5. **Capped** webrip / stream / lowest encodes (the 85/70/60%
   `maxrate`) were not scored. Uncapped scores above are the quality
   ceiling; production runs will be at or below them when the cap binds.

## Practical invoke

```bash
# What would we do to a VDH file?
.agents/run.sh analyze --path "/mnt/exos1/downloads/VDH/Gebby 26.07.25-1.mp4"

# Force the class when the heuristic is portrait-confused
.agents/run.sh transcode run --path "..." --source-class stream --backend nvenc

# Clip-aligned recipe A/B (CPU)
.agents/run.sh abtest --source "..." --variant a=1080p-film --variant b=1080p-film,crf=30 --at 122,607,792 --yes
```

`--source-class auto` is the default. Probe/analyze print `source-class:`
on every file so a mis-bucket is visible before `--yes`.
