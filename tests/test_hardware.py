"""Offline contract tests for ``hardware.yaml`` version 1."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from patchcord.hardware.models import HardwareDocument
from patchcord.hardware.schema import (
    HARDWARE_SCHEMA_ID,
    hardware_schema,
    hardware_schema_json,
    packaged_hardware_schema,
    write_hardware_schema,
)
from patchcord.hardware.validation import (
    Diagnostic,
    DiagnosticSeverity,
    normalize_library_name,
    parse_hardware_yaml,
    requirement_library_names,
    validate_hardware_file,
    validate_hardware_text,
)

COMPLETE_DOCUMENT = """\
schema_version: 1
board:
  id: adafruit_feather_rp2040
parts:
  oled:
    kind: display
    model: SSD1306 OLED breakout
    pins:
      VIN:
        role: power_in
      GND:
        role: ground
      SDA:
        role: i2c_sda
      SCL:
        role: i2c_scl
    interfaces:
      - kind: i2c
        bus: default
        address: 0x3c
    libraries:
      - adafruit_ssd1306
  status_resistor:
    kind: resistor
    value: 220 ohm
    pins:
      "1":
        role: passive
      "2":
        role: passive
  status_led:
    kind: led
    pins:
      A:
        role: passive
      K:
        role: ground
nets:
  power_3v3:
    role: power
    voltage: 3.3
    endpoints:
      - board.3V3
      - oled.VIN
  ground:
    role: ground
    endpoints:
      - board.GND
      - oled.GND
      - status_led.K
  i2c_sda:
    role: signal
    endpoints:
      - board.SDA
      - oled.SDA
  i2c_scl:
    role: signal
    endpoints:
      - board.SCL
      - oled.SCL
  status_led_drive:
    role: signal
    endpoints:
      - board.D5
      - status_resistor.1
  status_led_after_resistor:
    role: signal
    endpoints:
      - status_resistor.2
      - status_led.A
notes: |
  D5 is active high.
"""

MINIMAL_DOCUMENT = """\
schema_version: 1
board:
  id: test_board
parts: {}
nets: {}
"""
_SSD1306_ADDRESS = 0x3C
_DUPLICATE_PART_COUNT = 2


def _codes(text: str, requirements: str | None = None) -> list[str]:
    return [
        item.code
        for item in validate_hardware_text(text, requirements_text=requirements).diagnostics
    ]


def test_complete_example_is_valid_and_hex_address_remains_integer() -> None:
    report = validate_hardware_text(COMPLETE_DOCUMENT, "adafruit_ssd1306==2.1.0\n")

    assert report.ok
    assert report.errors == ()
    assert report.warnings == ()
    assert report.document is not None
    assert report.document.parts["oled"].interfaces[0].address == _SSD1306_ADDRESS
    assert type(report.document.parts["oled"].interfaces[0].address) is int


def test_minimal_document_is_valid() -> None:
    report = validate_hardware_text(MINIMAL_DOCUMENT)

    assert report.ok
    assert report.document is not None
    assert report.diagnostics == ()


@pytest.mark.parametrize(
    ("fragment", "code"),
    [
        ("notes: null", "invalid_field"),
        ("parts:\n  item:\n    kind: sensor\n    model: null\n    pins: {}", "invalid_field"),
        (
            "parts:\n  item:\n    kind: sensor\n    pins:\n      OUT:\n        role: null",
            "invalid_pin_role",
        ),
        (
            "nets:\n  power:\n    role: power\n    voltage: null\n"
            "    endpoints: [board.A, board.B]",
            "invalid_voltage",
        ),
    ],
)
def test_optional_fields_may_be_absent_but_not_explicitly_null(
    fragment: str,
    code: str,
) -> None:
    text = MINIMAL_DOCUMENT
    if fragment.startswith("parts:"):
        text = text.replace("parts: {}", fragment)
    elif fragment.startswith("nets:"):
        text = text.replace("nets: {}", fragment)
    else:
        text += fragment + "\n"

    assert code in _codes(text)


@pytest.mark.parametrize(
    "library",
    ["--auto", "../evil", "foo/bar", "git+https://example.invalid/repo"],
)
def test_part_libraries_must_be_conservative_circup_bundle_names(library: str) -> None:
    text = MINIMAL_DOCUMENT.replace(
        "parts: {}",
        f"parts:\n  item:\n    kind: sensor\n    pins: {{}}\n    libraries: [{library!r}]",
    )

    assert "invalid_library_name" in _codes(text, f"{library}\n")


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", "root_not_mapping"),
        ("[]\n", "root_not_mapping"),
        ("schema_version: [\n", "malformed_yaml"),
        (
            "schema_version: 1\nschema_version: 1\nboard: {id: x}\nparts: {}\nnets: {}\n",
            "duplicate_mapping_key",
        ),
        (
            "schema_version: !custom 1\nboard: {id: x}\nparts: {}\nnets: {}\n",
            "custom_yaml_tag",
        ),
        (
            "schema_version: 1\nboard: {id: x}\nparts: {}\nnets: {}\n---\n{}\n",
            "multiple_yaml_documents",
        ),
        (
            "%YAML 1.1\n---\nschema_version: 1\nboard: {id: x}\nparts: {}\nnets: {}\n",
            "unsupported_yaml_version",
        ),
        (
            "schema_version: 1\r\nboard: {id: x}\r\nparts: {}\r\nnets: {}\r\n",
            "invalid_line_endings",
        ),
    ],
)
def test_yaml_document_rules(text: str, code: str) -> None:
    report = parse_hardware_yaml(text)

    assert not report.ok
    assert report.document is None
    assert code in {item.code for item in report.errors}


def test_anchors_and_aliases_are_accepted_and_resolved() -> None:
    report = validate_hardware_text(
        """\
schema_version: 1
board:
  id: test_board
parts:
  first:
    kind: connector
    pins: &shared_pins
      P: {role: passive}
  second:
    kind: connector
    pins: *shared_pins
nets:
  link:
    role: signal
    endpoints: [first.P, second.P]
"""
    )

    assert report.ok
    assert report.document is not None
    assert report.document.parts["first"].pins is not report.document.parts["second"].pins


def test_required_fields_unknown_fields_and_schema_version_are_strict() -> None:
    missing = validate_hardware_text("schema_version: 1\nboard: {id: x}\nparts: {}\n")
    unknown = validate_hardware_text(MINIMAL_DOCUMENT + "surprise: true\n")
    unsupported = validate_hardware_text(
        MINIMAL_DOCUMENT.replace("schema_version: 1", "schema_version: 2")
    )
    bool_version = validate_hardware_text(
        MINIMAL_DOCUMENT.replace("schema_version: 1", "schema_version: true")
    )

    assert "missing_required_field" in {item.code for item in missing.errors}
    assert "unknown_field" in {item.code for item in unknown.errors}
    assert _codes(MINIMAL_DOCUMENT.replace("schema_version: 1", "schema_version: 2")) == [
        "unsupported_schema_version"
    ]
    assert not unsupported.ok
    assert _codes(MINIMAL_DOCUMENT.replace("schema_version: 1", "schema_version: true")) == [
        "unsupported_schema_version"
    ]
    assert not bool_version.ok


def test_validation_diagnostics_do_not_echo_unknown_field_values() -> None:
    report = validate_hardware_text(
        MINIMAL_DOCUMENT.replace(
            "board:\n  id: test_board",
            "board:\n  id: test_board\n  wifi_password: supersecret",
        )
    )

    assert "supersecret" not in json.dumps(report.model_dump(mode="json"))

    malformed = validate_hardware_text('wifi_password: "another-secret\n')
    assert "another-secret" not in json.dumps(malformed.model_dump(mode="json"))


@pytest.mark.parametrize("board_id", ["''", "'   '"])
def test_empty_board_id_is_a_semantic_error(board_id: str) -> None:
    text = MINIMAL_DOCUMENT.replace("id: test_board", f"id: {board_id}")
    report = validate_hardware_text(text)

    assert report.document is not None
    assert _codes(text) == ["missing_board_id"]


@pytest.mark.parametrize("identifier", ["Board", "_part", "1part", "part.name", "part name"])
def test_part_and_net_identifiers_follow_the_grammar(identifier: str) -> None:
    part_text = MINIMAL_DOCUMENT.replace(
        "parts: {}",
        f"parts:\n  {json.dumps(identifier)}: {{kind: x, pins: {{}}}}",
    )
    net_text = MINIMAL_DOCUMENT.replace(
        "nets: {}",
        f"nets:\n  {json.dumps(identifier)}: {{role: signal, endpoints: [board.A, board.B]}}",
    )

    assert "invalid_part_identifier" in _codes(part_text)
    assert "invalid_net_identifier" in _codes(net_text)


def test_board_is_a_reserved_part_identifier() -> None:
    text = MINIMAL_DOCUMENT.replace(
        "parts: {}",
        "parts:\n  board:\n    kind: connector\n    pins: {}",
    )

    assert _codes(text) == ["reserved_part_identifier"]


@pytest.mark.parametrize(
    ("pin_yaml", "expected_code"),
    [
        ("1", "invalid_pin_name"),
        ("''", "invalid_pin_name"),
        ("'bad.pin'", "invalid_pin_name"),
    ],
)
def test_pin_names_must_be_quoted_nonempty_strings_without_periods(
    pin_yaml: str,
    expected_code: str,
) -> None:
    text = MINIMAL_DOCUMENT.replace(
        "parts: {}",
        f"parts:\n  connector:\n    kind: connector\n    pins:\n      {pin_yaml}: {{}}",
    )

    assert expected_code in _codes(text)


def test_all_part_fields_are_strict_and_unknown_fields_fail() -> None:
    text = MINIMAL_DOCUMENT.replace(
        "parts: {}",
        """\
parts:
  item:
    kind: true
    pins: {}
    mystery: value
""".rstrip(),
    )

    report = validate_hardware_text(text)
    assert {"invalid_field", "unknown_field"} <= {item.code for item in report.errors}


@pytest.mark.parametrize(
    "role",
    [
        "power_in",
        "power_out",
        "ground",
        "passive",
        "digital_in",
        "digital_out",
        "digital_io",
        "analog_in",
        "analog_out",
        "i2c_sda",
        "i2c_scl",
        "spi_clock",
        "spi_mosi",
        "spi_miso",
        "uart_tx",
        "uart_rx",
        "chip_select",
        "interrupt",
        "other",
    ],
)
def test_all_pin_roles_are_supported(role: str) -> None:
    text = MINIMAL_DOCUMENT.replace(
        "parts: {}",
        f"parts:\n  item:\n    kind: other\n    pins:\n      P: {{role: {role}}}",
    )
    report = validate_hardware_text(text)

    assert report.ok
    assert [item.code for item in report.warnings] == ["unconnected_part_pin"]


def test_invalid_pin_role_is_an_error() -> None:
    text = MINIMAL_DOCUMENT.replace(
        "parts: {}",
        "parts:\n  item:\n    kind: other\n    pins:\n      P: {role: magic}",
    )

    assert "invalid_pin_role" in _codes(text)


@pytest.mark.parametrize("address", ["-1", "0x80", "true", "60.0", "'0x3c'"])
def test_i2c_address_is_a_strict_7_bit_integer(address: str) -> None:
    text = MINIMAL_DOCUMENT.replace(
        "parts: {}",
        f"""\
parts:
  item:
    kind: sensor
    pins:
      SDA: {{role: i2c_sda}}
      SCL: {{role: i2c_scl}}
    interfaces:
      - kind: i2c
        bus: default
        address: {address}
""".rstrip(),
    )

    assert "invalid_i2c_address" in _codes(text)


@pytest.mark.parametrize(
    "replacement",
    [
        "kind: spi",
        "kind: i2c\n        bus: alternate",
        "kind: i2c\n        bus: default\n        speed: 100000",
    ],
)
def test_only_the_exact_default_i2c_interface_shape_is_supported(replacement: str) -> None:
    text = MINIMAL_DOCUMENT.replace(
        "parts: {}",
        f"""\
parts:
  item:
    kind: sensor
    pins: {{}}
    interfaces:
      - {replacement}
        address: 0x3c
""".rstrip(),
    )

    assert {"invalid_interface", "unknown_field"} & set(_codes(text))


@pytest.mark.parametrize("address", ["0x00", "0x07", "0x78", "0x7f"])
def test_reserved_i2c_addresses_warn(address: str) -> None:
    text = MINIMAL_DOCUMENT.replace(
        "parts: {}",
        f"""\
parts:
  item:
    kind: sensor
    pins:
      SDA: {{role: i2c_sda}}
      SCL: {{role: i2c_scl}}
    interfaces:
      - kind: i2c
        bus: default
        address: {address}
""".rstrip(),
    )
    report = validate_hardware_text(text)

    assert report.ok
    assert "reserved_i2c_address" in {item.code for item in report.warnings}


def test_i2c_interface_warns_for_missing_sda_and_scl_roles() -> None:
    text = MINIMAL_DOCUMENT.replace(
        "parts: {}",
        """\
parts:
  item:
    kind: sensor
    pins: {}
    interfaces:
      - kind: i2c
        bus: default
        address: 0x3c
""".rstrip(),
    )
    report = validate_hardware_text(text)
    diagnostic = next(item for item in report.warnings if item.code == "i2c_missing_pin_roles")

    assert diagnostic.details["missing_roles"] == ["i2c_scl", "i2c_sda"]


def test_duplicate_i2c_addresses_on_default_bus_are_errors() -> None:
    text = """\
schema_version: 1
board: {id: test_board}
parts:
  alpha:
    kind: sensor
    pins: {SDA: {role: i2c_sda}, SCL: {role: i2c_scl}}
    interfaces: [{kind: i2c, bus: default, address: 0x3c}]
  beta:
    kind: sensor
    pins: {SDA: {role: i2c_sda}, SCL: {role: i2c_scl}}
    interfaces: [{kind: i2c, bus: default, address: 0x3c}]
nets:
  sda: {role: signal, endpoints: [board.SDA, alpha.SDA, beta.SDA]}
  scl: {role: signal, endpoints: [board.SCL, alpha.SCL, beta.SCL]}
"""
    report = validate_hardware_text(text)

    duplicate_errors = [item for item in report.errors if item.code == "duplicate_i2c_address"]
    assert len(duplicate_errors) == _DUPLICATE_PART_COUNT
    assert duplicate_errors[0].details["parts"] == ["alpha", "beta"]


def test_repeated_i2c_address_on_one_part_is_not_a_multiple_part_collision() -> None:
    text = """\
schema_version: 1
board: {id: test_board}
parts:
  item:
    kind: sensor
    pins: {SDA: {role: i2c_sda}, SCL: {role: i2c_scl}}
    interfaces:
      - {kind: i2c, bus: default, address: 0x3c}
      - {kind: i2c, bus: default, address: 0x3c}
nets:
  sda: {role: signal, endpoints: [board.SDA, item.SDA]}
  scl: {role: signal, endpoints: [board.SCL, item.SCL]}
"""

    assert "duplicate_i2c_address" not in _codes(text)


def test_requirements_cross_reference_normalizes_name_and_ignores_constraints() -> None:
    assert normalize_library_name(" Adafruit-SSD1306 ") == "adafruit_ssd1306"
    assert requirement_library_names(
        "# generated\nAdafruit-SSD1306==2.1.0\nadafruit_bus_device~=5.0; python_version > '3'\n"
    ) == frozenset({"adafruit_ssd1306", "adafruit_bus_device"})
    report = validate_hardware_text(COMPLETE_DOCUMENT, "Adafruit-SSD1306>=2\n")

    assert report.ok


def test_requirements_cross_reference_handles_not_equal_constraints() -> None:
    assert requirement_library_names("adafruit_requests!=4.0\n") == frozenset({"adafruit_requests"})


def test_library_missing_from_requirements_is_an_error_at_the_library() -> None:
    report = validate_hardware_text(COMPLETE_DOCUMENT, "# empty\n")
    diagnostic = next(item for item in report.errors if item.code == "library_not_in_requirements")

    assert diagnostic.path == "$.parts.oled.libraries[0]"
    assert diagnostic.details == {"library": "adafruit_ssd1306"}


@pytest.mark.parametrize("role", ["power", "ground", "signal", "analog", "other"])
def test_all_net_roles_are_supported(role: str) -> None:
    voltage = "\n    voltage: 3" if role == "power" else ""
    text = MINIMAL_DOCUMENT.replace(
        "nets: {}",
        f"nets:\n  test:\n    role: {role}{voltage}\n    endpoints: [board.A, board.B]",
    )

    assert validate_hardware_text(text).ok


def test_invalid_net_role_and_voltage_are_errors() -> None:
    invalid_role = MINIMAL_DOCUMENT.replace(
        "nets: {}",
        "nets:\n  bad: {role: mystery, endpoints: [board.A, board.B]}",
    )
    non_power_voltage = MINIMAL_DOCUMENT.replace(
        "nets: {}",
        "nets:\n  bad: {role: signal, voltage: 3.3, endpoints: [board.A, board.B]}",
    )
    invalid_voltage = MINIMAL_DOCUMENT.replace(
        "nets: {}",
        "nets:\n  bad: {role: power, voltage: 0, endpoints: [board.A, board.B]}",
    )

    assert "invalid_net_role" in _codes(invalid_role)
    assert "voltage_on_non_power_net" in _codes(non_power_voltage)
    assert "invalid_voltage" in _codes(invalid_voltage)


@pytest.mark.parametrize("voltage", [".inf", "-.inf", ".nan", "true", "'3.3'"])
def test_voltage_must_be_a_finite_number(voltage: str) -> None:
    text = MINIMAL_DOCUMENT.replace(
        "nets: {}",
        f"nets:\n  bad: {{role: power, voltage: {voltage}, endpoints: [board.A, board.B]}}",
    )

    assert "invalid_voltage" in _codes(text)


def test_net_requires_two_distinct_endpoints_and_detects_local_duplicates() -> None:
    text = MINIMAL_DOCUMENT.replace(
        "nets: {}",
        "nets:\n  bad: {role: signal, endpoints: [board.D5, board.D5]}",
    )

    assert set(_codes(text)) == {"duplicate_endpoint_in_net", "net_too_few_endpoints"}


def test_endpoint_syntax_and_references_are_checked() -> None:
    text = """\
schema_version: 1
board: {id: test_board}
parts:
  known:
    kind: connector
    pins: {P: {}}
nets:
  bad_syntax: {role: signal, endpoints: [no_period, board.A]}
  unknown_part: {role: signal, endpoints: [missing.P, board.B]}
  unknown_pin: {role: signal, endpoints: [known.Q, board.C]}
"""
    report = validate_hardware_text(text)

    assert {"invalid_endpoint", "unknown_part", "undeclared_part_pin"} <= {
        item.code for item in report.errors
    }
    assert "unconnected_part_pin" in {item.code for item in report.warnings}


def test_same_endpoint_cannot_be_assigned_to_two_nets() -> None:
    text = MINIMAL_DOCUMENT.replace(
        "nets: {}",
        """\
nets:
  first: {role: signal, endpoints: [board.D5, board.D6]}
  second: {role: signal, endpoints: [board.D5, board.D7]}
""".rstrip(),
    )
    report = validate_hardware_text(text)
    diagnostic = next(
        item for item in report.errors if item.code == "duplicate_endpoint_across_nets"
    )

    assert diagnostic.path == "$.nets.second.endpoints[0]"
    assert diagnostic.details["first_path"] == "$.nets.first.endpoints[0]"


def test_connection_consistency_warnings_and_unconnected_pin_warning() -> None:
    text = """\
schema_version: 1
board: {id: test_board}
parts:
  item:
    kind: breakout
    pins:
      GND: {role: ground}
      VIN: {role: power_in}
      UNUSED: {role: passive}
nets:
  wrong_ground: {role: signal, endpoints: [board.D1, item.GND]}
  wrong_power: {role: signal, endpoints: [board.D2, item.VIN]}
"""
    warning_codes = {item.code for item in validate_hardware_text(text).warnings}

    assert warning_codes == {
        "ground_pin_on_non_ground_net",
        "power_pin_on_signal_net",
        "unconnected_part_pin",
    }


def test_diagnostic_order_is_stable_across_mapping_order() -> None:
    first = """\
schema_version: 1
board: {id: test_board}
parts:
  zed: {kind: connector, pins: {P: {}}}
  alpha: {kind: connector, pins: {P: {}}}
nets: {}
"""
    second = """\
nets: {}
parts:
  alpha: {pins: {P: {}}, kind: connector}
  zed: {pins: {P: {}}, kind: connector}
board: {id: test_board}
schema_version: 1
"""

    first_records = [
        item.model_dump(mode="json") for item in validate_hardware_text(first).diagnostics
    ]
    second_records = [
        item.model_dump(mode="json") for item in validate_hardware_text(second).diagnostics
    ]
    assert first_records == second_records


class _RecordingConnectedHook:
    calls: list[str]

    def __init__(self) -> None:
        self.calls = []

    def validate_connected(self, document: HardwareDocument) -> tuple[Diagnostic, ...]:
        self.calls.append(document.board.id)
        return (
            Diagnostic(
                severity=DiagnosticSeverity.WARNING,
                code="connected_test_warning",
                message="Connected hook ran.",
                details={"board_id": document.board.id},
            ),
        )


def test_connected_hook_runs_only_after_offline_validation_has_no_errors() -> None:
    hook = _RecordingConnectedHook()
    valid = validate_hardware_text(MINIMAL_DOCUMENT, connected=hook)
    invalid = validate_hardware_text(
        MINIMAL_DOCUMENT.replace("id: test_board", "id: ''"),
        connected=hook,
    )

    assert hook.calls == ["test_board"]
    assert "connected_test_warning" in {item.code for item in valid.warnings}
    assert "connected_test_warning" not in {item.code for item in invalid.diagnostics}


def test_validation_report_has_machine_readable_serialization() -> None:
    report = validate_hardware_text(MINIMAL_DOCUMENT.replace("id: test_board", "id: ''"))
    serialized = report.model_dump(mode="json")

    assert serialized["ok"] is False
    assert "document" not in serialized
    diagnostics = serialized["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics[0]["severity"] == "error"
    assert diagnostics[0]["code"] == "missing_board_id"


def test_generated_schema_describes_strict_version_1_shape(tmp_path: Path) -> None:
    schema = hardware_schema()

    assert schema["$id"] == HARDWARE_SCHEMA_ID
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema_version"] == {
        "const": 1,
        "title": "Schema Version",
        "type": "integer",
    }
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"schema_version", "board", "parts", "nets"}
    assert "^[a-z][a-z0-9_-]*$" in schema["properties"]["parts"]["patternProperties"]
    assert schema["properties"]["parts"]["additionalProperties"] is False
    assert schema["properties"]["nets"]["additionalProperties"] is False
    pin_schema = schema["$defs"]["PartDefinition"]["properties"]["pins"]
    assert "^[^.]+$" in pin_schema["patternProperties"]
    assert pin_schema["additionalProperties"] is False

    schema_text = hardware_schema_json()
    assert json.loads(schema_text) == schema
    output = write_hardware_schema(tmp_path / "hardware-v1.json")
    assert output == (tmp_path / "hardware-v1.json").resolve()
    assert output.read_text(encoding="utf-8") == schema_text


def test_packaged_json_schema_matches_generated_schema() -> None:
    assert packaged_hardware_schema() == hardware_schema()


def test_validate_file_defaults_to_sibling_requirements(tmp_path: Path) -> None:
    manifest = tmp_path / "hardware.yaml"
    manifest.write_text(COMPLETE_DOCUMENT, encoding="utf-8", newline="\n")
    (tmp_path / "requirements.txt").write_text(
        "adafruit_ssd1306==2.0\n",
        encoding="utf-8",
        newline="\n",
    )

    report = validate_hardware_file(manifest)
    assert report.ok


def test_validate_file_reports_missing_file_and_invalid_utf8(tmp_path: Path) -> None:
    missing = validate_hardware_file(tmp_path / "missing.yaml")
    invalid_path = tmp_path / "hardware.yaml"
    invalid_path.write_bytes(b"\xff")
    invalid = validate_hardware_file(invalid_path)

    assert [item.code for item in missing.errors] == ["hardware_file_not_found"]
    assert [item.code for item in invalid.errors] == ["invalid_utf8"]
