from __future__ import annotations

from typing import Any

import pytest

from patchcord import probes
from patchcord.adapters.execution import ExecutionResult
from patchcord.errors import BoardExecutionError


def test_pin_probe_normalizes_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    execution = ExecutionResult(
        "",
        {
            "schema_version": 1,
            "kind": "pins",
            "board_id": "test_board",
            "pins": [{"name": "D5", "identity": 42}],
        },
        "0.12.0",
        "",
    )

    def run_probe(_port: str, _name: str, _kind: str, *, timeout: float = 30) -> ExecutionResult:
        del timeout
        return execution

    monkeypatch.setattr(probes, "_run_probe", run_probe)

    result, returned_execution = probes.probe_pins("/dev/test")

    assert result["board_id"] == "test_board"
    assert result["pins"] == [{"name": "D5", "identity": 42}]
    assert returned_execution is execution


def test_i2c_probe_sorts_and_formats_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    execution = ExecutionResult(
        "",
        {"schema_version": 1, "kind": "i2c", "addresses": [0x76, 0x3C, 0x3C]},
        "0.12.0",
        "",
    )

    def run_probe(_port: str, _name: str, _kind: str, *, timeout: float = 30) -> ExecutionResult:
        del timeout
        return execution

    monkeypatch.setattr(probes, "_run_probe", run_probe)

    result, _ = probes.probe_i2c("/dev/test")

    assert result == {"addresses": [0x3C, 0x76], "addresses_hex": ["0x3c", "0x76"]}


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1, "kind": "i2c", "addresses": ["0x3c"]},
        {"schema_version": 1, "kind": "i2c", "addresses": [128]},
        {"schema_version": 1, "kind": "i2c", "addresses": [True]},
    ],
)
def test_i2c_probe_rejects_invalid_payload(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> None:
    execution = ExecutionResult("", payload, "0.12.0", "")

    def run_probe(_port: str, _name: str, _kind: str, *, timeout: float = 30) -> ExecutionResult:
        del timeout
        return execution

    monkeypatch.setattr(probes, "_run_probe", run_probe)

    with pytest.raises(BoardExecutionError):
        probes.probe_i2c("/dev/test")
