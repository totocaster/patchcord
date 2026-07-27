from __future__ import annotations

from patchcord.hardware.connected import ConnectedHardwareValidator
from patchcord.hardware.models import HardwareDocument
from patchcord.hardware.validation import DiagnosticSeverity
from patchcord.probes import I2CResult, PinsResult


def _document() -> HardwareDocument:
    return HardwareDocument.model_validate(
        {
            "schema_version": 1,
            "board": {"id": "expected_board"},
            "parts": {
                "sensor": {
                    "kind": "sensor",
                    "pins": {"SDA": {"role": "i2c_sda"}, "SCL": {"role": "i2c_scl"}},
                    "interfaces": [{"kind": "i2c", "bus": "default", "address": 0x3C}],
                }
            },
            "nets": {
                "sda": {"role": "signal", "endpoints": ["board.SDA", "sensor.SDA"]},
                "scl": {"role": "signal", "endpoints": ["board.SCL", "sensor.SCL"]},
            },
        }
    )


def test_connected_validation_reports_board_pins_and_i2c_differences() -> None:
    def pins(_port: str) -> tuple[PinsResult, None]:
        return (
            {
                "board_id": "other_board",
                "pins": [{"name": "SDA", "identity": 1}],
            },
            None,
        )

    def i2c(_port: str) -> tuple[I2CResult, None]:
        return ({"addresses": [0x76], "addresses_hex": ["0x76"]}, None)

    diagnostics = ConnectedHardwareValidator("port", pins, i2c).validate_connected(_document())
    codes = {item.code for item in diagnostics}

    assert "board_id_mismatch" in codes
    assert "unknown_board_pin" in codes
    assert "missing_i2c_address" in codes
    assert "undeclared_i2c_address" in codes
    assert any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics)


def test_connected_validation_detects_physical_alias_conflict() -> None:
    document = _document()

    def pins(_port: str) -> tuple[PinsResult, None]:
        return (
            {
                "board_id": "expected_board",
                "pins": [
                    {"name": "SDA", "identity": 7},
                    {"name": "SCL", "identity": 7},
                ],
            },
            None,
        )

    def i2c(_port: str) -> tuple[I2CResult, None]:
        return ({"addresses": [0x3C], "addresses_hex": ["0x3c"]}, None)

    diagnostics = ConnectedHardwareValidator("port", pins, i2c).validate_connected(document)

    assert [item.code for item in diagnostics].count("conflicting_board_alias") == 2
