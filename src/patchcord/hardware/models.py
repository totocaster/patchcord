"""Typed models for the version 1 Patchcord hardware manifest."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
)
from pydantic.json_schema import SkipJsonSchema

IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_-]*$"
BUNDLE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
_IDENTIFIER_RE = re.compile(IDENTIFIER_PATTERN)


def _require_schema_version(value: object) -> object:
    """Keep ``True`` and ``1.0`` from satisfying ``Literal[1]``."""

    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("schema_version must be the integer 1")
    return value


def _reject_explicit_null(value: object) -> object:
    if value is None:
        raise ValueError("the field may be omitted but cannot be null")
    return value


SchemaVersion = Annotated[Literal[1], BeforeValidator(_require_schema_version)]
Identifier = Annotated[StrictStr, Field(pattern=IDENTIFIER_PATTERN)]
PinName = Annotated[StrictStr, Field(min_length=1, pattern=r"^[^.]+$")]
NonEmptyString = Annotated[StrictStr, Field(min_length=1)]
BundleName = Annotated[StrictStr, Field(pattern=BUNDLE_NAME_PATTERN)]
I2CAddress = Annotated[StrictInt, Field(ge=0x00, le=0x7F)]
PositiveVoltage = Annotated[StrictFloat, Field(gt=0, allow_inf_nan=False)]


class PinRole(StrEnum):
    """Electrical and protocol roles supported for part pins."""

    POWER_IN = "power_in"
    POWER_OUT = "power_out"
    GROUND = "ground"
    PASSIVE = "passive"
    DIGITAL_IN = "digital_in"
    DIGITAL_OUT = "digital_out"
    DIGITAL_IO = "digital_io"
    ANALOG_IN = "analog_in"
    ANALOG_OUT = "analog_out"
    I2C_SDA = "i2c_sda"
    I2C_SCL = "i2c_scl"
    SPI_CLOCK = "spi_clock"
    SPI_MOSI = "spi_mosi"
    SPI_MISO = "spi_miso"
    UART_TX = "uart_tx"
    UART_RX = "uart_rx"
    CHIP_SELECT = "chip_select"
    INTERRUPT = "interrupt"
    OTHER = "other"


class NetRole(StrEnum):
    """Logical net roles supported by hardware schema version 1."""

    POWER = "power"
    GROUND = "ground"
    SIGNAL = "signal"
    ANALOG = "analog"
    OTHER = "other"


class HardwareModel(BaseModel):
    """Common strictness shared by every public manifest model."""

    model_config = ConfigDict(extra="forbid", validate_default=True)


class BoardDefinition(HardwareModel):
    """The intended CircuitPython board model."""

    id: StrictStr


class PinDefinition(HardwareModel):
    """A documented pin on an external part."""

    role: Annotated[
        PinRole | SkipJsonSchema[None],
        BeforeValidator(_reject_explicit_null),
    ] = Field(default=None, validate_default=False)
    notes: Annotated[
        StrictStr | SkipJsonSchema[None],
        BeforeValidator(_reject_explicit_null),
    ] = Field(default=None, validate_default=False)


class I2CInterface(HardwareModel):
    """An expected device on CircuitPython's default I²C bus."""

    kind: Literal["i2c"]
    bus: Literal["default"]
    address: I2CAddress


class PartDefinition(HardwareModel):
    """A project-local external part."""

    kind: NonEmptyString
    model: Annotated[
        StrictStr | SkipJsonSchema[None],
        BeforeValidator(_reject_explicit_null),
    ] = Field(default=None, validate_default=False)
    value: Annotated[
        StrictStr | SkipJsonSchema[None],
        BeforeValidator(_reject_explicit_null),
    ] = Field(default=None, validate_default=False)
    pins: dict[PinName, PinDefinition]
    interfaces: list[I2CInterface] = Field(default_factory=list)
    libraries: list[BundleName] = Field(default_factory=list)
    notes: Annotated[
        StrictStr | SkipJsonSchema[None],
        BeforeValidator(_reject_explicit_null),
    ] = Field(default=None, validate_default=False)


class NetDefinition(HardwareModel):
    """A logical electrical connection between board and part pins."""

    role: NetRole
    endpoints: list[StrictStr]
    voltage: Annotated[
        PositiveVoltage | SkipJsonSchema[None],
        BeforeValidator(_reject_explicit_null),
    ] = Field(default=None, validate_default=False)
    notes: Annotated[
        StrictStr | SkipJsonSchema[None],
        BeforeValidator(_reject_explicit_null),
    ] = Field(default=None, validate_default=False)


class HardwareDocument(HardwareModel):
    """The complete, versioned ``hardware.yaml`` document."""

    schema_version: SchemaVersion
    board: BoardDefinition
    parts: dict[Identifier, PartDefinition]
    nets: dict[Identifier, NetDefinition]
    notes: Annotated[
        StrictStr | SkipJsonSchema[None],
        BeforeValidator(_reject_explicit_null),
    ] = Field(default=None, validate_default=False)


def is_identifier(value: str) -> bool:
    """Return whether *value* follows the public part/net identifier grammar."""

    return _IDENTIFIER_RE.fullmatch(value) is not None


__all__ = [
    "IDENTIFIER_PATTERN",
    "BUNDLE_NAME_PATTERN",
    "BoardDefinition",
    "BundleName",
    "HardwareDocument",
    "I2CAddress",
    "I2CInterface",
    "Identifier",
    "NetDefinition",
    "NetRole",
    "PartDefinition",
    "PinDefinition",
    "PinName",
    "PinRole",
    "PositiveVoltage",
    "SchemaVersion",
    "is_identifier",
]
