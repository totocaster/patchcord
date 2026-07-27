from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from patchcord.adapters import execution
from patchcord.adapters.execution import ExecutionCapability
from patchcord.adapters.process import ProcessResult
from patchcord.errors import BoardExecutionError, DependencyError


def _accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    def executable(_name: str) -> str:
        return "/venv/bin/circremote"

    monkeypatch.setattr(
        execution,
        "capability",
        lambda: ExecutionCapability(True, "circremote", "0.12.0", None),
    )
    monkeypatch.setattr(execution, "environment_executable", executable)


def test_released_backend_is_rejected_by_the_isolation_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def executable(_name: str) -> str:
        return "/bin/circremote"

    def backend_version(_name: str) -> str:
        return "0.12.0"

    monkeypatch.setattr(execution, "environment_executable", executable)
    monkeypatch.setattr(execution, "version", backend_version)

    state = execution.capability()

    assert state.available is False
    assert state.reason == "fails_isolated_configuration_gate"


def test_unreviewed_backend_version_does_not_auto_enable_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def executable(_name: str) -> str:
        return "/bin/circremote"

    def backend_version(_name: str) -> str:
        return "99.0.0"

    monkeypatch.setattr(execution, "environment_executable", executable)
    monkeypatch.setattr(execution, "version", backend_version)

    state = execution.capability()

    assert state.available is False
    assert state.reason == "acceptance_gate_not_run"


@pytest.mark.parametrize("backend_release", ["0.12.1rc1", "0.12.1+patched"])
def test_backend_acceptance_requires_the_exact_distribution_version(
    monkeypatch: pytest.MonkeyPatch,
    backend_release: str,
) -> None:
    def executable(_name: str) -> str:
        return "/bin/circremote"

    def backend_version(_name: str) -> str:
        return backend_release

    monkeypatch.setattr(execution, "environment_executable", executable)
    monkeypatch.setattr(execution, "version", backend_version)
    monkeypatch.setattr(execution, "_ACCEPTED_VERSIONS", frozenset({"0.12.1"}))

    state = execution.capability()

    assert state.available is False
    assert state.reason == "acceptance_gate_not_run"


def test_execution_is_isolated_and_parses_framed_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _accepted(monkeypatch)
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def runner(argv: Sequence[str], **kwargs: Any) -> ProcessResult:
        calls.append((list(argv), kwargs))
        script = Path(argv[-1]).read_text(encoding="utf-8")
        marker = next(
            line.split("print(", 1)[1].split(" + ", 1)[0].strip("'\"")
            for line in script.splitlines()
            if line.startswith("print('__PATCHCORD")
        )
        stdout = (
            "hello from board\n"
            f'{marker}{{"schema_version":1,"kind":"execution","completed":true}}\n'
        )
        config = Path(argv[argv.index("--config") + 1]).read_text(encoding="utf-8")
        assert '"search_paths": []' in config
        if os.name != "nt":
            assert Path(argv[argv.index("--config") + 1]).stat().st_mode & 0o077 == 0
        return ProcessResult(argv[0], 0, stdout, "")

    result = execution.run_script("/dev/test", "print('hello')", runner=runner)

    argv = calls[0][0]
    assert "--quiet" in argv
    assert "--skip-circup" in argv
    assert "--double-exit" in argv
    assert result.output == "hello from board\n"
    assert result.payload["completed"] is True


def test_unavailable_backend_error_includes_adapter_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def executable(_name: str) -> str:
        return "/venv/bin/circremote"

    monkeypatch.setattr(
        execution,
        "capability",
        lambda: ExecutionCapability(
            False,
            "circremote",
            "0.12.0",
            "fails_isolated_configuration_gate",
        ),
    )
    monkeypatch.setattr(execution, "environment_executable", executable)

    with pytest.raises(DependencyError) as raised:
        execution.run_script("COM7", "print('unused')")

    assert raised.value.code == "execution_backend_unavailable"
    assert raised.value.diagnostics == {
        "backend": "circremote",
        "backend_version": "0.12.0",
    }


def test_execution_process_error_keeps_adapter_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _accepted(monkeypatch)

    def runner(_argv: Sequence[str], **_kwargs: Any) -> ProcessResult:
        raise DependencyError(
            "dependency_timeout",
            "Upstream operation timed out.",
            details={"timeout_seconds": 45},
        )

    with pytest.raises(DependencyError) as raised:
        execution.run_script("COM7", "print('unused')", runner=runner)

    assert raised.value.code == "dependency_timeout"
    assert raised.value.details == {"timeout_seconds": 45}
    assert raised.value.diagnostics == {
        "backend": "circremote",
        "backend_version": "0.12.0",
    }


def test_no_reset_omits_double_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    _accepted(monkeypatch)

    def runner(argv: Sequence[str], **_kwargs: Any) -> ProcessResult:
        script = Path(argv[-1]).read_text(encoding="utf-8")
        marker = next(
            line.split("print(", 1)[1].split(" + ", 1)[0].strip("'\"")
            for line in script.splitlines()
            if line.startswith("print('__PATCHCORD")
        )
        assert "--double-exit" not in argv
        return ProcessResult(
            argv[0],
            0,
            f'{marker}{{"schema_version":1,"kind":"execution"}}\n',
            "",
        )

    execution.run_script("/dev/test", "x = 1", reset=False, runner=runner)


def test_missing_frame_fails_instead_of_guessing(monkeypatch: pytest.MonkeyPatch) -> None:
    _accepted(monkeypatch)

    def runner(argv: Sequence[str], **_kwargs: Any) -> ProcessResult:
        return ProcessResult(argv[0], 0, "human prose\n", "")

    with pytest.raises(BoardExecutionError) as raised:
        execution.run_script("/dev/test", "x = 1", runner=runner)

    assert raised.value.code == "execution_frame_missing"


def test_upstream_failure_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    _accepted(monkeypatch)

    def runner(argv: Sequence[str], **_kwargs: Any) -> ProcessResult:
        return ProcessResult(argv[0], 2, "", "unstable wording")

    with pytest.raises(BoardExecutionError) as raised:
        execution.run_script("/dev/test", "x = 1", runner=runner)

    assert raised.value.code == "board_execution_failed"
    assert raised.value.details == {"returncode": 2}
