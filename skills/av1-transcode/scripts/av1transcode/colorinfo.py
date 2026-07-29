"""Pure helpers for reading and re-emitting HDR/color metadata.

Verified against a real 4K remux in this library (Mission: Impossible - The
Final Reckoning, HEVC Main 10, Dolby Vision profile 8): `ffprobe -show_streams`
populates `color_primaries`/`color_transfer`/`color_space` as plain strings
directly on the video stream, and `side_data_list` on that same stream carries
"Mastering display metadata", "Content light level metadata", and "DOVI
configuration record" entries with no extra ffprobe flags needed -- probe.py
passes those through unmodified; everything here just interprets them.

Mastering-display chromaticity/luminance fields come back as "num/den"
fraction strings, but with an encoder-dependent denominator (65536 from one
encode, 50000/10000 from another, both observed directly) -- never assume a
fixed denominator, just divide.
"""

CicpTable = dict[str, int]

# ITU-T H.273 (CICP) codes, the values SVT-AV1's --color-primaries/
# --transfer-characteristics/--matrix-coefficients want, keyed by the string
# names ffprobe reports for color_primaries/color_transfer/color_space. Only
# the values actually seen on real discs/remuxes (SDR BT.709, HDR10/DV
# BT.2020+PQ, and the older BT.601-ish tags some DVD-sourced catalog titles
# still carry) are mapped; an unmapped name leaves that field out of the
# svtav1-params HDR string rather than guessing.
CICP_PRIMARIES: CicpTable = {
    "bt709": 1,
    "bt470bg": 5,
    "smpte170m": 6,
    "bt2020": 9,
}
CICP_TRANSFER: CicpTable = {
    "bt709": 1,
    "smpte170m": 6,
    "smpte2084": 16,  # PQ (HDR10/HDR10+/Dolby Vision base layer)
    "arib-std-b67": 18,  # HLG
}
CICP_MATRIX: CicpTable = {
    "bt709": 1,
    "smpte170m": 6,
    "bt2020nc": 9,
    "bt2020c": 10,
}

HDR_TRANSFER_NAMES = frozenset({"smpte2084", "arib-std-b67"})


def parse_fraction(value: str) -> float | None:
    if not value:
        return None
    num, _, den = value.partition("/")
    try:
        if den:
            return float(num) / float(den)
        return float(num)
    except ValueError, ZeroDivisionError:
        return None


def is_hdr(video: dict) -> bool:
    """True for PQ (HDR10/HDR10+/Dolby Vision) or HLG content. Checked via
    transfer characteristic rather than bit depth/primaries alone -- a 10-bit
    BT.2020 SDR master exists but is rare and shouldn't be treated as HDR."""
    return (video.get("color_transfer") or "") in HDR_TRANSFER_NAMES


def has_dolby_vision(video: dict) -> bool:
    return video.get("dolby_vision") is not None


def has_hdr10_plus(video: dict) -> bool:
    """True when ffprobe reported dynamic HDR10+ side data on the video stream.

    Static HDR10 (PQ + mastering display) alone is not HDR10+ -- that path
    is already handled by the plain ffmpeg HDR color tags / nvenc encode.
    Dynamic metadata needs the nvencc `--dhdr10-info` path (or is lost)."""
    return video.get("hdr10_plus") is not None


def needs_dynamic_metadata_path(video: dict) -> bool:
    """True when the GPU encode must go through nvencc (or CPU for DV) rather
    than plain ffmpeg av1_nvenc, which drops Dolby Vision RPU and HDR10+ SEI."""
    return has_dolby_vision(video) or has_hdr10_plus(video)


def mastering_display_param(mastering_display: dict) -> str | None:
    """Build SVT-AV1's `--mastering-display G(x,y)B(x,y)R(x,y)WP(x,y)L(max,min)`
    value from ffprobe's "Mastering display metadata" side_data fields."""
    keys = (
        "green_x",
        "green_y",
        "blue_x",
        "blue_y",
        "red_x",
        "red_y",
        "white_point_x",
        "white_point_y",
        "max_luminance",
        "min_luminance",
    )
    values = {}
    for key in keys:
        parsed = parse_fraction(mastering_display.get(key, ""))
        if parsed is None:
            return None
        values[key] = parsed
    return (
        f"G({values['green_x']:.5f},{values['green_y']:.5f})"
        f"B({values['blue_x']:.5f},{values['blue_y']:.5f})"
        f"R({values['red_x']:.5f},{values['red_y']:.5f})"
        f"WP({values['white_point_x']:.5f},{values['white_point_y']:.5f})"
        f"L({values['max_luminance']:.4f},{values['min_luminance']:.4f})"
    )


def content_light_param(content_light: dict) -> str | None:
    max_content = content_light.get("max_content")
    max_average = content_light.get("max_average")
    if max_content is None or max_average is None:
        return None
    return f"{int(max_content)},{int(max_average)}"


def svtav1_hdr_params(video: dict) -> dict[str, str]:
    """svtav1-params key/value pairs that carry the source's static HDR
    metadata into the AV1 bitstream. libsvtav1 does not forward AVFrame HDR
    side data into the bitstream on its own (ffmpeg trac #10355) -- these
    have to be set explicitly from what probe.py already read off the
    source, never left to a default/auto behavior."""
    params: dict[str, str] = {}

    primaries = CICP_PRIMARIES.get(video.get("color_primaries") or "")
    if primaries is not None:
        params["color-primaries"] = str(primaries)
    transfer = CICP_TRANSFER.get(video.get("color_transfer") or "")
    if transfer is not None:
        params["transfer-characteristics"] = str(transfer)
    matrix = CICP_MATRIX.get(video.get("color_space") or "")
    if matrix is not None:
        params["matrix-coefficients"] = str(matrix)

    mastering_display = video.get("mastering_display")
    if mastering_display:
        value = mastering_display_param(mastering_display)
        if value:
            params["mastering-display"] = value

    content_light = video.get("content_light")
    if content_light:
        value = content_light_param(content_light)
        if value:
            params["content-light"] = value

    return params
