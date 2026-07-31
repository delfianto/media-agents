"""Shared subprocess supervision for long-running external tools.

Drains stdout and stderr concurrently so pipes cannot deadlock, tees complete
diagnostics into a per-file log, retains a bounded error tail, and invokes a
backend-specific progress parser.
"""

from __future__ import annotations

import contextlib
import os
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, TextIO

ProgressCallback = Callable[[dict[str, float | str | None]], None]
LineCallback = Callable[[str, str], None]  # stream_name, line
HeartbeatCallback = Callable[[], None]


def _open_log_file(path: Path) -> TextIO:
    """Open a live log for replacement without following special entries."""
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if not stat.S_ISREG(existing.st_mode):
            raise OSError(f"refusing to replace non-regular log path: {path}")
        if existing.st_nlink != 1:
            raise OSError(f"refusing to truncate multiply-linked log path: {path}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o666)
    return os.fdopen(fd, "w", encoding="utf-8", errors="replace")


@dataclass(slots=True)
class ProcessResult:
    returncode: int
    tail: str
    cancelled: bool = False
    stdout: str = ""
    stderr: str = ""


@dataclass(slots=True)
class ProcessSupervisor:
    """Run a child process with concurrent stream draining."""

    cmd: list[str]
    log_path: Path | None = None
    progress_parser: Callable[[str, str], dict[str, float | str | None] | None] | None = None
    on_progress: ProgressCallback | None = None
    on_line: LineCallback | None = None
    on_heartbeat: HeartbeatCallback | None = None
    min_progress_interval: float = 1.0
    heartbeat_interval: float = 60.0
    cancellation_grace: float = 5.0
    timeout: float | None = None
    tail_limit: int = 200
    env: dict[str, str] | None = None

    _proc: subprocess.Popen[bytes] | None = field(default=None, init=False, repr=False)
    _cancelled: bool = field(default=False, init=False, repr=False)

    def run(self) -> ProcessResult:
        self._cancelled = False
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

        tail: list[str] = []
        stream_tails: dict[str, list[str]] = {"stdout": [], "stderr": []}
        last_progress = 0.0
        last_heartbeat = time.monotonic()
        process_started = last_heartbeat
        lock = threading.Lock()
        reader_errors: list[BaseException] = []
        error_lock = threading.Lock()
        reader_failed = threading.Event()

        def handle_line(stream_name: str, line: str, log_file: TextIO | None) -> None:
            nonlocal last_progress
            with lock:
                if log_file is not None:
                    log_file.write(line + "\n")
                    log_file.flush()
                tail.append(line)
                if len(tail) > self.tail_limit:
                    del tail[: len(tail) - self.tail_limit]
                stream_tail = stream_tails[stream_name]
                stream_tail.append(line)
                if len(stream_tail) > self.tail_limit:
                    del stream_tail[: len(stream_tail) - self.tail_limit]
                if self.on_line is not None:
                    self.on_line(stream_name, line)
                if self.progress_parser is not None and self.on_progress is not None:
                    parsed = self.progress_parser(stream_name, line)
                    if parsed is not None:
                        now = time.monotonic()
                        terminal = parsed.get("progress") == "end" or parsed.get("percent") == 100.0
                        if terminal or now - last_progress >= self.min_progress_interval:
                            last_progress = now
                            self.on_progress(parsed)

        log_cm = (
            _open_log_file(self.log_path) if self.log_path is not None else contextlib.nullcontext()
        )
        with log_cm as log_file:
            self._proc = subprocess.Popen(
                self.cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                bufsize=0,
                env=self.env,
                start_new_session=os.name == "posix",
            )
            assert self._proc.stdout is not None
            assert self._proc.stderr is not None

            def reader(stream: BinaryIO, name: str) -> None:
                try:
                    for line in _iter_stream_records(stream):
                        handle_line(name, line, log_file)
                except BaseException as exc:
                    with error_lock:
                        reader_errors.append(exc)
                    reader_failed.set()
                    self.cancel()

            threads = [
                threading.Thread(target=reader, args=(self._proc.stdout, "stdout"), daemon=True),
                threading.Thread(target=reader, args=(self._proc.stderr, "stderr"), daemon=True),
            ]
            for thread in threads:
                thread.start()
            try:
                while self._proc.poll() is None or any(thread.is_alive() for thread in threads):
                    for thread in threads:
                        thread.join(timeout=0.1)
                    if reader_failed.is_set():
                        self.cancel()
                        self._wait_after_cancel(threads)
                        with error_lock:
                            reader_error = reader_errors[0]
                        raise RuntimeError(
                            f"subprocess stream consumer failed: {reader_error}"
                        ) from reader_error
                    if self._cancelled:
                        self._wait_after_cancel(threads)
                        break
                    now = time.monotonic()
                    if (
                        self.timeout is not None
                        and self._proc.poll() is None
                        and now - process_started >= self.timeout
                    ):
                        self.cancel()
                        self._wait_after_cancel(threads)
                        raise subprocess.TimeoutExpired(self.cmd, self.timeout)
                    if (
                        self.on_heartbeat is not None
                        and now - last_heartbeat >= self.heartbeat_interval
                    ):
                        last_heartbeat = now
                        with lock:
                            self.on_heartbeat()
                returncode = self._proc.wait()
                with error_lock:
                    reader_error = reader_errors[0] if reader_errors else None
                if reader_error is not None:
                    raise RuntimeError(
                        f"subprocess stream consumer failed: {reader_error}"
                    ) from reader_error
            except BaseException as exc:
                requested_signal = getattr(exc, "signum", None)
                if not isinstance(requested_signal, int):
                    requested_signal = (
                        signal.SIGINT if isinstance(exc, KeyboardInterrupt) else signal.SIGTERM
                    )
                self.cancel(requested_signal)
                self._wait_after_cancel(threads)
                raise
            return ProcessResult(
                returncode=returncode,
                tail="\n".join(tail),
                cancelled=self._cancelled,
                stdout="\n".join(stream_tails["stdout"]),
                stderr="\n".join(stream_tails["stderr"]),
            )

    def cancel(self, sig: int = signal.SIGTERM) -> None:
        self._cancelled = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                if os.name == "posix":
                    os.killpg(proc.pid, sig)
                else:
                    os.kill(proc.pid, sig)

    def _wait_after_cancel(self, threads: list[threading.Thread]) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            proc.wait(timeout=self.cancellation_grace)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
            proc.wait()
        for thread in threads:
            thread.join(timeout=1.0)


def _iter_stream_records(stream: BinaryIO):
    """Yield records immediately for either carriage-return or newline framing."""
    buffer = bytearray()
    while True:
        chunk = stream.read(4096)
        if chunk == b"":
            break
        for char in chunk:
            if char in (10, 13):
                if buffer:
                    yield buffer.decode("utf-8", errors="replace")
                    buffer.clear()
                continue
            buffer.append(char)
    if buffer:
        yield buffer.decode("utf-8", errors="replace")


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
            percent = min(100.0, max(0.0, float(position) / total_duration * 100.0))
            result["percent"] = percent
            speed = result.get("speed")
            if isinstance(speed, (int, float)) and speed > 0:
                result["eta_seconds"] = max(0.0, (total_duration - float(position)) / float(speed))
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
        "percent": min(100.0, max(0.0, float(groups["percent"]))),
        "fps": float(groups["fps"]),
        "frames": float(groups["frames"]),
        "bitrate_kbps": float(groups["bitrate"]),
        "eta_seconds": eta_seconds,
        "backend": "nvencc",
        "raw": cleaned,
    }
