"""Shared subprocess supervision for long-running external tools.

Drains stdout and stderr concurrently so pipes cannot deadlock, tees complete
diagnostics into a per-file log, retains a bounded error tail, and invokes a
backend-specific progress parser.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

ProgressCallback = Callable[[dict[str, float | str | None]], None]
LineCallback = Callable[[str, str], None]  # stream_name, line


@dataclass(slots=True)
class ProcessResult:
    returncode: int
    tail: str
    cancelled: bool = False


@dataclass(slots=True)
class ProcessSupervisor:
    """Run a child process with concurrent stream draining."""

    cmd: list[str]
    log_path: Path | None = None
    progress_parser: Callable[[str, str], dict[str, float | str | None] | None] | None = None
    on_progress: ProgressCallback | None = None
    on_line: LineCallback | None = None
    min_progress_interval: float = 1.0
    tail_limit: int = 200
    env: dict[str, str] | None = None

    _proc: subprocess.Popen[str] | None = field(default=None, init=False, repr=False)
    _cancelled: bool = field(default=False, init=False, repr=False)

    def run(self) -> ProcessResult:
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

        tail: list[str] = []
        last_progress = 0.0
        lock = threading.Lock()

        def handle_line(stream_name: str, line: str, log_file: TextIO | None) -> None:
            nonlocal last_progress
            with lock:
                if log_file is not None:
                    log_file.write(line + "\n")
                    log_file.flush()
                tail.append(line)
                if len(tail) > self.tail_limit:
                    del tail[: len(tail) - self.tail_limit]
                if self.on_line is not None:
                    self.on_line(stream_name, line)
                if self.progress_parser is not None and self.on_progress is not None:
                    parsed = self.progress_parser(stream_name, line)
                    if parsed is not None:
                        now = time.monotonic()
                        if now - last_progress >= self.min_progress_interval:
                            last_progress = now
                            self.on_progress(parsed)

        log_cm = (
            open(self.log_path, "w", encoding="utf-8", errors="replace")  # noqa: SIM115
            if self.log_path is not None
            else contextlib.nullcontext()
        )
        with log_cm as log_file:
            self._proc = subprocess.Popen(
                self.cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=self.env,
            )
            assert self._proc.stdout is not None
            assert self._proc.stderr is not None

            def reader(stream: TextIO, name: str) -> None:
                for line in stream:
                    handle_line(name, line.rstrip("\n"), log_file)

            threads = [
                threading.Thread(target=reader, args=(self._proc.stdout, "stdout"), daemon=True),
                threading.Thread(target=reader, args=(self._proc.stderr, "stderr"), daemon=True),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            returncode = self._proc.wait()
            return ProcessResult(
                returncode=returncode,
                tail="\n".join(tail),
                cancelled=self._cancelled,
            )

    def cancel(self, sig: int = signal.SIGTERM) -> None:
        self._cancelled = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(proc.pid, sig)


def parse_ffmpeg_progress_line(
    stream_name: str,
    line: str,
    *,
    state: dict[str, float | str | None] | None = None,
) -> dict[str, float | str | None] | None:
    """Parse one key=value line from FFmpeg ``-progress`` output.

    Returns a progress dict when a ``progress=continue|end`` terminator is seen;
    otherwise updates ``state`` in place and returns None.
    """
    if state is None:
        return None
    line = line.strip()
    if not line or "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    value = value.strip()
    if key == "progress":
        result = dict(state)
        result["progress"] = value
        # Normalize common fields
        if "out_time_us" in state and state["out_time_us"] not in (None, "N/A"):
            with contextlib.suppress(TypeError, ValueError):
                result["media_position"] = int(str(state["out_time_us"])) / 1_000_000.0
        if "fps" in state:
            with contextlib.suppress(TypeError, ValueError):
                result["fps"] = float(str(state["fps"]))
        if "speed" in state and state["speed"] not in (None, "N/A"):
            speed_raw = str(state["speed"]).rstrip("x")
            with contextlib.suppress(TypeError, ValueError):
                result["speed"] = float(speed_raw)
        state.clear()
        return result
    state[key] = value
    return None


def make_ffmpeg_progress_parser(
    total_duration: float | None = None,
) -> Callable[[str, str], dict[str, float | str | None] | None]:
    state: dict[str, float | str | None] = {}

    def parser(stream_name: str, line: str) -> dict[str, float | str | None] | None:
        # FFmpeg -progress goes to the nominated pipe (typically stdout).
        if stream_name not in ("stdout", "progress"):
            return None
        result = parse_ffmpeg_progress_line(stream_name, line, state=state)
        if result is None:
            return None
        position = result.get("media_position")
        if isinstance(position, (int, float)) and total_duration and total_duration > 0:
            percent = min(100.0, float(position) / total_duration * 100.0)
            result["percent"] = percent
            speed = result.get("speed")
            if isinstance(speed, (int, float)) and speed > 0:
                result["eta_seconds"] = (total_duration - float(position)) / float(speed)
            result["media_duration"] = total_duration
        return result

    return parser


_NVENCC_PROGRESS_RE = __import__("re").compile(
    r"\[(?P<percent>\d+(?:\.\d+)?)%\]\s+"
    r"(?P<frames>\d+)\s+frames:\s+"
    r"(?P<fps>\d+(?:\.\d+)?)\s+fps,\s+"
    r"(?P<bitrate>\d+(?:\.\d+)?)\s+kbps,\s+"
    r"remain\s+(?P<remain>[0-9:]+)"
)


def parse_nvencc_progress_line(stream_name: str, line: str) -> dict[str, float | str | None] | None:
    """Parse an NVEncC carriage-return progress line.

    Observed grammar (NVEncC 9.27)::

        [45.7%] 62 frames: 313.13 fps, 198 kbps, remain 0:00:00, GPU 18%, ...
    """
    # Strip ANSI colour codes if present.
    cleaned = __import__("re").sub(r"\x1b\[[0-9;]*m", "", line).strip()
    if not cleaned:
        return None
    match = _NVENCC_PROGRESS_RE.search(cleaned)
    if not match:
        return None
    groups = match.groupdict()
    remain = groups["remain"]
    eta_seconds: float | None = None
    parts = remain.split(":")
    try:
        if len(parts) == 3:
            eta_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            eta_seconds = int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        eta_seconds = None
    return {
        "percent": float(groups["percent"]),
        "fps": float(groups["fps"]),
        "frames": float(groups["frames"]),
        "bitrate_kbps": float(groups["bitrate"]),
        "eta_seconds": eta_seconds,
        "backend": "nvencc",
        "raw": cleaned,
    }
