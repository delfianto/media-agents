"""Render codec/language statistics from a scan cache. Stdlib-only, no
third-party table libraries -- this is meant to run anywhere ffmpeg does."""

from collections import Counter

from medialib.humansize import human_size

from . import langs, track_policy


def human_duration(seconds):
    if not seconds:
        return "0m"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def print_table(headers, rows, indent="  "):
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    line = indent + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print(indent + "  ".join("-" * w for w in widths))
    for row in rows:
        print(indent + "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))


def estimate_stream_bytes(stream, file_duration):
    if stream.get("bytes_tag"):
        return stream["bytes_tag"]
    duration = stream.get("duration") or file_duration
    rate = stream.get("bit_rate") or stream.get("bps_tag")
    if rate and duration:
        return int(rate * duration / 8)
    return None


LOSSLESS_AUDIO_CODECS = {"truehd", "flac", "alac", "mlp", "pcm_s16le", "pcm_s24le"}


def is_lossless_audio(codec_name, profile):
    return codec_name in LOSSLESS_AUDIO_CODECS or (
        codec_name == "dts" and bool(profile) and "MA" in profile
    )


def build_report(cache, policy: track_policy.Policy | None = None):
    policy = policy or track_policy.Policy()
    files = {rel: e for rel, e in cache.get("files", {}).items() if "error" not in e}
    errors = {rel: e for rel, e in cache.get("files", {}).items() if "error" in e}

    report = {
        "total_files": len(files),
        "error_files": errors,
        "total_size": 0,
        "total_duration": 0.0,
        "containers": Counter(),
        "video_codecs": Counter(),
        "resolutions": Counter(),
        "audio_codecs": Counter(),
        "lossless_audio_tracks": 0,
        "lossy_audio_tracks": 0,
        "subtitle_codecs": Counter(),
        "audio_lang_counts": Counter(),
        "subtitle_lang_counts": Counter(),
        "files_with_non_english_audio": 0,
        "files_with_non_english_subtitle": 0,
        "total_audio_tracks": 0,
        "total_subtitle_tracks": 0,
        "policy_files_changed": 0,
        "policy_audio_tracks_to_drop": 0,
        "policy_subtitle_tracks_to_drop": 0,
        "policy_bytes_reclaimed_estimate": 0,
        "anime_files": 0,
    }

    for entry in files.values():
        fmt = entry.get("format", {})
        report["total_size"] += fmt.get("size") or 0
        report["total_duration"] += fmt.get("duration") or 0.0

        file_has_non_eng_audio = False
        file_has_non_eng_sub = False
        norm_tracks = []

        for s in entry["streams"]:
            ctype = s["codec_type"]
            if ctype == "video":
                if (s.get("disposition") or {}).get("attached_pic"):
                    continue
                report["video_codecs"][(s.get("codec_name"), s.get("profile"))] += 1
                h = s.get("height")
                if h:
                    bucket = (
                        "2160p"
                        if h >= 2000
                        else "1080p"
                        if h >= 1000
                        else "720p"
                        if h >= 700
                        else f"{h}p"
                    )
                    report["resolutions"][bucket] += 1
            elif ctype == "audio":
                report["total_audio_tracks"] += 1
                report["audio_codecs"][(s.get("codec_name"), s.get("profile"))] += 1
                if is_lossless_audio(s.get("codec_name"), s.get("profile")):
                    report["lossless_audio_tracks"] += 1
                else:
                    report["lossy_audio_tracks"] += 1
                lang = s.get("language")
                report["audio_lang_counts"][langs.display_name(lang) if lang else "Unknown"] += 1
                if not langs.is_english(lang) and not langs.is_unknown(lang):
                    file_has_non_eng_audio = True
            elif ctype == "subtitle":
                report["total_subtitle_tracks"] += 1
                report["subtitle_codecs"][s.get("codec_name")] += 1
                lang = s.get("language")
                report["subtitle_lang_counts"][langs.display_name(lang) if lang else "Unknown"] += 1
                if not langs.is_english(lang) and not langs.is_unknown(lang):
                    file_has_non_eng_sub = True

            if ctype in ("video", "audio", "subtitle"):
                norm_tracks.append(track_policy.from_ffprobe_stream(s))

        if file_has_non_eng_audio:
            report["files_with_non_english_audio"] += 1
        if file_has_non_eng_sub:
            report["files_with_non_english_subtitle"] += 1

        plan_result = track_policy.plan_streams(norm_tracks, policy)
        if plan_result["is_anime"]:
            report["anime_files"] += 1
        if plan_result["changed"]:
            report["policy_files_changed"] += 1
            report["policy_audio_tracks_to_drop"] += len(plan_result["drop_audio"])
            report["policy_subtitle_tracks_to_drop"] += len(plan_result["drop_subtitle"])
            file_duration = fmt.get("duration")
            for t, _reason in plan_result["drop_audio"]:
                src = next(s for s in entry["streams"] if s["index"] == t["index"])
                est = estimate_stream_bytes(src, file_duration)
                if est:
                    report["policy_bytes_reclaimed_estimate"] += est

    report["containers"] = Counter(_ext(rel) for rel in files)
    return report


def _ext(rel):
    return rel.rsplit(".", 1)[-1].lower() if "." in rel else "unknown"


def print_report(report, policy: track_policy.Policy | None = None):
    policy = policy or track_policy.Policy()
    print("=" * 70)
    print("LIBRARY OVERVIEW")
    print("=" * 70)
    print(f"  Files scanned:      {report['total_files']}")
    if report["error_files"]:
        print(f"  Files with errors:  {len(report['error_files'])} (see --show-errors)")
    print(f"  Total size:         {human_size(report['total_size'])}")
    print(f"  Total duration:     {human_duration(report['total_duration'])}")
    print_table(["Container", "Files"], [[ext, n] for ext, n in report["containers"].most_common()])

    print()
    print("=" * 70)
    print("VIDEO CODECS")
    print("=" * 70)
    rows = [
        [codec or "?", profile or "-", n]
        for (codec, profile), n in report["video_codecs"].most_common()
    ]
    print_table(["Codec", "Profile", "Count"], rows)
    print()
    print_table(["Resolution", "Count"], [[r, n] for r, n in report["resolutions"].most_common()])

    print()
    print("=" * 70)
    print("AUDIO CODECS")
    print("=" * 70)
    rows = [
        [codec or "?", profile or "-", n]
        for (codec, profile), n in report["audio_codecs"].most_common()
    ]
    print_table(["Codec", "Profile", "Count"], rows)
    print(
        f"\n  Lossless tracks: {report['lossless_audio_tracks']}   "
        f"Lossy tracks: {report['lossy_audio_tracks']}   "
        f"Total: {report['total_audio_tracks']}"
    )

    print()
    print("=" * 70)
    print("SUBTITLE CODECS")
    print("=" * 70)
    print_table(
        ["Codec", "Count"], [[c or "?", n] for c, n in report["subtitle_codecs"].most_common()]
    )
    print(f"\n  Total subtitle tracks: {report['total_subtitle_tracks']}")

    print()
    print("=" * 70)
    print("LANGUAGES")
    print("=" * 70)
    print(
        f"  Files with >=1 non-English AUDIO track:    {report['files_with_non_english_audio']} "
        f"/ {report['total_files']}"
    )
    print(
        f"  Files with >=1 non-English SUBTITLE track: {report['files_with_non_english_subtitle']} "
        f"/ {report['total_files']}"
    )
    print("\n  Audio tracks by language (top 15):")
    print_table(["Language", "Tracks"], report["audio_lang_counts"].most_common(15), indent="    ")
    print("\n  Subtitle tracks by language (top 15):")
    print_table(
        ["Language", "Tracks"], report["subtitle_lang_counts"].most_common(15), indent="    "
    )

    print()
    print("=" * 70)
    print(
        f"REMUX IMPACT (policy: keep_unknown={policy.keep_unknown}, "
        f"keep_forced_subs={policy.keep_forced_subs}, strip_commentary={policy.strip_commentary}, "
        f"detect_anime={policy.detect_anime})"
    )
    print("=" * 70)
    if policy.detect_anime:
        print(
            f"  Anime-classified files (<=2 audio tracks incl. Japanese):"
            f"  {report['anime_files']} -- policy flipped to keep JP audio + EN/JP subs"
        )
    print(
        f"  Files that would be modified:        "
        f"{report['policy_files_changed']} / {report['total_files']}"
    )
    print(f"  Audio tracks that would be dropped:    {report['policy_audio_tracks_to_drop']}")
    print(f"  Subtitle tracks that would be dropped: {report['policy_subtitle_tracks_to_drop']}")
    reclaimed = human_size(report["policy_bytes_reclaimed_estimate"])
    print(f"  Estimated space reclaimed (audio):   ~{reclaimed}")
