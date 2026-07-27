from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from patchcord.serial_log import (
    MAX_TAIL_LINES,
    SESSION_HEADER_PREFIX,
    SESSION_INDEX_SUFFIX,
    SerialLog,
    parse_duration,
)


class SequenceClock:
    def __init__(self, *values: datetime) -> None:
        self.values = list(values)

    def __call__(self) -> datetime:
        if not self.values:
            raise AssertionError("clock was called more often than expected")
        return self.values.pop(0)


def header_metadata(line: bytes) -> dict[str, object]:
    assert line.startswith(SESSION_HEADER_PREFIX)
    value = cast(object, json.loads(line[len(SESSION_HEADER_PREFIX) :]))
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def test_session_frames_exact_raw_output_with_timestamps(tmp_path: Path) -> None:
    started = datetime(2026, 7, 27, 1, 2, 3, tzinfo=UTC)
    ended = datetime(2026, 7, 27, 1, 2, 4, tzinfo=UTC)
    path = tmp_path / ".patchcord" / "logs" / "serial.log"
    log = SerialLog(path, clock=SequenceClock(started, ended))
    raw = b"hello\r\ninvalid: \xff"

    log.append_session(raw, operation="monitor", port="/dev/cu.test")

    content = path.read_bytes()
    lines = content.splitlines(keepends=True)
    start_header = header_metadata(lines[0])
    end_header = header_metadata(lines[-1])
    assert start_header == {
        "event": "session_start",
        "operation": "monitor",
        "port": "/dev/cu.test",
        "timestamp": "2026-07-27T01:02:03.000000Z",
    }
    assert end_header == {
        "event": "session_end",
        "status": "ok",
        "timestamp": "2026-07-27T01:02:04.000000Z",
    }
    assert raw in content
    index_path = path.with_name(f"{path.name}{SESSION_INDEX_SUFFIX}")
    assert len(index_path.read_text(encoding="ascii").splitlines()) == 1


def test_open_session_can_persist_streamed_chunks(tmp_path: Path) -> None:
    clock = SequenceClock(
        datetime(2026, 7, 27, tzinfo=UTC),
        datetime(2026, 7, 27, 0, 0, 1, tzinfo=UTC),
    )
    log = SerialLog(tmp_path / "serial.log", clock=clock)

    with log.session(operation="monitor", port="COM4") as session:
        assert session.write(b"one") == 3
        assert session.write(b"\r\ntwo\n") == 6

    assert b"one\r\ntwo\n" in log.path.read_bytes()


def test_tail_defaults_to_last_200_lines(tmp_path: Path) -> None:
    path = tmp_path / "serial.log"
    path.write_bytes(b"".join(f"{number}\n".encode() for number in range(205)))
    log = SerialLog(path)

    output = log.read_bytes()

    assert len(output.splitlines()) == 200
    assert output.startswith(b"5\n")
    assert output.endswith(b"204\n")


def test_explicit_tail_and_missing_log(tmp_path: Path) -> None:
    path = tmp_path / "serial.log"
    log = SerialLog(path)

    assert log.read(tail=10) == ""
    path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    assert log.read(tail=2) == "two\nthree\n"
    assert log.read(tail=0) == ""


def test_since_returns_only_recent_sessions(tmp_path: Path) -> None:
    old_start = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)
    old_end = old_start + timedelta(seconds=1)
    new_start = datetime(2026, 7, 27, 11, 55, tzinfo=UTC)
    new_end = new_start + timedelta(seconds=1)
    now = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    log = SerialLog(
        tmp_path / "serial.log",
        clock=SequenceClock(old_start, old_end, new_start, new_end, now),
    )
    log.append_session(b"old output\n", operation="monitor", port="COM1")
    log.append_session(b"new output\n", operation="reset", port="COM1")

    output = log.read(since="10m")

    assert "old output" not in output
    assert "new output" in output
    assert '"operation":"reset"' in output


def test_board_output_cannot_inject_trusted_since_framing(tmp_path: Path) -> None:
    old_start = datetime(2026, 7, 27, 10, 0, tzinfo=UTC)
    old_end = old_start + timedelta(seconds=1)
    now = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
    fake_header = (
        SESSION_HEADER_PREFIX + b'{"event":"session_start","timestamp":"2099-01-01T00:00:00Z"}\n'
    )
    log = SerialLog(
        tmp_path / "serial.log",
        clock=SequenceClock(old_start, old_end, now),
    )
    log.append_session(fake_header + b"old output\n", operation="monitor", port="COM1")

    assert log.read(since="10m") == ""


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("30s", 30), ("10m", 600), ("2h", 7200), ("1.5d", 129_600), (" 0S ", 0)],
)
def test_parse_duration(value: str, seconds: float) -> None:
    assert parse_duration(value).total_seconds() == seconds


@pytest.mark.parametrize(
    "value",
    ["", "10", "seconds", "-1s", "1ms", "1h30m", f"{'9' * 400}d"],
)
def test_parse_duration_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError, match="Invalid duration"):
        parse_duration(value)


def test_tail_and_since_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        SerialLog(tmp_path / "serial.log").read(tail=2, since="1m")


def test_tail_has_a_pragmatic_upper_bound(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot exceed"):
        SerialLog(tmp_path / "serial.log").read(tail=MAX_TAIL_LINES + 1)


def test_since_rejects_duration_that_underflows_datetime(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid duration"):
        SerialLog(tmp_path / "serial.log").read(since="999999999d")
