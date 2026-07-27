from __future__ import annotations

import pytest
from serial import SerialException

from patchcord.adapters.serial import (
    CTRL_C,
    CTRL_D,
    SerialFactory,
    SerialTransport,
    contains_traceback,
)
from patchcord.errors import TransportError


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class FakeSerial:
    def __init__(
        self,
        clock: FakeClock,
        reads: list[bytes | BaseException] | None = None,
        *,
        write_error: bool = False,
        close_error: bool = False,
    ) -> None:
        self.timeout: float | None = 0.1
        self.clock = clock
        self.reads = list(reads or [])
        self.write_error = write_error
        self.close_error = close_error
        self.writes: list[tuple[float, bytes]] = []
        self.closed = False

    def read(self, size: int = 1) -> bytes:
        del size
        self.clock.sleep(self.timeout or 0)
        if not self.reads:
            return b""
        value = self.reads.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def write(self, data: bytes) -> int:
        if self.write_error:
            raise SerialException("write failed")
        self.writes.append((self.clock(), data))
        return len(data)

    def flush(self) -> None:
        return

    def close(self) -> None:
        self.closed = True
        if self.close_error:
            raise SerialException("close failed")


def transport_for(
    connection: FakeSerial,
    clock: FakeClock,
    *,
    factory: SerialFactory | None = None,
) -> SerialTransport:
    selected_factory: SerialFactory
    if factory is None:

        def connection_factory(
            *,
            port: str,
            baudrate: int,
            timeout: float,
            write_timeout: float,
        ) -> FakeSerial:
            del port, baudrate, timeout, write_timeout
            return connection

        selected_factory = connection_factory
    else:
        selected_factory = factory
    return SerialTransport(
        "/dev/test",
        serial_factory=selected_factory,
        clock=clock,
        sleep=clock.sleep,
        control_delay=0,
    )


def test_monitor_captures_raw_bytes_and_calls_callback() -> None:
    clock = FakeClock()
    connection = FakeSerial(clock, [b"hello\r\n", b"\xff"])
    seen: list[bytes] = []

    result = transport_for(connection, clock).monitor(duration=0.25, on_data=seen.append)

    assert result.raw_output == b"hello\r\n\xff"
    assert result.text == "hello\r\n\ufffd"
    assert seen == [b"hello\r\n", b"\xff"]
    assert result.duration == pytest.approx(0.25)
    assert connection.closed


def test_monitor_keyboard_interrupt_is_a_clean_stop() -> None:
    clock = FakeClock()
    connection = FakeSerial(clock, [b"before", KeyboardInterrupt()])

    result = transport_for(connection, clock).monitor()

    assert result.raw_output == b"before"
    assert result.interrupted
    assert connection.closed


def test_streaming_monitor_can_avoid_retaining_unbounded_output() -> None:
    clock = FakeClock()
    connection = FakeSerial(clock, [b"streamed"])
    seen: list[bytes] = []

    result = transport_for(connection, clock).monitor(
        duration=0.1,
        on_data=seen.append,
        retain_output=False,
    )

    assert seen == [b"streamed"]
    assert result.raw_output == b""


def test_interrupt_sends_ctrl_c_and_is_bounded() -> None:
    clock = FakeClock()
    connection = FakeSerial(clock, [b">>> "])

    result = transport_for(connection, clock).interrupt(duration=0.2)

    assert [data for _, data in connection.writes] == [CTRL_C]
    assert result.raw_output == b">>> "
    assert result.duration == pytest.approx(0.2)
    assert connection.closed


def test_reset_capture_begins_before_ctrl_d_and_classifies_traceback() -> None:
    clock = FakeClock()
    output = b'Traceback (most recent call last):\r\n  File "code.py", line 1\r\nError\r\n'
    connection = FakeSerial(clock, [output])

    result = transport_for(connection, clock).reset(capture=0.1)

    assert [data for _, data in connection.writes] == [CTRL_C, CTRL_D]
    ctrl_d_time = connection.writes[1][0]
    assert result.started_at <= ctrl_d_time
    assert result.raw_output == output
    assert result.traceback_detected


@pytest.mark.parametrize(
    ("operation", "expected_code"),
    [
        ("open", "serial_open_failed"),
        ("read", "serial_read_failed"),
        ("write", "serial_write_failed"),
        ("close", "serial_close_failed"),
    ],
)
def test_serial_failures_have_stable_errors(operation: str, expected_code: str) -> None:
    clock = FakeClock()
    connection = FakeSerial(
        clock,
        [SerialException("read failed")] if operation == "read" else [],
        write_error=operation == "write",
        close_error=operation == "close",
    )

    if operation == "open":

        def factory(
            *,
            port: str,
            baudrate: int,
            timeout: float,
            write_timeout: float,
        ) -> FakeSerial:
            del port, baudrate, timeout, write_timeout
            raise SerialException("open failed")

    else:

        def factory(
            *,
            port: str,
            baudrate: int,
            timeout: float,
            write_timeout: float,
        ) -> FakeSerial:
            del port, baudrate, timeout, write_timeout
            return connection

    transport = transport_for(connection, clock, factory=factory)

    with pytest.raises(TransportError) as raised:
        if operation == "write":
            transport.interrupt(duration=0)
        else:
            transport.monitor(duration=0.1 if operation == "read" else 0)

    assert raised.value.code == expected_code
    if operation != "open":
        assert connection.closed


def test_callback_failure_still_closes_serial_port() -> None:
    clock = FakeClock()
    connection = FakeSerial(clock, [b"data"])

    def fail_callback(_chunk: bytes) -> None:
        raise RuntimeError("consumer failed")

    with pytest.raises(RuntimeError, match="consumer failed"):
        transport_for(connection, clock).monitor(duration=0.1, on_data=fail_callback)

    assert connection.closed


def test_traceback_classifier_accepts_bytes_and_text() -> None:
    assert contains_traceback(b"Traceback (most recent call last):\n")
    assert contains_traceback("prefix Traceback (most recent call last): suffix")
    assert not contains_traceback(b"normal output")


@pytest.mark.parametrize("duration", [-1.0, float("inf"), float("nan")])
def test_bounded_operations_reject_non_finite_or_negative_duration(duration: float) -> None:
    clock = FakeClock()
    connection = FakeSerial(clock)

    with pytest.raises(ValueError, match="finite and non-negative"):
        transport_for(connection, clock).interrupt(duration=duration)

    assert not connection.closed
