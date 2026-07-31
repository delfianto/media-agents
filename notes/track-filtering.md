# Language filtering and safe track stripping

`track-strip` is a stream-copy/remux operation: it does not re-encode video and normally does not re-encode audio. `transcode` has a simpler by-language filter that runs while it converts video/audio. Use `track-strip` first when the policy needs commentary, duplicate-track, SDH, codec, or anime-release handling.

## How tracks are classified

Both ffprobe and mkvmerge listings are normalized to the same fields before policy code runs. Language codes are compared as ISO-639 values, with English (`eng`) and Japanese (`jpn`) recognized through aliases and descriptive names.

The default policy is:

- Keep English audio and English subtitles; keep unknown-language tracks unless the operator opts into dropping them.
- Keep forced subtitles regardless of language unless explicitly disabled.
- Keep every matching plain subtitle, because subtitles are cheap and multiple subtitle variants are useful.
- Keep unknown audio rather than guessing what a blank language tag means.
- For Japanese-original releases, detected conservatively as at most two audio tracks including Japanese, keep Japanese audio and English/Japanese subtitles instead of preferring an English dub. A Japanese dub hidden among many other dubs does not trigger this rule.

Commentary is identified by the commentary disposition or title text. SDH is identified by the hearing-impaired disposition or title text containing SDH, hearing-impaired, or deaf. The optional size heuristic is intentionally narrow: it compares exactly two unlabeled English/Japanese candidates, of the same subtitle codec, with a 1.10–3.0 size ratio. It ignores forced/commentary titles, language names in titles, unknown-language buckets, and groups of three or more candidates. This avoids confusing bitmap PGS size with text-subtitle size or mistagged foreign subtitles with SDH.

When `--single-audio-track` is requested, audio is ranked by codec family first (lossless TrueHD/FLAC/DTS-HD above lossy E-AC3/AC-3/AAC), then channel count, then bitrate. Commentary is avoided where possible. A safety fallback always keeps a non-commentary audio track if one exists; a file must never become silent or commentary-only merely because its preferred language or codec was removed.

`transcode` applies a less opinionated version: it matches the requested language, chooses the best matching audio track when single-track mode is on, prefers plain subtitles over SDH, and falls back to all audio if no requested language exists. Pass `all` to retain all tracks.

## How stripping is performed safely

The planner reports every keep/drop decision before mutation. In a real apply, the selected streams are remuxed to a temporary file, then checked for a video stream, a usable audio stream, matching duration (within the configured tolerance), and a clean head decode. Only after verification does the tool atomically replace the original, normally moving the original to a backup first. Backups preserve recoverability but consume the same amount of disk space until purged. Output, backup, and log directories are excluded from the input walk so a live run cannot rediscover its own files.

Codec comparisons use mkvmerge's stable codec IDs and ffprobe's short names through one normalization layer. This matters because free-text descriptions such as “DTS-HD Master Audio” are not reliable command-line match keys.

## Space-saving estimate

Subtitle removal usually saves only megabytes across a library. The meaningful savings come from removing foreign-language audio dubs and redundant lossless or compatibility tracks. In the baseline audit represented by this repository, the default English/Japanese policy would remove about 814 audio tracks and 13,845 subtitles across 856 files, for roughly 185 GB reclaimed; the completed remux run reclaimed about 180 GB. Treat that as a planning example, not a promise: calculate the estimate with `track-strip stats` on the current library before applying a policy. Files with anime/Japanese-original handling, forced subtitles, unknown tags, commentary, or only one usable soundtrack are deliberately excluded from the naive count.

The estimate is approximately the sum of removed stream sizes, not the sum of whole-file sizes. Container overhead is negligible, and remuxing does not compress the remaining streams. Always review the plan and keep a recovery path before a library-wide apply.

## General lessons

Audit a proposed policy over the complete library before executing it. Small samples miss mistagged languages, missing disposition flags, codec-dependent subtitle sizes, and files whose only non-commentary soundtrack is the one a codec rule would remove. Cross-checking a cached ffprobe plan against a live mkvmerge plan is also valuable because it exercises two different metadata shapes through the same policy code.
