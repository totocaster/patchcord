"""Packaged, versioned CircuitPython probes run by the bounded adapter."""

from __future__ import annotations

from collections.abc import Mapping
from importlib.resources import files
from typing import TypedDict, cast

from patchcord.adapters.execution import ExecutionResult, run_script
from patchcord.errors import BoardExecutionError


class PinRecord(TypedDict):
    name: str
    identity: int


class PinsResult(TypedDict):
    board_id: str
    pins: list[PinRecord]


class I2CResult(TypedDict):
    addresses: list[int]
    addresses_hex: list[str]


def _source(name: str) -> str:
    resource = files("patchcord").joinpath("resources", name)
    return resource.read_text(encoding="utf-8")


def _run_probe(port: str, name: str, kind: str, *, timeout: float = 30) -> ExecutionResult:
    return run_script(
        port,
        _source(name),
        expected_kind=kind,
        timeout=timeout,
        reset=True,
        source_emits_frame=True,
    )


def probe_pins(port: str, *, timeout: float = 30) -> tuple[PinsResult, ExecutionResult]:
    """Return runtime board pin aliases and physical identity tokens."""

    execution = _run_probe(port, "probe_pins.py.txt", "pins", timeout=timeout)
    raw_pins = execution.payload.get("pins")
    if not isinstance(raw_pins, list):
        raise BoardExecutionError(
            "invalid_probe_result",
            "The board returned an invalid pin probe result.",
        )
    pins: list[PinRecord] = []
    for raw_pin in cast("list[object]", raw_pins):
        if not isinstance(raw_pin, Mapping):
            raise BoardExecutionError(
                "invalid_probe_result",
                "The board returned an invalid pin probe result.",
            )
        pin_mapping = cast("Mapping[object, object]", raw_pin)
        name = pin_mapping.get("name")
        identity = pin_mapping.get("identity")
        if not isinstance(name, str) or not isinstance(identity, int) or isinstance(identity, bool):
            raise BoardExecutionError(
                "invalid_probe_result",
                "The board returned an invalid pin probe result.",
            )
        pins.append({"name": name, "identity": identity})
    board_id = execution.payload.get("board_id")
    if not isinstance(board_id, str):
        raise BoardExecutionError(
            "invalid_probe_result",
            "The pin probe did not return the board ID.",
        )
    return {"board_id": board_id, "pins": pins}, execution


def probe_i2c(port: str, *, timeout: float = 30) -> tuple[I2CResult, ExecutionResult]:
    """Return addresses discovered on CircuitPython's default I²C bus."""

    execution = _run_probe(port, "probe_i2c.py.txt", "i2c", timeout=timeout)
    raw_addresses = execution.payload.get("addresses")
    if not isinstance(raw_addresses, list):
        raise BoardExecutionError(
            "invalid_probe_result",
            "The board returned an invalid I²C probe result.",
        )
    addresses: list[int] = []
    for address in cast("list[object]", raw_addresses):
        if not isinstance(address, int) or isinstance(address, bool) or not 0 <= address <= 0x7F:
            raise BoardExecutionError(
                "invalid_probe_result",
                "The board returned an invalid I²C probe result.",
            )
        addresses.append(address)
    normalized = sorted(set(addresses))
    return {
        "addresses": normalized,
        "addresses_hex": [f"0x{address:02x}" for address in normalized],
    }, execution
