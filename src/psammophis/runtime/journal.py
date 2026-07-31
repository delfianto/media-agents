"""Durable run journal under ``<state-root>/.cache/psammophis/runs/<run-id>/``.

Writes:
- status.json   — latest state, replaced atomically
- events.jsonl  — append-only normalized events
- summary.json  — terminal result, written only at completion
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .events import SCHEMA_VERSION, Event, RunCompleted, utc_now


@dataclass(frozen=True, slots=True)
class JournalPaths:
    run_dir: Path
    status_path: Path
    events_path: Path
    summary_path: Path


def default_state_root(
    *,
    state_dir: Path | None = None,
    media_root: Path | None = None,
    medialib_root: str | None = None,
) -> Path | None:
    """Resolve where journals live, or None if journaling should stay disabled.

    ``--state-dir PATH`` names the Psammophis state directory directly (runs at
    ``PATH/runs``). Otherwise journals live at
    ``<media-root>/.cache/psammophis`` when a contextual root is available.
    """
    if state_dir is not None:
        return Path(state_dir)
    if media_root is not None:
        return Path(media_root) / ".cache" / "psammophis"
    if medialib_root:
        return Path(medialib_root) / ".cache" / "psammophis"
    return None


def journal_paths(state_root: Path, run_id: str) -> JournalPaths:
    run_dir = Path(state_root) / "runs" / run_id
    return JournalPaths(
        run_dir=run_dir,
        status_path=run_dir / "status.json",
        events_path=run_dir / "events.jsonl",
        summary_path=run_dir / "summary.json",
    )


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


class JournalSink:
    """EventSink that appends events and rewrites status.json atomically."""

    def __init__(
        self,
        paths: JournalPaths,
        *,
        command: str,
        run_id: str,
        pid: int | None = None,
    ) -> None:
        self.paths = paths
        self.command = command
        self.run_id = run_id
        self.pid = os.getpid() if pid is None else pid
        self.hostname = socket.gethostname()
        self.started_at = utc_now()
        self._status: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "run_id": run_id,
            "command": command,
            "pid": self.pid,
            "hostname": self.hostname,
            "started_at": self.started_at,
            "updated_at": self.started_at,
            "state": "running",
            "current_phase": None,
            "current_item": None,
            "counts": {},
            "percent": None,
            "exit_code": None,
        }
        self.paths.run_dir.mkdir(parents=True, exist_ok=True)
        self.paths.events_path.touch(exist_ok=True)
        self._write_status()

    def emit(self, event: Event) -> None:
        line = event.to_json()
        with open(self.paths.events_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
        self._apply_event(event)
        self._write_status()
        if isinstance(event, RunCompleted):
            summary = event.to_dict()
            summary["hostname"] = self.hostname
            summary["pid"] = self.pid
            _atomic_write_json(self.paths.summary_path, summary)

    def close(self) -> None:
        return None

    def _apply_event(self, event: Event) -> None:
        data = event.to_dict()
        self._status["updated_at"] = data.get("timestamp", utc_now())
        self._status["last_seq"] = data.get("seq")
        name = data.get("event")
        if name == "run.started":
            if data.get("root") is not None:
                self._status["root"] = data["root"]
            if data.get("mode") is not None:
                self._status["mode"] = data["mode"]
            if data.get("items_total") is not None:
                self._status["items_total"] = data["items_total"]
        elif name == "phase.started":
            self._status["current_phase"] = data.get("phase")
            if data.get("item") is not None:
                self._status["current_item"] = data.get("item")
        elif name == "item.started":
            self._status["current_item"] = data.get("item")
        elif name == "item.progress":
            if data.get("percent") is not None:
                self._status["percent"] = data["percent"]
            if data.get("phase") is not None:
                self._status["current_phase"] = data["phase"]
            if data.get("item") is not None:
                self._status["current_item"] = data["item"]
        elif name == "run.completed":
            self._status["state"] = data.get("status", "succeeded")
            self._status["exit_code"] = data.get("exit_code")
            self._status["percent"] = (
                100.0 if data.get("status") == "succeeded" else self._status.get("percent")
            )
            counts = {
                key: data[key]
                for key in ("changed", "planned", "errors", "review")
                if data.get(key) is not None
            }
            if counts:
                self._status["counts"] = counts

    def _write_status(self) -> None:
        _atomic_write_json(self.paths.status_path, self._status)


def read_status(status_path: Path) -> dict[str, Any]:
    return json.loads(status_path.read_text(encoding="utf-8"))


def read_events(events_path: Path, *, after: int = 0) -> list[dict[str, Any]]:
    if not events_path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with open(events_path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if int(data.get("seq", 0)) > after:
                out.append(data)
    return out


def list_runs(state_root: Path) -> list[dict[str, Any]]:
    runs_dir = Path(state_root) / "runs"
    if not runs_dir.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for child in sorted(runs_dir.iterdir(), reverse=True):
        status_path = child / "status.json"
        if not status_path.is_file():
            continue
        try:
            results.append(read_status(status_path))
        except OSError, json.JSONDecodeError:
            continue
    return results


def is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but not owned by us — treat as alive.
        return True
    return True


def annotate_stale(status: dict[str, Any]) -> dict[str, Any]:
    """If a journal says running but the PID is gone, mark it stale."""
    out = dict(status)
    if out.get("state") == "running":
        pid = out.get("pid")
        host = out.get("hostname")
        if isinstance(pid, int) and host == socket.gethostname() and not is_process_alive(pid):
            out["state"] = "stale"
            out["stale_reason"] = "process no longer alive on recorded host"
    return out
