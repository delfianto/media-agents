from dataclasses import dataclass
from pathlib import Path


class MetadataError(RuntimeError):
    pass


@dataclass(frozen=True)
class Track:
    id: int
    uid: int
    type: str
    language: str | None
    name: str | None
    default: bool


@dataclass(frozen=True)
class Attachment:
    id: int
    uid: int
    name: str
    mime_type: str


@dataclass(frozen=True)
class Container:
    path: Path
    tracks: tuple[Track, ...]
    attachments: tuple[Attachment, ...]


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MetadataError(f"mkvmerge JSON has no valid {label}")
    return value


def parse_container(path: Path, data: object) -> Container:
    if not isinstance(data, dict):
        raise MetadataError("mkvmerge output is not a JSON object")
    raw_tracks = data.get("tracks")
    raw_attachments = data.get("attachments", [])
    if not isinstance(raw_tracks, list) or not isinstance(raw_attachments, list):
        raise MetadataError("mkvmerge JSON has invalid tracks or attachments")
    tracks: list[Track] = []
    for raw in raw_tracks:
        if not isinstance(raw, dict) or not isinstance(raw.get("properties"), dict):
            raise MetadataError("mkvmerge JSON has an invalid track")
        properties = raw["properties"]
        track_type = raw.get("type")
        if not isinstance(track_type, str):
            raise MetadataError("mkvmerge JSON track has no type")
        if track_type == "subtitles":
            track_type = "subtitle"
        language = properties.get("language_ietf") or properties.get("language")
        name = properties.get("track_name")
        tracks.append(
            Track(
                id=_integer(raw.get("id"), "track ID"),
                uid=_integer(properties.get("uid"), "track UID"),
                type=track_type,
                language=language if isinstance(language, str) else None,
                name=name if isinstance(name, str) else None,
                default=bool(properties.get("default_track", False)),
            )
        )
    attachments: list[Attachment] = []
    for raw in raw_attachments:
        if not isinstance(raw, dict):
            raise MetadataError("mkvmerge JSON has an invalid attachment")
        name = raw.get("file_name")
        mime_type = raw.get("content_type")
        attachments.append(
            Attachment(
                id=_integer(raw.get("id"), "attachment ID"),
                uid=_integer(raw.get("properties", {}).get("uid"), "attachment UID")
                if isinstance(raw.get("properties"), dict)
                else _integer(None, "attachment UID"),
                name=name if isinstance(name, str) else "",
                mime_type=mime_type if isinstance(mime_type, str) else "",
            )
        )
    return Container(path, tuple(tracks), tuple(attachments))


def select_track(container: Container, selector: str) -> Track:
    prefix, separator, value = selector.partition(":")
    if not separator or not value.isdigit():
        raise MetadataError(
            f"invalid track selector {selector!r}; use uid:N, id:N, audio:N, subtitle:N, or video:N"
        )
    number = int(value)
    if prefix == "uid":
        matches = [track for track in container.tracks if track.uid == number]
    elif prefix == "id":
        matches = [track for track in container.tracks if track.id == number]
    elif prefix in ("audio", "subtitle", "video"):
        matches = [track for track in container.tracks if track.type == prefix]
        matches = matches[number - 1 : number] if number > 0 else []
    else:
        raise MetadataError(f"unknown track selector prefix {prefix!r}")
    if len(matches) != 1:
        raise MetadataError(f"track selector {selector!r} matched {len(matches)} tracks")
    return matches[0]


def cover_attachments(container: Container) -> tuple[Attachment, ...]:
    names = {"cover.jpg", "cover.jpeg", "cover.png", "cover.webp"}
    return tuple(item for item in container.attachments if item.name.lower() in names)
