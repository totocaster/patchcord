"""Connected-board checks layered on structurally sound offline validation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from patchcord.adapters.execution import ExecutionResult
from patchcord.errors import DependencyError, PatchcordError
from patchcord.hardware.models import HardwareDocument
from patchcord.hardware.validation import Diagnostic, DiagnosticSeverity
from patchcord.probes import I2CResult, PinsResult, probe_i2c, probe_pins

PinsProbe = Callable[[str], tuple[PinsResult, object]]
I2CProbe = Callable[[str], tuple[I2CResult, object]]


def _record(
    severity: DiagnosticSeverity,
    code: str,
    message: str,
    path: str | None = None,
    **details: Any,
) -> Diagnostic:
    return Diagnostic(
        severity=severity,
        code=code,
        message=message,
        path=path,
        details=details,
    )


def _error(code: str, message: str, path: str | None = None, **details: Any) -> Diagnostic:
    return _record(DiagnosticSeverity.ERROR, code, message, path, **details)


def _warning(code: str, message: str, path: str | None = None, **details: Any) -> Diagnostic:
    return _record(DiagnosticSeverity.WARNING, code, message, path, **details)


@dataclass(slots=True)
class ConnectedHardwareValidator:
    """Validate runtime aliases, board identity, and expected default-I²C devices."""

    port: str
    pins_probe: PinsProbe = probe_pins
    i2c_probe: I2CProbe = probe_i2c
    execution_output: Callable[[str], object] | None = None

    def validate_connected(self, document: HardwareDocument) -> Sequence[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        try:
            pin_result, pin_execution = self.pins_probe(self.port)
        except PatchcordError as exc:
            if isinstance(exc, DependencyError):
                raise
            return [
                _error(
                    "connected_pin_probe_failed",
                    "Could not validate board pins on the connected board.",
                    error_code=exc.code,
                )
            ]
        if self.execution_output is not None and isinstance(pin_execution, ExecutionResult):
            self.execution_output(pin_execution.output)

        connected_board_id = pin_result["board_id"]
        if connected_board_id != document.board.id:
            diagnostics.append(
                _error(
                    "board_id_mismatch",
                    "The connected board does not match hardware.yaml.",
                    "$.board.id",
                    declared=document.board.id,
                    connected=connected_board_id,
                )
            )

        identities = {item["name"]: item["identity"] for item in pin_result["pins"]}
        identity_nets: dict[int, list[tuple[str, str, str]]] = defaultdict(list)
        for net_id in sorted(document.nets):
            net = document.nets[net_id]
            for endpoint_index, endpoint in enumerate(net.endpoints):
                if not endpoint.startswith("board."):
                    continue
                alias = endpoint.split(".", 1)[1]
                path = f"$.nets.{net_id}.endpoints[{endpoint_index}]"
                identity = identities.get(alias)
                if identity is None:
                    diagnostics.append(
                        _error(
                            "unknown_board_pin",
                            f"Board pin alias {alias!r} is not exposed by the connected board.",
                            path,
                            alias=alias,
                            endpoint=endpoint,
                        )
                    )
                    continue
                identity_nets[identity].append((net_id, endpoint, path))

        for identity, uses in sorted(identity_nets.items()):
            nets = sorted({net_id for net_id, _, _ in uses})
            if len(nets) < 2:
                continue
            endpoints = sorted({endpoint for _, endpoint, _ in uses})
            for net_id, endpoint, path in uses:
                diagnostics.append(
                    _error(
                        "conflicting_board_alias",
                        "Aliases for one physical board pin are assigned to different nets.",
                        path,
                        endpoint=endpoint,
                        net=net_id,
                        conflicting_nets=nets,
                        aliases=endpoints,
                        identity=identity,
                    )
                )

        expected: dict[int, list[tuple[str, int]]] = defaultdict(list)
        for part_id, part in document.parts.items():
            for interface_index, interface in enumerate(part.interfaces):
                expected[interface.address].append((part_id, interface_index))
        if not expected:
            return diagnostics
        try:
            i2c_result, i2c_execution = self.i2c_probe(self.port)
        except PatchcordError as exc:
            if isinstance(exc, DependencyError):
                raise
            diagnostics.append(
                _error(
                    "i2c_bus_inaccessible",
                    "The connected board's default I²C bus could not be scanned.",
                    error_code=exc.code,
                )
            )
            return diagnostics
        if self.execution_output is not None and isinstance(i2c_execution, ExecutionResult):
            self.execution_output(i2c_execution.output)
        discovered = set(i2c_result["addresses"])
        for address, declarations in sorted(expected.items()):
            if address in discovered:
                continue
            for part_id, interface_index in declarations:
                diagnostics.append(
                    _error(
                        "missing_i2c_address",
                        f"Expected I²C address 0x{address:02x} was not discovered.",
                        f"$.parts.{part_id}.interfaces[{interface_index}].address",
                        address=address,
                        address_hex=f"0x{address:02x}",
                        part=part_id,
                    )
                )
        for address in sorted(discovered - set(expected)):
            diagnostics.append(
                _warning(
                    "undeclared_i2c_address",
                    f"I²C address 0x{address:02x} is not declared in hardware.yaml.",
                    "$",
                    address=address,
                    address_hex=f"0x{address:02x}",
                )
            )
        return diagnostics
