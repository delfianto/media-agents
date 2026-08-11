# grav1synth patches

[`grav1synth`](https://github.com/rust-av/grav1synth) is a third-party Rust tool
(`rust-av/grav1synth`, MIT) for inspecting, applying, and removing AV1 film-grain
synthesis metadata directly in the OBU bitstream -- no re-encode. It is **not**
vendored, built, or invoked by anything in `src/psammophis/` today; these patches
exist because a real investigation (denoise-before-`nvenc`, then reinject grain
via `grav1synth`, to approach `libsvtav1`'s grain-synthesis efficiency at
`nvenc`'s speed -- see `notes/av1-encoding.md`'s grain-routing discussion for why
that pairing is otherwise unavailable) needed a *working* build to test against,
and found and fixed two real bugs along the way. Kept here so the fix isn't lost
if this gets revisited, not because the tool is part of this repo's pipeline.

Base commit: `1044228cd411672b565e5762a9b3597f4dd163b0` (upstream `main`,
2026-04-30, tag-less at time of patching -- `grav1synth` v0.2.0).

## Patches, in apply order

1. **`0001-bump-ffmpeg-the-third.patch`** -- environment compatibility, not a
   bug fix. `grav1synth`'s `Cargo.toml` pins `ffmpeg-the-third = "5.0.0"`
   (built against FFmpeg 8.1's headers). This machine runs FFmpeg 9.0, which
   removed several `AVCodec` struct fields (`sample_fmts`, `pix_fmts`,
   `supported_framerates`, `ch_layouts`, ...) that `5.0.0`'s raw bindings still
   reference directly, so the crate fails to compile at all. Bumping to
   `ffmpeg-the-third = "6.0.0"` (published on crates.io 2026-08-09, built
   against FFmpeg 9.0) fixes the build. If revisiting this on a machine with an
   older FFmpeg, check whether this bump is still needed or even correct --
   don't apply it blindly.

2. **`0002-fix-nvenc-film-grain-flag.patch`** -- the actual bug fix, in
   `src/parser/sequence.rs`. `grav1synth apply` reliably corrupted any AV1
   stream produced by `av1_nvenc` (ffmpeg's NVENC AV1 encoder) while working
   fine on `libsvtav1` output, failing with `obu_forbidden_bit out of range`
   deep in the rewritten bitstream. Root cause: two related bugs in how
   `parse_sequence_header`'s write path rewrites the `film_grain_params_present`
   flag in place --
   - It patched `obu_out.last_mut()` (whatever byte happens to currently be
     last in the output buffer) instead of the byte that actually contains the
     flag. This coincidentally is the same byte when the flag lands in the
     OBU's final declared byte with no trailing padding after it -- true for
     the specific SVT-AV1 stream this was tested against, false for the NVENC
     one, purely because earlier sequence-header fields (`seq_level_idx`,
     color description details, operating-point count, ...) differ enough
     between encoders to shift the flag's bit position by a few bits. The
     flag physically failed to flip on the NVENC file; the frame header still
     got grain params written into it regardless, so the output was
     self-contradictory (frame carries grain data the sequence header says
     shouldn't exist).
   - Separately, the OBU dispatcher in `src/parser/obu.rs` decides whether to
     grow/shrink the sequence header's declared `obu_size` by comparing bytes
     written (`obu_out.len()`, always the full declared payload for this
     write strategy) against bytes the bit-parser's returned cursor advanced
     by (which stops right after the last meaningfully-parsed field, not at
     the OBU's true end -- `trailing_bits()` padding is never explicitly
     consumed). Whether those two happen to match is, again, an accident of
     where the flag lands relative to a byte boundary. On the NVENC stream
     they didn't match, so a one-bit in-place toggle was misread as "grew by
     1 byte," and the sequence header's own size field got bumped from 15 to
     16 -- eating one real byte belonging to the *next* OBU when re-parsed.

   Fixed by computing the flag's actual byte offset from consumed-byte
   arithmetic instead of assuming it's the buffer's last byte, and by forcing
   `parse_sequence_header`'s returned cursor out to the full declared payload
   boundary in write mode, so the caller's size-adjustment math is never fed
   a spurious mismatch. See the patch's own comments for the full reasoning.

   Verified: builds clean; fixes `apply --iso <N>` on two independently
   reproduced `av1_nvenc` files (a 5s clip and a 3min clip) that failed
   identically before the fix; does not regress the working `libsvtav1` case
   (explicit before/after check); output decodes cleanly via `ffmpeg`/`dav1d`
   with an exact frame-count match against the source (4345/4345); grav1synth's
   own test suite passes except for one pre-existing, unrelated failure in
   `parser::grain::tests::film_grain_params_key_frame_forces_update_and_uses_420_zero_luma_shortcut`
   -- confirmed present before this patch too (reproduces identically with
   only patch 1 applied), not introduced by this fix.

   Not upstreamed. If this gets revisited, filing it against
   `rust-av/grav1synth` with the repro clips would be the right next step
   rather than carrying a local patch indefinitely.

## Applying

```bash
git clone https://github.com/rust-av/grav1synth.git
cd grav1synth
git checkout 1044228cd411672b565e5762a9b3597f4dd163b0   # base commit these were cut against
git apply /path/to/this/dir/0001-bump-ffmpeg-the-third.patch
git apply /path/to/this/dir/0002-fix-nvenc-film-grain-flag.patch
cargo build --release   # regenerates Cargo.lock; not vendored here, it's mostly transitive churn
```

Needs a Rust toolchain and FFmpeg's dev headers/pkgconfig (already satisfied on
this machine since `env-check`'s FFmpeg prerequisite covers it).
