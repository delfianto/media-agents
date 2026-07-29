# Incident history

Real bugs found while building and smoke-testing this skill against actual
files in this library (a real 4K HDR10+Dolby Vision remux, and a real anime
episode), in the same spirit as `media-library/reference/incidents.md` -- the
fixes behind several safety/correctness details in `presets.py`, `gpu.py`,
and `run.py` came from here, not from reading documentation alone.

## `opus_bitrate_kbps` was non-monotonic for uncommon channel counts

**What happened:** the first implementation used a sparse table for the
common layouts (mono/stereo/5.1/7.1) plus a separately-anchored linear
formula (`48 + 56 * channels`) for anything else. `test_opus_bitrate_kbps_*`
in `test_presets.py` (written before any real file was touched, per this
repo's convention of testing "nontrivial parsing/threshold tuning" up front)
caught that 5 channels estimated *higher* (328k) than the real 6-channel/5.1
table figure (320k) -- a file with a bare 5.0 track (rare but real) would
have gotten a higher bitrate than a 5.1 track with an LFE channel, backwards.

**Fix:** replaced the sparse-table-plus-separate-formula shape with a single
piecewise-linear interpolation anchored at the four real data points
(1->64k, 2->128k, 6->320k, 8->450k), which is monotonic by construction. Any
future anchor point must keep the whole sequence monotonic, or the same test
will catch it again.

## The GPU capability probe's synthetic clip was too small for `av1_nvenc`

**What happened:** `gpu.detect_av1_nvenc_gpu()` hands each GPU index a
trivial, throwaway `av1_nvenc` encode to check real capability rather than
matching GPU names/architectures (see `reference/presets.md` for why that
matters on this exact machine). The first version used a 64x64 synthetic
test frame. Run for real, it failed on **both** GPUs, including the RTX 4080
that demonstrably encodes AV1 fine at real resolutions:

```
[av1_nvenc] InitializeEncoder failed: invalid param (8): Frame dimensions
are less than the minimum supported value.
```

128x128 hits the same error; 192x192 and 256x256 don't. The probe was
conflating "this GPU can't encode AV1" with "this GPU's AV1 encoder has a
minimum frame size the test clip didn't meet" -- on a capable GPU it would
have reported `None` (no AV1-capable GPU found) and silently fallen back to
the much slower CPU path for every run, exactly the failure mode the
capability-probing design was meant to avoid.

**Fix:** the probe clip is `256x256`, safely clear of the floor. Confirmed
against both real GPUs afterward: GPU 0 (RTX 4080) succeeds, GPU 1 (RTX
3060) still correctly fails with `"No capable devices found"` -- a real
capability difference, not a resolution artifact.

## `-b_ref_mode each` is accepted by ffmpeg's CLI parser but rejected by the driver

**What happened:** the NVENC preset defaults initially used `-b_ref_mode
each` ("each B-frame as reference" -- the strongest of the three modes ffmpeg
documents for this option, `disabled`/`each`/`middle`). It's a real,
documented `av1_nvenc` AVOption value with no warning attached. A live
end-to-end smoke test against a real anime clip on this machine's RTX 4080
failed immediately:

```
[av1_nvenc] Each B frame as reference is not supported
[av1_nvenc] No capable devices found
```

That second line is misleading on its own -- it reads like a capability
problem with the *whole GPU*, not a rejected option value, and would have
sent debugging effort toward `gpu.py`'s detection logic instead of
`command.py`'s NVENC arguments if the smoke test hadn't been narrowed down
argument-by-argument first.

**Fix:** switched to `-b_ref_mode middle`, confirmed directly to open and
encode successfully on the same GPU/preset/tune combination. `each` may work
on other NVENC generations/driver versions; this wasn't re-tested elsewhere,
so treat `middle` as the known-good floor rather than assuming `each` is
universally broken.

## Progress-tick throttling didn't recognize not-yet-resolved ticks as ticks

**What happened:** `run.stream_ffmpeg()` throttles ffmpeg's periodic
`frame=...` stats lines (at most once per `min_progress_interval`) while
forwarding every other line (banners, warnings, errors) immediately, so a
multi-hour encode doesn't flood whatever captured its stdout. The first
version detected "is this a tick" by checking whether `_PROGRESS_RE`
(requiring a resolved `time=HH:MM:SS`) matched. During SVT-AV1's lookahead
buffer fill at the start of a real encode, ffmpeg emits the *same* periodic
stats line every ~0.5s but with `time=N/A speed=N/A` -- `_PROGRESS_RE` never
matches those, so every single one of them bypassed the throttle and printed
immediately, for as long as ~10 seconds at the start of a real 4K clip.

**Fix:** tick detection now checks `line.startswith("frame=")` --
independent of whether a time/speed value has resolved yet -- while
`_PROGRESS_RE` is still used only for the separate job of *annotating* a
line with a computed percentage/ETA when one is derivable. Reconfirmed
against the same clip: the `N/A` period now throttles at the same interval
as the rest of the encode.
