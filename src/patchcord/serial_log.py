"""Timestamped host-side framing for exact CircuitPython serial output."""

from __future__ import annotations

import json
import os
import re
import sys
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from math import isfinite
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self, cast

from patchcord.errors import TransportError

DEFAULT_TAIL_LINES = 200
MAX_TAIL_LINES = 1_000_000
SESSION_HEADER_PREFIX = b"--- patchcord serial "
SESSION_INDEX_SUFFIX = ".index.jsonl"

_DURATION_RE = re.compile(r"(?P<value>(?:\d+(?:\.\d*)?|\.\d+))(?P<unit>[smhd])", re.IGNORECASE)
_DURATION_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86_400}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_duration(value: str) -> timedelta:
    """Parse a compact non-negative duration such as ``30s``, ``10m``, or ``2h``."""

    match = _DURATION_RE.fullmatch(value.strip())
    if match is None:
        msg = f"Invalid duration {value!r}; use a number followed by s, m, h, or d."
        raise ValueError(msg)
    amount = float(match.group("value"))
    if not isfinite(amount):
        msg = f"Invalid duration {value!r}; duration must be finite."
        raise ValueError(msg)
    unit = match.group("unit").lower()
    try:
        return timedelta(seconds=amount * _DURATION_SECONDS[unit])
    except OverflowError as exc:
        msg = f"Invalid duration {value!r}; duration is too large."
        raise ValueError(msg) from exc


def _header(event: str, timestamp: datetime, **fields: str) -> bytes:
    metadata = {
        "event": event,
        **fields,
        "timestamp": _format_timestamp(timestamp),
    }
    payload = json.dumps(metadata, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return SESSION_HEADER_PREFIX + payload.encode("ascii") + b"\n"


def _parse_timestamp(raw_timestamp: object) -> datetime | None:
    if not isinstance(raw_timestamp, str):
        return None
    try:
        return _as_utc(datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00")))
    except ValueError:
        return None


class SerialLogSession:
    """One append-only session whose payload writes remain byte-for-byte exact."""

    def __init__(
        self,
        *,
        path: Path,
        handle: BinaryIO,
        index_path: Path,
        operation: str,
        port: str,
        clock: Callable[[], datetime],
    ) -> None:
        self.path = path
        self.index_path = index_path
        self.operation = operation
        self.port = port
        self._handle = handle
        self._clock = clock
        self._closed = False
        self._ends_with_newline = True
        self._start_offset = handle.tell()
        self._started_at = self._clock()
        self._write_framing(
            _header("session_start", self._started_at, operation=operation, port=port),
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        self.close(status="error" if exc_type is not None else "ok")

    def write(self, data: bytes) -> int:
        """Append exact board bytes and flush them for live diagnostics."""

        if self._closed:
            msg = "Cannot write to a closed serial log session."
            raise ValueError(msg)
        if not data:
            return 0
        try:
            written = self._handle.write(data)
            self._handle.flush()
        except OSError as exc:
            raise TransportError(
                "serial_log_write_failed",
                f"Could not write serial log {self.path}.",
                details={"path": str(self.path)},
            ) from exc
        self._ends_with_newline = data.endswith((b"\n", b"\r"))
        return written

    def close(self, *, status: str = "ok") -> None:
        """Finish the timestamped frame and reliably close its file handle."""

        if self._closed:
            return
        try:
            if not self._ends_with_newline:
                self._write_framing(b"\n")
            ended_at = self._clock()
            self._write_framing(_header("session_end", ended_at, status=status))
            self._write_index(ended_at)
        finally:
            active_error = sys.exception()
            self._closed = True
            try:
                self._handle.close()
            except OSError as exc:
                if active_error is None:
                    raise TransportError(
                        "serial_log_close_failed",
                        f"Could not close serial log {self.path}.",
                        details={"path": str(self.path)},
                    ) from exc

    def _write_framing(self, data: bytes) -> None:
        try:
            self._handle.write(data)
            self._handle.flush()
        except OSError as exc:
            raise TransportError(
                "serial_log_write_failed",
                f"Could not write serial log {self.path}.",
                details={"path": str(self.path)},
            ) from exc

    def _write_index(self, ended_at: datetime) -> None:
        record = {
            "end_offset": self._handle.tell(),
            "ended_at": _format_timestamp(ended_at),
            "start_offset": self._start_offset,
            "started_at": _format_timestamp(self._started_at),
        }
        encoded = (
            json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
                "ascii"
            )
            + b"\n"
        )
        try:
            with self.index_path.open("ab") as index_handle:
                index_handle.write(encoded)
                index_handle.flush()
                os.fsync(index_handle.fileno())
        except OSError as exc:
            raise TransportError(
                "serial_log_index_write_failed",
                f"Could not write serial log index {self.index_path}.",
                details={"path": str(self.index_path)},
            ) from exc


class SerialLog:
    """Append and query a project's persistent serial log."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.path = path
        self.index_path = path.with_name(f"{path.name}{SESSION_INDEX_SUFFIX}")
        self._clock = clock

    def session(self, *, operation: str, port: str) -> SerialLogSession:
        """Open a timestamped append-only serial session."""

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("ab")
        except OSError as exc:
            raise TransportError(
                "serial_log_open_failed",
                f"Could not open serial log {self.path}.",
                details={"path": str(self.path)},
            ) from exc
        try:
            return SerialLogSession(
                path=self.path,
                handle=handle,
                index_path=self.index_path,
                operation=operation,
                port=port,
                clock=self._clock,
            )
        except BaseException:
            handle.close()
            raise

    def append_session(self, data: bytes, *, operation: str, port: str) -> None:
        """Append one complete framed session."""

        with self.session(operation=operation, port=port) as session:
            session.write(data)

    def read_bytes(
        self,
        *,
        tail: int | None = None,
        since: timedelta | str | None = None,
    ) -> bytes:
        """Read a line tail or sessions active within a recent duration."""

        if tail is not None and since is not None:
            msg = "tail and since are mutually exclusive."
            raise ValueError(msg)
        if tail is not None and tail < 0:
            msg = "tail cannot be negative."
            raise ValueError(msg)
        if tail is not None and tail > MAX_TAIL_LINES:
            msg = f"tail cannot exceed {MAX_TAIL_LINES} lines."
            raise ValueError(msg)

        if since is None:
            line_count = DEFAULT_TAIL_LINES if tail is None else tail
            return self._read_tail(line_count)

        duration = parse_duration(since) if isinstance(since, str) else since
        if duration.total_seconds() < 0:
            msg = "since cannot be negative."
            raise ValueError(msg)
        try:
            cutoff = _as_utc(self._clock()) - duration
        except OverflowError as exc:
            msg = "Invalid duration; duration exceeds the supported date range."
            raise ValueError(msg) from exc
        return self._read_since(cutoff)

    def read(
        self,
        *,
        tail: int | None = None,
        since: timedelta | str | None = None,
    ) -> str:
        """Read log output as display text while retaining malformed byte evidence."""

        return self.read_bytes(tail=tail, since=since).decode("utf-8", errors="replace")

    def _read_tail(self, line_count: int) -> bytes:
        if line_count == 0:
            return b""
        try:
            with self.path.open("rb") as handle:
                return b"".join(deque(handle, maxlen=line_count))
        except FileNotFoundError:
            return b""
        except OSError as exc:
            raise TransportError(
                "serial_log_read_failed",
                f"Could not read serial log {self.path}.",
                details={"path": str(self.path)},
            ) from exc

    def _read_since(self, cutoff: datetime) -> bytes:
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            return b""
        except OSError as exc:
            raise TransportError(
                "serial_log_read_failed",
                f"Could not read serial log {self.path}.",
                details={"path": str(self.path)},
            ) from exc

        try:
            index_lines = self.index_path.read_text(encoding="ascii").splitlines()
        except FileNotFoundError:
            # Legacy or active logs have no trusted index; retain all evidence.
            return data
        except (OSError, UnicodeError):
            return data

        selected: list[bytes] = []
        expected_start = 0
        for line in index_lines:
            try:
                value: object = json.loads(line)
            except json.JSONDecodeError:
                return data
            if not isinstance(value, dict):
                return data
            record = cast("dict[str, object]", value)
            start_offset = record.get("start_offset")
            end_offset = record.get("end_offset")
            started_at = _parse_timestamp(record.get("started_at"))
            ended_at = _parse_timestamp(record.get("ended_at"))
            if (
                not isinstance(start_offset, int)
                or isinstance(start_offset, bool)
                or not isinstance(end_offset, int)
                or isinstance(end_offset, bool)
                or start_offset != expected_start
                or end_offset <= start_offset
                or end_offset > len(data)
                or started_at is None
                or ended_at is None
            ):
                return data
            if max(started_at, ended_at) >= cutoff:
                selected.append(data[start_offset:end_offset])
            expected_start = end_offset
        if expected_start != len(data):
            # A session may still be active. Do not hide unindexed board output.
            return data
        return b"".join(selected)


__all__ = [
    "DEFAULT_TAIL_LINES",
    "MAX_TAIL_LINES",
    "SESSION_HEADER_PREFIX",
    "SESSION_INDEX_SUFFIX",
    "SerialLog",
    "SerialLogSession",
    "parse_duration",
]
