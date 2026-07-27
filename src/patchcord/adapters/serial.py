"""Small, bounded pyserial operations against an explicitly selected port."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from math import isfinite
from typing import Protocol, cast

import serial as pyserial

from patchcord.errors import TransportError

CTRL_C = b"\x03"
CTRL_D = b"\x04"
DEFAULT_BAUDRATE = 115_200
DEFAULT_CAPTURE_SECONDS = 5.0
DEFAULT_INTERRUPT_SECONDS = 1.0
DEFAULT_READ_TIMEOUT = 0.1
DEFAULT_WRITE_TIMEOUT = 1.0

_SERIAL_FAILURES = (pyserial.SerialException, OSError)
_TRACEBACK_MARKER = b"Traceback (most recent call last):"


class SerialConnection(Protocol):
    """The public pyserial surface used by Patchcord."""

    timeout: float | None

    def read(self, size: int = 1) -> bytes: ...

    def write(self, data: bytes) -> int | None: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


class SerialFactory(Protocol):
    """A serial constructor, kept injectable for transport tests."""

    def __call__(
        self,
        *,
        port: str,
        baudrate: int,
        timeout: float,
        write_timeout: float,
    ) -> SerialConnection: ...


@dataclass(frozen=True, slots=True)
class SerialCapture:
    """Exact bytes and stable classification from one serial capture."""

    raw_output: bytes
    started_at: float
    ended_at: float
    interrupted: bool
    traceback_detected: bool

    @property
    def text(self) -> str:
        """Decode captured output for terminal display without changing the bytes."""

        return self.raw_output.decode("utf-8", errors="replace")

    @property
    def duration(self) -> float:
        """Elapsed capture time according to the injected monotonic clock."""

        return max(0.0, self.ended_at - self.started_at)


def contains_traceback(output: bytes | str) -> bool:
    """Classify CircuitPython's standard uncaught-traceback marker."""

    raw = output.encode("utf-8") if isinstance(output, str) else output
    return _TRACEBACK_MARKER in raw


def _default_serial_factory(
    *,
    port: str,
    baudrate: int,
    timeout: float,
    write_timeout: float,
) -> SerialConnection:
    return cast(
        SerialConnection,
        pyserial.Serial(
            port=port,
            baudrate=baudrate,
            timeout=timeout,
            write_timeout=write_timeout,
        ),
    )


class SerialTransport:
    """Perform only monitor, interrupt, and soft-reset serial operations."""

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = DEFAULT_BAUDRATE,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        write_timeout: float = DEFAULT_WRITE_TIMEOUT,
        serial_factory: SerialFactory = _default_serial_factory,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        read_size: int = 4096,
        control_delay: float = 0.05,
    ) -> None:
        if not port:
            msg = "A serial port is required."
            raise ValueError(msg)
        if baudrate <= 0:
            msg = "baudrate must be greater than zero."
            raise ValueError(msg)
        if not isfinite(read_timeout) or read_timeout <= 0:
            msg = "read_timeout must be finite and greater than zero."
            raise ValueError(msg)
        if not isfinite(write_timeout) or write_timeout <= 0:
            msg = "write_timeout must be finite and greater than zero."
            raise ValueError(msg)
        if read_size <= 0:
            msg = "read_size must be greater than zero."
            raise ValueError(msg)
        if not isfinite(control_delay) or control_delay < 0:
            msg = "control_delay must be finite and non-negative."
            raise ValueError(msg)

        self.port = port
        self.baudrate = baudrate
        self.read_timeout = read_timeout
        self.write_timeout = write_timeout
        self._serial_factory = serial_factory
        self._clock = clock
        self._sleep = sleep
        self._read_size = read_size
        self._control_delay = control_delay

    def monitor(
        self,
        *,
        duration: float | None = None,
        on_data: Callable[[bytes], object] | None = None,
        retain_output: bool = True,
    ) -> SerialCapture:
        """Capture serial bytes until *duration* elapses or the user interrupts."""

        self._validate_duration(duration, bounded=False)
        with self._connection() as connection:
            return self._capture(
                connection,
                duration=duration,
                on_data=on_data,
                retain_output=retain_output,
            )

    def interrupt(
        self,
        *,
        duration: float = DEFAULT_INTERRUPT_SECONDS,
        on_data: Callable[[bytes], object] | None = None,
    ) -> SerialCapture:
        """Send Ctrl-C and capture the bounded console response."""

        self._validate_duration(duration, bounded=True)
        with self._connection() as connection:
            self._write_control(connection, CTRL_C)
            return self._capture(connection, duration=duration, on_data=on_data)

    def reset(
        self,
        *,
        capture: float = DEFAULT_CAPTURE_SECONDS,
        on_data: Callable[[bytes], object] | None = None,
    ) -> SerialCapture:
        """Interrupt, soft-reset, and capture startup output from before Ctrl-D."""

        self._validate_duration(capture, bounded=True)
        with self._connection() as connection:
            self._write_control(connection, CTRL_C)
            if self._control_delay:
                self._sleep(self._control_delay)
            return self._capture(
                connection,
                duration=capture,
                on_data=on_data,
                start_action=lambda: self._write_control(connection, CTRL_D),
            )

    @staticmethod
    def _validate_duration(duration: float | None, *, bounded: bool) -> None:
        if bounded and duration is None:
            msg = "A bounded serial operation requires a duration."
            raise ValueError(msg)
        if duration is not None and (not isfinite(duration) or duration < 0):
            msg = "duration must be finite and non-negative."
            raise ValueError(msg)

    def _open(self) -> SerialConnection:
        try:
            return self._serial_factory(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.read_timeout,
                write_timeout=self.write_timeout,
            )
        except _SERIAL_FAILURES as exc:
            raise TransportError(
                "serial_open_failed",
                f"Could not open serial port {self.port}.",
                details={"port": self.port},
            ) from exc

    @contextmanager
    def _connection(self) -> Generator[SerialConnection]:
        connection = self._open()
        try:
            yield connection
        finally:
            active_error = sys.exception()
            try:
                connection.close()
            except _SERIAL_FAILURES as exc:
                if active_error is None:
                    raise TransportError(
                        "serial_close_failed",
                        f"Could not close serial port {self.port}.",
                        details={"port": self.port},
                    ) from exc

    def _write_control(self, connection: SerialConnection, control: bytes) -> None:
        try:
            written = connection.write(control)
            connection.flush()
        except _SERIAL_FAILURES as exc:
            raise TransportError(
                "serial_write_failed",
                f"Could not write to serial port {self.port}.",
                details={"port": self.port},
            ) from exc
        if written is not None and written != len(control):
            raise TransportError(
                "serial_write_incomplete",
                f"Serial port {self.port} accepted only part of a control operation.",
                details={
                    "port": self.port,
                    "expected_bytes": len(control),
                    "written_bytes": written,
                },
            )

    def _capture(
        self,
        connection: SerialConnection,
        *,
        duration: float | None,
        on_data: Callable[[bytes], object] | None,
        start_action: Callable[[], None] | None = None,
        retain_output: bool = True,
    ) -> SerialCapture:
        started_at = self._clock()
        deadline = started_at + duration if duration is not None else None
        chunks: list[bytes] = []
        interrupted = False

        if start_action is not None:
            start_action()

        try:
            while deadline is None or self._clock() < deadline:
                wait = self.read_timeout
                if deadline is not None:
                    wait = min(wait, max(0.0, deadline - self._clock()))
                    if wait == 0:
                        break
                connection.timeout = wait
                before_read = self._clock()
                try:
                    chunk = connection.read(self._read_size)
                except _SERIAL_FAILURES as exc:
                    raise TransportError(
                        "serial_read_failed",
                        f"Could not read from serial port {self.port}.",
                        details={"port": self.port},
                    ) from exc
                after_read = self._clock()
                if chunk:
                    if retain_output:
                        chunks.append(chunk)
                    if on_data is not None:
                        on_data(chunk)
                elif after_read - before_read < wait:
                    self._sleep(wait - max(0.0, after_read - before_read))
        except KeyboardInterrupt:
            interrupted = True

        ended_at = self._clock()
        raw_output = b"".join(chunks)
        return SerialCapture(
            raw_output=raw_output,
            started_at=started_at,
            ended_at=ended_at,
            interrupted=interrupted,
            traceback_detected=contains_traceback(raw_output),
        )


__all__ = [
    "CTRL_C",
    "CTRL_D",
    "DEFAULT_CAPTURE_SECONDS",
    "DEFAULT_INTERRUPT_SECONDS",
    "SerialCapture",
    "SerialConnection",
    "SerialFactory",
    "SerialTransport",
    "contains_traceback",
]
