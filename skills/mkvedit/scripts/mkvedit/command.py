import mimetypes
from dataclasses import dataclass, field
from pathlib import Path

from .model import Container, MetadataError, Track, cover_attachments, select_track

FLAG_NAMES = frozenset(
    {
        "default",
        "forced",
        "enabled",
        "original",
        "commentary",
        "hearing-impaired",
        "visual-impaired",
        "text-descriptions",
    }
)


@dataclass
class Edits:
    title: str | None = None
    delete_title: bool = False
    track_selector: str | None = None
    track_name: str | None = None
    delete_track_name: bool = False
    language: str | None = None
    flags: dict[str, bool] = field(default_factory=dict)
    defaults: dict[str, str] = field(default_factory=dict)
    cover: Path | None = None
    delete_cover: bool = False
    attachment_name: str | None = None
    attachment_mime_type: str | None = None
    tags: Path | None = None
    delete_tags: bool = False
    chapters: Path | None = None
    delete_chapters: bool = False


def _track_edit(args: list[str], track: Track, *actions: str) -> None:
    if actions:
        args.extend(("--edit", f"track:={track.uid}", *actions))


def _validate(edits: Edits) -> None:
    pairs = (
        (edits.title is not None, edits.delete_title, "title"),
        (edits.track_name is not None, edits.delete_track_name, "track name"),
        (edits.cover is not None, edits.delete_cover, "cover"),
        (edits.tags is not None, edits.delete_tags, "tags"),
        (edits.chapters is not None, edits.delete_chapters, "chapters"),
    )
    for has_value, delete, label in pairs:
        if has_value and delete:
            raise MetadataError(f"cannot set and delete {label} in the same edit")
    has_track_mutation = (
        edits.track_name is not None
        or edits.delete_track_name
        or edits.language is not None
        or bool(edits.flags)
    )
    if has_track_mutation and not edits.track_selector:
        raise MetadataError("--track is required for track name, language, and flag edits")
    if edits.track_selector and not has_track_mutation:
        raise MetadataError("--track was provided without a track mutation")
    if (edits.attachment_name or edits.attachment_mime_type) and edits.cover is None:
        raise MetadataError("attachment name and MIME type only apply with --cover")
    for path, label in (
        (edits.cover, "cover"),
        (edits.tags, "tags"),
        (edits.chapters, "chapters"),
    ):
        if path is not None and not path.is_file():
            raise MetadataError(f"{label} file does not exist: {path}")
    if not any(
        (
            edits.title is not None,
            edits.delete_title,
            has_track_mutation,
            bool(edits.defaults),
            edits.cover is not None,
            edits.delete_cover,
            edits.tags is not None,
            edits.delete_tags,
            edits.chapters is not None,
            edits.delete_chapters,
        )
    ):
        raise MetadataError("no metadata mutation was requested")
    unknown_flags = edits.flags.keys() - FLAG_NAMES
    if unknown_flags:
        raise MetadataError(f"unknown track flag(s): {', '.join(sorted(unknown_flags))}")


def build_command(container: Container, edits: Edits) -> list[str]:
    _validate(edits)
    args = ["mkvpropedit", str(container.path), "--abort-on-warnings"]
    if edits.title is not None:
        args.extend(("--edit", "info", "--set", f"title={edits.title}"))
    elif edits.delete_title:
        args.extend(("--edit", "info", "--delete", "title"))

    if edits.track_selector:
        track = select_track(container, edits.track_selector)
        actions: list[str] = []
        if edits.track_name is not None:
            actions.extend(("--set", f"name={edits.track_name}"))
        elif edits.delete_track_name:
            actions.extend(("--delete", "name"))
        if edits.language is not None:
            actions.extend(("--set", f"language={edits.language}"))
        for name, enabled in sorted(edits.flags.items()):
            actions.extend(("--set", f"flag-{name}={int(enabled)}"))
        _track_edit(args, track, *actions)

    for requested_type, selector in edits.defaults.items():
        selected = select_track(container, selector)
        track_type = selected.type if requested_type == "*" else requested_type
        if selected.type != track_type:
            raise MetadataError(f"{selector!r} is {selected.type}, not requested {track_type}")
        for track in container.tracks:
            if track.type == track_type:
                _track_edit(
                    args,
                    track,
                    "--set",
                    f"flag-default={int(track.uid == selected.uid)}",
                )

    covers = cover_attachments(container)
    if edits.delete_cover:
        for attachment in covers:
            args.extend(("--delete-attachment", f"={attachment.uid}"))
    elif edits.cover is not None:
        mime_type = edits.attachment_mime_type or mimetypes.guess_type(edits.cover.name)[0]
        if not mime_type or not mime_type.startswith("image/"):
            raise MetadataError("cover MIME type must be an image type")
        extension = mimetypes.guess_extension(mime_type) or edits.cover.suffix.lower()
        if extension == ".jpe":
            extension = ".jpg"
        name = edits.attachment_name or f"cover{extension}"
        args.extend(("--attachment-name", name, "--attachment-mime-type", mime_type))
        if not covers:
            args.extend(("--add-attachment", str(edits.cover)))
        elif len(covers) == 1:
            args.extend(("--replace-attachment", f"={covers[0].uid}:{edits.cover}"))
        else:
            raise MetadataError("multiple cover attachments found; delete them before adding one")

    if edits.tags is not None:
        args.extend(("--tags", f"all:{edits.tags}"))
    elif edits.delete_tags:
        args.extend(("--tags", "all:"))
    if edits.chapters is not None:
        args.extend(("--chapters", str(edits.chapters)))
    elif edits.delete_chapters:
        args.extend(("--chapters", ""))
    return args
