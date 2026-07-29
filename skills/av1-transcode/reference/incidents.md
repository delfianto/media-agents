# Incident history

Real bugs found while building and smoke-testing this skill against actual files in this library (a real 4K HDR10+Dolby Vision remux, and a real anime episode), in the same spirit as `media-library/reference/incidents.md` -- the fixes behind several safety/correctness details in `presets.py`, `gpu.py`, and `run.py` came from here, not from reading documentation alone.

## `opus_bitrate_kbps` was non-monotonic for uncommon channel counts

**What happened:** the first implementation used a sparse table for the common layouts (mono/stereo/5.1/7.1) plus a separately-anchored linear formula (`48 + 56 * channels`) for anything else. `test_opus_bitrate_kbps_*` in `test_presets.py` (written before any real file was touched, per this repo's convention of testing "nontrivial parsing/threshold tuning" up front) caught that 5 channels estimated *higher* (328k) than the real 6-channel/5.1 table figure (320k) -- a file with a bare 5.0 track (rare but real) would have gotten a higher bitrate than a 5.1 track with an LFE channel, backwards.

**Fix:** replaced the sparse-table-plus-separate-formula shape with a single piecewise-linear interpolation anchored at the four real data points (1->64k, 2->128k, 6->320k, 8->450k), which is monotonic by construction. Any future anchor point must keep the whole sequence monotonic, or the same test will catch it again.

## The GPU capability probe's synthetic clip was too small for `av1_nvenc`

**What happened:** `gpu.detect_av1_nvenc_gpu()` hands each GPU index a trivial, throwaway `av1_nvenc` encode to check real capability rather than matching GPU names/architectures (see `reference/presets.md` for why that matters on this exact machine). The first version used a 64x64 synthetic test frame. Run for real, it failed on **both** GPUs, including the RTX 4080 that demonstrably encodes AV1 fine at real resolutions:

```
[av1_nvenc] InitializeEncoder failed: invalid param (8): Frame dimensions
are less than the minimum supported value.
```

128x128 hits the same error; 192x192 and 256x256 don't. The probe was conflating "this GPU can't encode AV1" with "this GPU's AV1 encoder has a minimum frame size the test clip didn't meet" -- on a capable GPU it would have reported `None` (no AV1-capable GPU found) and silently fallen back to the much slower CPU path for every run, exactly the failure mode the capability-probing design was meant to avoid.

**Fix:** the probe clip is `256x256`, safely clear of the floor. Confirmed against both real GPUs afterward: GPU 0 (RTX 4080) succeeds, GPU 1 (RTX 3060) still correctly fails with `"No capable devices found"` -- a real capability difference, not a resolution artifact.

## `-b_ref_mode each` is accepted by ffmpeg's CLI parser but rejected by the driver

**What happened:** the NVENC preset defaults initially used `-b_ref_mode each` ("each B-frame as reference" -- the strongest of the three modes ffmpeg documents for this option, `disabled`/`each`/`middle`). It's a real, documented `av1_nvenc` AVOption value with no warning attached. A live end-to-end smoke test against a real anime clip on this machine's RTX 4080 failed immediately:

```
[av1_nvenc] Each B frame as reference is not supported
[av1_nvenc] No capable devices found
```

That second line is misleading on its own -- it reads like a capability problem with the *whole GPU*, not a rejected option value, and would have sent debugging effort toward `gpu.py`'s detection logic instead of `command.py`'s NVENC arguments if the smoke test hadn't been narrowed down argument-by-argument first.

**Fix:** switched to `-b_ref_mode middle`, confirmed directly to open and encode successfully on the same GPU/preset/tune combination. `each` may work on other NVENC generations/driver versions; this wasn't re-tested elsewhere, so treat `middle` as the known-good floor rather than assuming `each` is universally broken.

## Progress-tick throttling didn't recognize not-yet-resolved ticks as ticks

**What happened:** `run.stream_ffmpeg()` throttles ffmpeg's periodic `frame=...` stats lines (at most once per `min_progress_interval`) while forwarding every other line (banners, warnings, errors) immediately, so a multi-hour encode doesn't flood whatever captured its stdout. The first version detected "is this a tick" by checking whether `_PROGRESS_RE` (requiring a resolved `time=HH:MM:SS`) matched. During SVT-AV1's lookahead buffer fill at the start of a real encode, ffmpeg emits the *same* periodic stats line every ~0.5s but with `time=N/A speed=N/A` -- `_PROGRESS_RE` never matches those, so every single one of them bypassed the throttle and printed immediately, for as long as ~10 seconds at the start of a real 4K clip.

**Fix:** tick detection now checks `line.startswith("frame=")` -- independent of whether a time/speed value has resolved yet -- while `_PROGRESS_RE` is still used only for the separate job of *annotating* a line with a computed percentage/ETA when one is derivable. Reconfirmed against the same clip: the `N/A` period now throttles at the same interval as the rest of the encode.

## CRF/CQ-only encoding produced a file *larger* than the source

**What happened:** reported directly by the user against a real file from a different (non-Blu-ray-remux) library: a 3840x2160 x264 source, deliberately bitrate-capped to 15 Mbps (2-pass VBR, `vbv_maxrate=43.2Mbps` -- MediaInfo confirmed this exactly), was already larger than its own source size by 70% through the encode. Every preset here was tuned against Blu-ray *remux* sources (40-80+ Mbps 4K masters, wastefully high for their actual content complexity) where AV1 at CRF/CQ 20-22 undercuts the original easily. CRF/CQ target a *quality level*, not a *size ceiling* -- fed an already-efficiently-encoded source instead, hitting that same quality bar can legitimately need more bits than the source itself used. `verify_output()` already had a "larger than source" check, but only as a post-hoc warning *after* the wasted encode -- there was no ceiling anywhere in the actual encode path.

**Fix:** every encode now caps output video bitrate to a fraction (`presets.MAX_BITRATE_FRACTION_OF_SOURCE`, default 0.85) of the *source's own* video bitrate -- SVT-AV1's "Capped CRF" mode (`--mbr`, alongside `--crf`) and NVENC's `-maxrate`/`-bufsize` (alongside `-cq`). Both mechanisms confirmed directly against real clips before shipping: uncapped NVENC CQ=22 on a real 4K clip produced ~17.9 Mbps, `-maxrate 4M -bufsize 8M` brought it to ~4.4 Mbps; SVT-AV1's own startup log explicitly names the mode (`BRC mode ... capped CRF`) once `mbr` is set. Re-ran end-to-end against a fresh 20s extract of the exact file that exposed the bug (same library, `ffmpeg -c copy` extract, never touching the real file) -- output landed at 90% of source size instead of exceeding it. This is a safety net, not a guarantee of dramatic savings on already-tight sources: on a genuine wasteful Blu-ray remux the cap essentially never binds, since CRF/CQ-driven output lands far under it anyway.

## `-map 0` was quietly including cover-art "video" streams in the AV1 encode

**Side finding while fixing the bitrate cap:** adding per-stream language filtering required switching from a blanket `-map 0` to mapping specific stream indices, which surfaced a latent issue in the old blanket-map approach: a source with an embedded cover-art image (a second, `attached_pic` "video" stream) would have had `-c:v` apply to *both* streams, running the cover art through AV1 encoding alongside the real video. `probe.py` already excludes `attached_pic` streams when picking the *primary* video stream, but the old command-building code mapped every stream anyway. Now only `probed["video"]["index"]` is ever mapped as video -- cover art is dropped entirely (Plex/Jellyfin use `poster.jpg`/`fanart.jpg` files, not embedded cover art, so nothing is lost).

## NVENC preset p6 vs p7: no measured speed difference on this hardware

**What happened:** every preset defaulted to NVENC `-preset p6`, on the unverified assumption (carried over from the general "higher preset number = slower" intuition that holds for most encoders' preset ladders) that `p7` would cost meaningfully more time for little quality gain -- written up that way in `reference/presets.md` without ever actually measuring it.

**Fix:** measured directly on this machine's RTX 4080 -- the same 12s 4K clip, same `cq`/`tune`/AQ settings, encoded 4 times alternating `p6`/`p7`. Results: both took ~10s wall-clock (no measurable difference once past a one-off cold-start outlier on the very first run), and `p7` produced a marginally *smaller* file at the same `cq`. Switched every preset's `nvenc_preset` to `p7` (including the "sd" tier, previously `p5` -- if the most expensive resolution shows no penalty, a cheaper one won't either). This was measured only on this specific GPU/driver combination (Ada Lovelace, driver 610.43.03); if this skill ever runs on meaningfully different NVENC hardware, it'd be worth re-measuring rather than assuming the same holds.

## MKV cover art: `-disposition:v attached_pic` is silently a no-op

**What happened:** the first attempt at embedding cover art followed the pattern used for e.g. audio-file album art -- map the poster image as a second video-type output stream and flag it `-disposition:v:1 attached_pic`. `ffmpeg` accepts this with no warning or error at any verbosity level, and the resulting file plays fine, but the flag never actually takes -- confirmed directly with `mkvmerge --identify` (the authoritative tool for what a Matroska file actually contains): the disposition came back `attached_pic: 0` regardless of exact command form tried (`attached_pic`, `+attached_pic`, targeting `v:0` alone, before or after other output options). ffprobe's own `-show_streams` view doesn't surface anything to contradict this either -- it just shows a second ordinary "video" stream, no hint that anything is missing.

**Why:** `attached_pic` disposition is an MP4-family convention (cover art *is* literally a flagged stream there). Matroska has a completely different, unrelated mechanism: real container-level **attachments** (the same mechanism this skill already uses read-only for font attachments, mapped via `0:t?`) -- not a video stream at all.

**Fix:** ffmpeg's `-attach <path>` output option, confirmed correct via `mkvmerge --identify` showing a genuine `Attachment ID` entry (not an extra `Track ID`). Two more things had to be right before it actually worked: `-attach` requires an explicit `-metadata:s:t:N mimetype=...` or ffmpeg refuses to write the file at all (`"Attachment stream N has no mimetype tag and it cannot be deduced from the codec id"` -- a real error, not a warning); and `N` is *not* always `0` -- it's one past however many attachment-type streams the source already had (e.g. font attachments mapped via `0:t?`), confirmed by deliberately muxing a source with one pre-existing attachment and watching ffmpeg's own error name the *next* index. `probe.py`'s new `attachment_count` field exists specifically to get this right (`command._cover_art_args`).
