"""Acceptance-gated bounded execution through circremote's documented CLI."""

from __future__ import annotations

import json
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, cast

from patchcord.adapters.process import ProcessResult, environment_executable, run_process
from patchcord.errors import BoardExecutionError, DependencyError

_ACCEPTED_VERSIONS: frozenset[str] = frozenset()
_KNOWN_REJECTED_VERSIONS: dict[str, str] = {"0.12.0": "fails_isolated_configuration_gate"}
_TRACEBACK = "Traceback (most recent call last):"
Runner = Callable[..., ProcessResult]


@dataclass(frozen=True, slots=True)
class ExecutionCapability:
    """Static acceptance checks that can run without touching hardware."""

    available: bool
    backend: str
    backend_version: str | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Normalized board output and framed structured payload."""

    output: str
    payload: dict[str, Any]
    backend_version: str
    stderr: str


def capability() -> ExecutionCapability:
    """Report whether an exact circremote release passed the acceptance gate."""

    executable = environment_executable("circremote")
    try:
        backend_version = version("circremote")
    except PackageNotFoundError:
        backend_version = None
    if executable is None or backend_version is None:
        return ExecutionCapability(False, "circremote", backend_version, "not_installed")
    rejected_reason = _KNOWN_REJECTED_VERSIONS.get(backend_version)
    if rejected_reason is not None:
        return ExecutionCapability(
            False,
            "circremote",
            backend_version,
            rejected_reason,
        )
    if backend_version not in _ACCEPTED_VERSIONS:
        return ExecutionCapability(
            False,
            "circremote",
            backend_version,
            "acceptance_gate_not_run",
        )
    return ExecutionCapability(True, "circremote", backend_version, None)


def _require_backend() -> tuple[str, str]:
    state = capability()
    executable = environment_executable("circremote")
    if not state.available or executable is None or state.backend_version is None:
        raise DependencyError(
            "execution_backend_unavailable",
            "Bounded board execution requires an accepted circremote backend.",
            details={"reason": state.reason, "backend_version": state.backend_version},
            diagnostics={
                "backend": state.backend,
                "backend_version": state.backend_version,
            },
        )
    return executable, state.backend_version


def _with_backend_diagnostics(
    error: DependencyError,
    backend_version: str,
) -> DependencyError:
    diagnostics = dict(error.diagnostics)
    diagnostics.update(
        {
            "backend": "circremote",
            "backend_version": backend_version,
        }
    )
    return DependencyError(
        error.code,
        error.message,
        details=error.details,
        diagnostics=diagnostics,
    )


def _isolated_config(path: Path) -> None:
    config: dict[str, Any] = {
        "devices": [],
        "command_aliases": [],
        "search_paths": [],
        "variable_defaults": {},
    }
    path.write_text(json.dumps(config), encoding="utf-8", newline="\n")
    path.chmod(0o600)


def _parse_frame(stdout: str, sentinel: str, *, expected_kind: str) -> tuple[str, dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    output_lines: list[str] = []
    for line in stdout.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if not stripped.startswith(sentinel):
            output_lines.append(line)
            continue
        raw_payload = stripped.removeprefix(sentinel)
        try:
            decoded: object = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise BoardExecutionError(
                "invalid_execution_frame",
                "The board returned a malformed Patchcord result frame.",
            ) from exc
        if not isinstance(decoded, dict):
            raise BoardExecutionError(
                "invalid_execution_frame",
                "The board result frame is not a JSON object.",
            )
        frames.append(cast("dict[str, Any]", decoded))
    if len(frames) != 1:
        raise BoardExecutionError(
            "execution_frame_missing" if not frames else "ambiguous_execution_frame",
            "The bounded execution backend did not return exactly one Patchcord result frame.",
            details={"frames": len(frames)},
        )
    payload = frames[0]
    if payload.get("schema_version") != 1 or payload.get("kind") != expected_kind:
        raise BoardExecutionError(
            "invalid_execution_frame",
            "The board returned an unexpected Patchcord result schema.",
            details={
                "schema_version": payload.get("schema_version"),
                "kind": payload.get("kind"),
            },
        )
    return "".join(output_lines), payload


def run_script(
    port: str,
    source: str,
    *,
    expected_kind: str = "execution",
    timeout: float = 30,
    reset: bool = True,
    source_emits_frame: bool = False,
    runner: Runner = run_process,
) -> ExecutionResult:
    """Run one temporary local script through an isolated circremote process."""

    executable, backend_version = _require_backend()
    nonce = uuid.uuid4().hex
    sentinel = "__PATCHCORD_RESULT_V1__"
    if not source_emits_frame:
        sentinel = f"__PATCHCORD_RESULT_V1__:{nonce}:"
        source = (
            f"{source.rstrip()}\n"
            "import json as __patchcord_json\n"
            f"print({sentinel!r} + __patchcord_json.dumps("
            "{'schema_version': 1, 'kind': 'execution', 'completed': True}))\n"
        )
    with tempfile.TemporaryDirectory(prefix="patchcord-exec-") as directory:
        temporary = Path(directory)
        config_path = temporary / "circremote.json"
        script_path = temporary / "script.py"
        _isolated_config(config_path)
        script_path.write_text(source, encoding="utf-8", newline="\n")
        argv = [
            executable,
            "--quiet",
            "--skip-circup",
            "--config",
            str(config_path),
            "--timeout",
            str(max(1, int(timeout))),
        ]
        if reset:
            argv.append("--double-exit")
        argv.extend([port, str(script_path)])
        try:
            result = runner(
                argv,
                timeout=timeout + 15,
                cwd=temporary,
                env={"PYTHONIOENCODING": "utf-8"},
            )
        except DependencyError as error:
            raise _with_backend_diagnostics(error, backend_version) from error
    diagnostics = {
        "backend": "circremote",
        "backend_version": backend_version,
        "returncode": result.returncode,
    }
    if result.returncode != 0:
        raise BoardExecutionError(
            "board_execution_failed",
            "The board did not complete the bounded execution.",
            details={"returncode": result.returncode},
            diagnostics=diagnostics,
        )
    if _TRACEBACK in result.stdout:
        raise BoardExecutionError(
            "board_traceback",
            "The board reported an uncaught traceback.",
            diagnostics=diagnostics,
        )
    output, payload = _parse_frame(result.stdout, sentinel, expected_kind=expected_kind)
    return ExecutionResult(output, payload, backend_version, result.stderr)


def run_file(
    port: str,
    path: Path,
    *,
    timeout: float = 30,
    reset: bool = True,
    runner: Runner = run_process,
) -> ExecutionResult:
    """Read and run an explicitly supplied local UTF-8 Python file."""

    try:
        source = path.resolve().read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BoardExecutionError(
            "repl_file_unreadable",
            "The selected REPL script is not a readable UTF-8 file.",
            details={"path": str(path)},
        ) from exc
    return run_script(port, source, timeout=timeout, reset=reset, runner=runner)
