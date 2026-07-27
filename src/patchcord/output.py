"""Human and machine output rendering."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from typing import Any

from rich.console import Console

from patchcord.errors import PatchcordError
from patchcord.models import ErrorRecord, ResultEnvelope, TargetInfo

console = Console(stderr=False)
error_console = Console(stderr=True)


def emit_result(
    command: str,
    result: Mapping[str, Any],
    *,
    json_output: bool,
    ok: bool = True,
    errors: list[ErrorRecord] | None = None,
    target: TargetInfo | None = None,
    diagnostics: dict[str, Any] | None = None,
    human: Callable[[Console], None] | None = None,
) -> None:
    """Emit one successful command result."""

    envelope = ResultEnvelope(
        command=command,
        ok=ok,
        target=target or TargetInfo(),
        result=dict(result),
        errors=errors or [],
        diagnostics=diagnostics or {},
    )
    if json_output:
        sys.stdout.write(
            json.dumps(envelope.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
            + "\n"
        )
    elif human is not None:
        human(console)


def emit_error(
    command: str,
    error: PatchcordError,
    *,
    json_output: bool,
    target: TargetInfo | None = None,
) -> None:
    """Emit an expected failure without leaking raw upstream output."""

    if json_output:
        envelope = ResultEnvelope(
            command=command,
            ok=False,
            target=target or TargetInfo(),
            errors=[
                ErrorRecord(code=error.code, message=error.message, details=error.details),
            ],
            diagnostics=error.diagnostics,
        )
        sys.stdout.write(
            json.dumps(envelope.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
            + "\n"
        )
    else:
        error_console.print(f"[bold red]Error:[/bold red] {error.message}")
        if error.details:
            for key, value in error.details.items():
                error_console.print(f"  {key}: {value}")
