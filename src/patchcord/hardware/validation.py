"""Strict, deterministic validation for Patchcord ``hardware.yaml`` files."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, computed_field
from ruamel.yaml import YAML
from ruamel.yaml.composer import ComposerError
from ruamel.yaml.constructor import ConstructorError, DuplicateKeyError
from ruamel.yaml.error import MarkedYAMLError, YAMLError
from ruamel.yaml.parser import ParserError
from ruamel.yaml.scanner import ScannerError

from patchcord.hardware.models import HardwareDocument, NetRole, PinRole, is_identifier

_REQUIREMENT_SUFFIX_RE = re.compile(r"[!<>=~\[;@\s]")
_NORMALIZED_LIBRARY_SEPARATOR_RE = re.compile(r"[-_.]+")
_SIMPLE_PATH_COMPONENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_MINIMUM_NET_ENDPOINTS = 2
_MINIMUM_DUPLICATE_DECLARATIONS = 2
_I2C_LOW_RESERVED_MAX = 0x07
_I2C_HIGH_RESERVED_MIN = 0x78


class DiagnosticSeverity(StrEnum):
    """Machine-readable validation severity."""

    ERROR = "error"
    WARNING = "warning"


class Diagnostic(BaseModel):
    """A stable validation record suitable for human or JSON rendering."""

    model_config = ConfigDict(frozen=True)

    severity: DiagnosticSeverity
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    message: str
    path: str | None = None
    details: dict[str, JsonValue] = Field(default_factory=dict)


class HardwareValidationReport(BaseModel):
    """The validated document and all deterministic diagnostic records."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    document: HardwareDocument | None = Field(default=None, exclude=True)
    diagnostics: tuple[Diagnostic, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ok(self) -> bool:
        """Whether validation produced no errors."""

        return not any(item.severity is DiagnosticSeverity.ERROR for item in self.diagnostics)

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.severity is DiagnosticSeverity.ERROR)

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(
            item for item in self.diagnostics if item.severity is DiagnosticSeverity.WARNING
        )


@runtime_checkable
class ConnectedValidationHook(Protocol):
    """Narrow boundary implemented by a future connected-board validator."""

    def validate_connected(self, document: HardwareDocument) -> Sequence[Diagnostic]:
        """Return connected-board diagnostics without changing the document."""

        ...


def _diagnostic(
    severity: DiagnosticSeverity,
    code: str,
    message: str,
    path: str | None = None,
    **details: object,
) -> Diagnostic:
    return Diagnostic(
        severity=severity,
        code=code,
        message=message,
        path=path,
        details=cast("dict[str, JsonValue]", details),
    )


def _error(
    code: str,
    message: str,
    path: str | None = None,
    **details: object,
) -> Diagnostic:
    return _diagnostic(DiagnosticSeverity.ERROR, code, message, path, **details)


def _warning(
    code: str,
    message: str,
    path: str | None = None,
    **details: object,
) -> Diagnostic:
    return _diagnostic(DiagnosticSeverity.WARNING, code, message, path, **details)


def _stable_diagnostics(diagnostics: Iterable[Diagnostic]) -> tuple[Diagnostic, ...]:
    severity_order = {
        DiagnosticSeverity.ERROR: 0,
        DiagnosticSeverity.WARNING: 1,
    }
    return tuple(
        sorted(
            diagnostics,
            key=lambda item: (
                severity_order[item.severity],
                item.path or "",
                item.code,
                json.dumps(item.details, sort_keys=True, separators=(",", ":")),
                item.message,
            ),
        )
    )


def _path_component(component: object) -> str:
    if isinstance(component, int):
        return f"[{component}]"
    value = str(component)
    if value in {"[key]", "__key__"}:
        return ""
    if _SIMPLE_PATH_COMPONENT_RE.fullmatch(value):
        return f".{value}"
    return f"[{json.dumps(value)}]"


def _json_path(location: Sequence[object]) -> str:
    return "$" + "".join(_path_component(component) for component in location)


class _YamlMark(Protocol):
    line: int
    column: int


def _mark_details(exc: MarkedYAMLError) -> dict[str, object]:
    problem_mark = cast("_YamlMark | None", exc.problem_mark)
    context_mark = cast("_YamlMark | None", exc.context_mark)
    mark = problem_mark or context_mark
    if mark is None:
        return {}
    return {"line": mark.line + 1, "column": mark.column + 1}


def _yaml_error(exc: BaseException) -> Diagnostic:
    if isinstance(exc, DuplicateKeyError):
        return _error(
            "duplicate_mapping_key",
            "YAML mappings cannot contain duplicate keys.",
            None,
            **_mark_details(exc),
        )
    problem = str(cast(object, exc.problem) or "") if isinstance(exc, ConstructorError) else ""
    if isinstance(exc, ConstructorError) and "tag" in problem.lower():
        return _error(
            "custom_yaml_tag",
            "Custom YAML tags are not allowed.",
            None,
            **_mark_details(exc),
        )
    if isinstance(exc, ComposerError):
        code = (
            "multiple_yaml_documents" if "single document" in str(exc).lower() else "malformed_yaml"
        )
        return _error(
            code,
            "hardware.yaml must contain exactly one valid YAML document.",
            None,
            **_mark_details(exc),
        )
    if isinstance(exc, (ScannerError, ParserError, MarkedYAMLError)):
        return _error(
            "malformed_yaml",
            "hardware.yaml is not valid YAML.",
            None,
            **_mark_details(exc),
        )
    return _error("malformed_yaml", "hardware.yaml is not valid YAML.")


def _validation_error_code(  # noqa: PLR0912
    location: tuple[object, ...],
    error_type: str,
) -> str:
    location_strings = tuple(str(item) for item in location)
    first = location_strings[0] if location_strings else ""
    code = "invalid_field"
    is_key_error = location_strings[-1:] == ("[key]",)
    if first == "schema_version":
        code = "unsupported_schema_version"
    elif error_type == "missing":
        code = "missing_required_field"
    elif error_type == "extra_forbidden":
        code = "unknown_field"
    elif "pins" in location_strings and is_key_error:
        code = "invalid_pin_name"
    elif first == "parts" and is_key_error:
        code = "invalid_part_identifier"
    elif first == "nets" and is_key_error:
        code = "invalid_net_identifier"
    elif "interfaces" in location_strings:
        if location_strings[-1:] == ("address",):
            code = "invalid_i2c_address"
        else:
            code = "invalid_interface"
    elif location_strings[-1:] == ("role",):
        code = "invalid_net_role" if first == "nets" else "invalid_pin_role"
    elif location_strings[-1:] == ("voltage",):
        code = "invalid_voltage"
    elif "libraries" in location_strings:
        code = "invalid_library_name"
    elif location_strings[-1:] == ("endpoints",):
        code = "invalid_endpoints"
    return code


def _model_diagnostics(exc: ValidationError) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for item in exc.errors(include_url=False):
        location = tuple(item["loc"])
        error_type = str(item["type"])
        if location[-1:] == ("none",) and error_type == "none_required":
            continue
        if len(location) >= 2 and str(location[-2]) in {
            "model",
            "notes",
            "role",
            "value",
            "voltage",
        }:
            location = location[:-1]
        code = _validation_error_code(location, error_type)
        details: dict[str, object] = {"validation_type": error_type}
        diagnostics.append(
            _error(
                code,
                str(item["msg"]),
                _json_path(location),
                **details,
            )
        )
    return diagnostics


def parse_hardware_yaml(text: str) -> HardwareValidationReport:
    """Parse and structurally validate one UTF-8 YAML 1.2 document.

    The returned report contains a document only when YAML and Pydantic shape
    validation both succeed. Semantic checks are performed by
    :func:`validate_hardware_text`.
    """

    if "\r" in text:
        return HardwareValidationReport(
            diagnostics=(
                _error(
                    "invalid_line_endings",
                    "hardware.yaml must use LF line endings.",
                    "$",
                ),
            )
        )

    yaml = YAML(typ="safe")
    yaml.version = (1, 2)
    yaml.allow_duplicate_keys = False
    try:
        value: object = yaml.load(text)  # pyright: ignore[reportUnknownMemberType]
    except (YAMLError, AssertionError) as exc:
        return HardwareValidationReport(diagnostics=(_yaml_error(exc),))

    loaded_version = yaml.version
    if loaded_version != (1, 2):
        version = "unknown" if loaded_version is None else ".".join(map(str, loaded_version))
        return HardwareValidationReport(
            diagnostics=(
                _error(
                    "unsupported_yaml_version",
                    "hardware.yaml must use YAML 1.2.",
                    "$",
                    version=version,
                ),
            )
        )
    if not isinstance(value, Mapping):
        return HardwareValidationReport(
            diagnostics=(
                _error(
                    "root_not_mapping",
                    "The root of hardware.yaml must be a mapping.",
                    "$",
                ),
            )
        )

    try:
        document = HardwareDocument.model_validate(value)
    except ValidationError as exc:
        return HardwareValidationReport(diagnostics=_stable_diagnostics(_model_diagnostics(exc)))
    return HardwareValidationReport(document=document)


def normalize_library_name(name: str) -> str:
    """Normalize a circup bundle library name for cross-file comparison."""

    return _NORMALIZED_LIBRARY_SEPARATOR_RE.sub("_", name.strip().lower())


def requirement_library_names(requirements_text: str | None) -> frozenset[str]:
    """Extract normalized names from circup-style ``requirements.txt`` text."""

    if requirements_text is None:
        return frozenset()
    names: set[str] = set()
    for raw_line in requirements_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name = _REQUIREMENT_SUFFIX_RE.split(line, maxsplit=1)[0].strip()
        if name:
            names.add(normalize_library_name(name))
    return frozenset(names)


def _endpoint_parts(endpoint: str) -> tuple[str, str] | None:
    if endpoint.count(".") != 1:
        return None
    owner, pin = endpoint.split(".", 1)
    if not owner or not pin:
        return None
    if owner != "board" and not is_identifier(owner):
        return None
    return owner, pin


def _semantic_diagnostics(  # noqa: PLR0912, PLR0915
    document: HardwareDocument,
    requirements_text: str | None,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    if not document.board.id.strip():
        diagnostics.append(
            _error(
                "missing_board_id",
                "board.id must be filled before the project is complete.",
                "$.board.id",
            )
        )

    if "board" in document.parts:
        diagnostics.append(
            _error(
                "reserved_part_identifier",
                "'board' is reserved and cannot be used as a part identifier.",
                "$.parts.board",
                identifier="board",
            )
        )

    addresses: dict[tuple[str, int], list[tuple[str, int]]] = defaultdict(list)
    for part_id in sorted(document.parts):
        part = document.parts[part_id]
        roles = {pin.role for pin in part.pins.values()}
        for interface_index, interface in enumerate(part.interfaces):
            addresses[(interface.bus, interface.address)].append((part_id, interface_index))
            missing_roles = sorted(
                role.value for role in (PinRole.I2C_SDA, PinRole.I2C_SCL) if role not in roles
            )
            if missing_roles:
                diagnostics.append(
                    _warning(
                        "i2c_missing_pin_roles",
                        "An I²C interface should declare both SDA and SCL pin roles.",
                        f"$.parts.{part_id}.interfaces[{interface_index}]",
                        missing_roles=missing_roles,
                    )
                )
            if (
                interface.address <= _I2C_LOW_RESERVED_MAX
                or interface.address >= _I2C_HIGH_RESERVED_MIN
            ):
                diagnostics.append(
                    _warning(
                        "reserved_i2c_address",
                        f"I²C address 0x{interface.address:02x} is in a reserved range.",
                        f"$.parts.{part_id}.interfaces[{interface_index}].address",
                        address=interface.address,
                        address_hex=f"0x{interface.address:02x}",
                    )
                )

    for (bus, address), declarations in sorted(addresses.items()):
        parts = sorted({part_id for part_id, _ in declarations})
        if len(parts) < _MINIMUM_DUPLICATE_DECLARATIONS:
            continue
        for part_id, interface_index in sorted(declarations):
            diagnostics.append(
                _error(
                    "duplicate_i2c_address",
                    f"I²C address 0x{address:02x} is declared by multiple parts on {bus}.",
                    f"$.parts.{part_id}.interfaces[{interface_index}].address",
                    address=address,
                    address_hex=f"0x{address:02x}",
                    bus=bus,
                    parts=parts,
                )
            )

    endpoint_first_path: dict[str, str] = {}
    connected_part_pins: set[tuple[str, str]] = set()
    for net_id in sorted(document.nets):
        net = document.nets[net_id]
        if net.voltage is not None and net.role is not NetRole.POWER:
            diagnostics.append(
                _error(
                    "voltage_on_non_power_net",
                    "voltage is valid only on a power net.",
                    f"$.nets.{net_id}.voltage",
                    net_role=net.role.value,
                    voltage=net.voltage,
                )
            )

        distinct_endpoints = set(net.endpoints)
        if len(distinct_endpoints) < _MINIMUM_NET_ENDPOINTS:
            diagnostics.append(
                _error(
                    "net_too_few_endpoints",
                    "A net must contain at least two distinct endpoints.",
                    f"$.nets.{net_id}.endpoints",
                    distinct_endpoint_count=len(distinct_endpoints),
                )
            )

        seen_in_net: dict[str, str] = {}
        for endpoint_index, endpoint in enumerate(net.endpoints):
            endpoint_path = f"$.nets.{net_id}.endpoints[{endpoint_index}]"
            parsed = _endpoint_parts(endpoint)
            if parsed is None:
                diagnostics.append(
                    _error(
                        "invalid_endpoint",
                        f"Endpoint {endpoint!r} does not use owner.pin syntax.",
                        endpoint_path,
                        endpoint=endpoint,
                    )
                )
            else:
                owner, pin_name = parsed
                if owner != "board":
                    part = document.parts.get(owner)
                    if part is None:
                        diagnostics.append(
                            _error(
                                "unknown_part",
                                f"Endpoint {endpoint!r} references an unknown part.",
                                endpoint_path,
                                endpoint=endpoint,
                                part=owner,
                            )
                        )
                    elif pin_name not in part.pins:
                        diagnostics.append(
                            _error(
                                "undeclared_part_pin",
                                f"Endpoint {endpoint!r} references an undeclared part pin.",
                                endpoint_path,
                                endpoint=endpoint,
                                part=owner,
                                pin=pin_name,
                            )
                        )
                    else:
                        connected_part_pins.add((owner, pin_name))
                        pin_role = part.pins[pin_name].role
                        if pin_role is PinRole.GROUND and net.role is not NetRole.GROUND:
                            diagnostics.append(
                                _warning(
                                    "ground_pin_on_non_ground_net",
                                    "A ground-role pin is connected to a non-ground net.",
                                    endpoint_path,
                                    endpoint=endpoint,
                                    net=net_id,
                                    net_role=net.role.value,
                                )
                            )
                        if (
                            pin_role is PinRole.POWER_IN or pin_role is PinRole.POWER_OUT
                        ) and net.role is NetRole.SIGNAL:
                            diagnostics.append(
                                _warning(
                                    "power_pin_on_signal_net",
                                    "A power-role pin is connected to a signal net.",
                                    endpoint_path,
                                    endpoint=endpoint,
                                    net=net_id,
                                    pin_role=pin_role.value,
                                )
                            )

            if endpoint in seen_in_net:
                diagnostics.append(
                    _error(
                        "duplicate_endpoint_in_net",
                        f"Endpoint {endpoint!r} occurs more than once in this net.",
                        endpoint_path,
                        endpoint=endpoint,
                        first_path=seen_in_net[endpoint],
                        net=net_id,
                    )
                )
            else:
                seen_in_net[endpoint] = endpoint_path

            first_path = endpoint_first_path.get(endpoint)
            if first_path is not None and not first_path.startswith(f"$.nets.{net_id}."):
                diagnostics.append(
                    _error(
                        "duplicate_endpoint_across_nets",
                        f"Endpoint {endpoint!r} is assigned to more than one net.",
                        endpoint_path,
                        endpoint=endpoint,
                        first_path=first_path,
                        net=net_id,
                    )
                )
            else:
                endpoint_first_path.setdefault(endpoint, endpoint_path)

    diagnostics.extend(
        _warning(
            "unconnected_part_pin",
            f"Declared pin {part_id}.{pin_name} is not connected to a net.",
            f"$.parts.{part_id}.pins[{json.dumps(pin_name)}]",
            part=part_id,
            pin=pin_name,
        )
        for part_id in sorted(document.parts)
        for pin_name in sorted(document.parts[part_id].pins)
        if (part_id, pin_name) not in connected_part_pins
    )

    requirement_names = requirement_library_names(requirements_text)
    for part_id in sorted(document.parts):
        part = document.parts[part_id]
        for library_index, library in enumerate(part.libraries):
            if normalize_library_name(library) not in requirement_names:
                diagnostics.append(
                    _error(
                        "library_not_in_requirements",
                        f"Library {library!r} is not listed in requirements.txt.",
                        f"$.parts.{part_id}.libraries[{library_index}]",
                        library=library,
                    )
                )

    return diagnostics


def validate_hardware(
    document: HardwareDocument,
    requirements_text: str | None = None,
    *,
    connected: ConnectedValidationHook | None = None,
) -> HardwareValidationReport:
    """Run semantic and cross-file checks on a structurally valid document."""

    diagnostics = _semantic_diagnostics(document, requirements_text)
    if connected is not None and not any(
        item.severity is DiagnosticSeverity.ERROR for item in diagnostics
    ):
        diagnostics.extend(connected.validate_connected(document))
    return HardwareValidationReport(
        document=document,
        diagnostics=_stable_diagnostics(diagnostics),
    )


def validate_hardware_text(
    text: str,
    requirements_text: str | None = None,
    *,
    connected: ConnectedValidationHook | None = None,
) -> HardwareValidationReport:
    """Parse and fully validate ``hardware.yaml`` text without rewriting it."""

    parsed = parse_hardware_yaml(text)
    if parsed.document is None:
        return parsed
    return validate_hardware(parsed.document, requirements_text, connected=connected)


def validate_hardware_file(
    hardware_path: str | Path,
    requirements_path: str | Path | None = None,
    *,
    connected: ConnectedValidationHook | None = None,
) -> HardwareValidationReport:
    """Read and validate a project manifest plus its requirements cross-references."""

    manifest_path = Path(hardware_path)
    try:
        manifest_bytes = manifest_path.read_bytes()
    except FileNotFoundError:
        return HardwareValidationReport(
            diagnostics=(
                _error(
                    "hardware_file_not_found",
                    f"Hardware manifest not found: {manifest_path}",
                    details=str(manifest_path),
                ),
            )
        )
    except OSError:
        return HardwareValidationReport(
            diagnostics=(
                _error(
                    "hardware_file_unreadable",
                    f"Could not read hardware manifest: {manifest_path}",
                    details=str(manifest_path),
                ),
            )
        )

    try:
        text = manifest_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        return HardwareValidationReport(
            diagnostics=(
                _error(
                    "invalid_utf8",
                    "hardware.yaml must be valid UTF-8.",
                    "$",
                    byte_offset=exc.start,
                ),
            )
        )

    selected_requirements_path = (
        Path(requirements_path)
        if requirements_path is not None
        else manifest_path.with_name("requirements.txt")
    )
    requirements_text: str | None = None
    if selected_requirements_path.is_file():
        try:
            requirements_text = selected_requirements_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            parsed = parse_hardware_yaml(text)
            diagnostics = list(parsed.diagnostics)
            diagnostics.append(
                _error(
                    "requirements_invalid_utf8",
                    "requirements.txt must be valid UTF-8.",
                    details=str(selected_requirements_path),
                    byte_offset=exc.start,
                )
            )
            return HardwareValidationReport(
                document=parsed.document,
                diagnostics=_stable_diagnostics(diagnostics),
            )
        except OSError:
            parsed = parse_hardware_yaml(text)
            diagnostics = list(parsed.diagnostics)
            diagnostics.append(
                _error(
                    "requirements_file_unreadable",
                    f"Could not read requirements file: {selected_requirements_path}",
                    details=str(selected_requirements_path),
                )
            )
            return HardwareValidationReport(
                document=parsed.document,
                diagnostics=_stable_diagnostics(diagnostics),
            )

    return validate_hardware_text(
        text,
        requirements_text,
        connected=connected,
    )


__all__ = [
    "ConnectedValidationHook",
    "Diagnostic",
    "DiagnosticSeverity",
    "HardwareValidationReport",
    "normalize_library_name",
    "parse_hardware_yaml",
    "requirement_library_names",
    "validate_hardware",
    "validate_hardware_file",
    "validate_hardware_text",
]
