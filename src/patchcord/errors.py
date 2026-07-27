"""Stable Patchcord failures and process exit codes."""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class ExitCode(IntEnum):
    """Process exit codes grouped by the failing Patchcord boundary."""

    OK = 0
    INTERNAL = 1
    USAGE = 2
    DISCOVERY = 10
    VALIDATION = 11
    TRANSPORT = 12
    DEPLOYMENT = 13
    BOARD_EXECUTION = 14
    DEPENDENCY = 15
    PROJECT = 16
    LOCKED = 17


class PatchcordError(Exception):
    """An expected failure with a stable machine-facing code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        exit_code: ExitCode = ExitCode.INTERNAL,
        details: dict[str, Any] | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.details = details or {}
        self.diagnostics = diagnostics or {}


class ProjectError(PatchcordError):
    """A project is absent, incomplete, or unsafe to use."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, exit_code=ExitCode.PROJECT, details=details)


class DiscoveryError(PatchcordError):
    """A board target could not be selected unambiguously."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, exit_code=ExitCode.DISCOVERY, details=details)


class TransportError(PatchcordError):
    """A serial or filesystem transport failed."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, exit_code=ExitCode.TRANSPORT, details=details)


class DeploymentError(PatchcordError):
    """A deployment plan or filesystem copy failed safely."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, exit_code=ExitCode.DEPLOYMENT, details=details)


class BoardExecutionError(PatchcordError):
    """Bounded code execution or a structured board probe failed."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            code,
            message,
            exit_code=ExitCode.BOARD_EXECUTION,
            details=details,
            diagnostics=diagnostics,
        )


class DependencyError(PatchcordError):
    """A required upstream adapter is missing or incompatible."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            code,
            message,
            exit_code=ExitCode.DEPENDENCY,
            details=details,
            diagnostics=diagnostics,
        )
