"""Credential-conscious host-side operation evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, cast

from patchcord.errors import ExitCode, PatchcordError
from patchcord.models import TargetInfo
from patchcord.project import Project

_SENSITIVE_KEY_PARTS = ("password", "credential", "secret", "token", "settings")


def _sanitize(value: object, *, key: str = "") -> object:
    normalized_key = key.casefold()
    if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
        return "[redacted]"
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        return {
            str(child_key): _sanitize(child, key=str(child_key))
            for child_key, child in mapping.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sequence = cast("Sequence[object]", value)
        return [_sanitize(child, key=key) for child in sequence]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def record_operation(
    project: Project,
    *,
    command: str,
    target: TargetInfo,
    ok: bool,
    result: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> None:
    """Append a bounded metadata record without command lines or board stdout."""

    record = {
        "schema_version": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "command": command,
        "ok": ok,
        "target": target.model_dump(mode="json"),
        "result": _sanitize(result or {}),
        "diagnostics": _sanitize(diagnostics or {}),
    }
    try:
        project.ensure_state()
        with project.operations_log.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True))
            handle.write("\n")
    except OSError as exc:
        raise PatchcordError(
            "operation_log_write_failed",
            "Patchcord could not append the operation log.",
            exit_code=ExitCode.PROJECT,
            details={"path": str(project.operations_log)},
        ) from exc
