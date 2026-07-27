"""Typed public records shared by Patchcord commands."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ErrorRecord(BaseModel):
    """Stable machine-readable error details."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class TargetInfo(BaseModel):
    """The explicit board target selected for an operation."""

    model_config = ConfigDict(extra="forbid")

    board_id: str | None = None
    board_name: str | None = None
    circuitpython_version: str | None = None
    mount: str | None = None
    port: str | None = None
    serial_number: str | None = None


class ResultEnvelope(BaseModel):
    """Versioned result contract emitted by bounded commands."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    command: str
    ok: bool
    target: TargetInfo = Field(default_factory=TargetInfo)
    result: dict[str, Any] = Field(default_factory=dict)
    errors: list[ErrorRecord] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class DiagnosticRecord(BaseModel):
    """A source-oriented validation error or warning."""

    model_config = ConfigDict(extra="forbid")

    severity: Literal["error", "warning"]
    code: str
    message: str
    path: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
