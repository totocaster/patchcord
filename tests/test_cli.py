"""Command-level coverage for Patchcord's public CLI contract."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner, Result

import patchcord.cli as cli
from patchcord import __version__
from patchcord.adapters.circup import CircupResult
from patchcord.adapters.discovery import Drive, SelectedTarget, SerialPort
from patchcord.adapters.execution import ExecutionCapability, ExecutionResult
from patchcord.adapters.serial import SerialCapture
from patchcord.errors import DependencyError, DiscoveryError, ExitCode, TransportError
from patchcord.hardware.models import HardwareDocument
from patchcord.hardware.validation import Diagnostic, DiagnosticSeverity
from patchcord.project import Project, init_project

VALID_HARDWARE = """\
schema_version: 1
board:
  id: test_board
parts: {}
nets: {}
"""

_ENVELOPE_KEYS = {
    "schema_version",
    "command",
    "ok",
    "target",
    "result",
    "errors",
    "diagnostics",
}


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    initialized, _, _ = init_project(tmp_path)
    initialized.hardware_file.write_text(VALID_HARDWARE, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return initialized


@pytest.fixture
def target(project: Project) -> SelectedTarget:
    return _target(project)


def _target(
    project: Project,
    *,
    board_id: str | None = "test_board",
    with_drive: bool = True,
    with_serial: bool = True,
) -> SelectedTarget:
    drive: Drive | None = None
    if with_drive:
        mount = project.root / "CIRCUITPY"
        mount.mkdir(exist_ok=True)
        drive = Drive(
            mount=mount,
            board_id=board_id,
            board_name="Test Board",
            circuitpython_version="10.0.0",
            free_bytes=4096,
            total_bytes=8192,
        )
    serial: SerialPort | None = None
    if with_serial:
        serial = SerialPort(
            device="COM7",
            description="CircuitPython",
            serial_number="SERIAL-1",
            vid=0x239A,
            pid=0x0001,
            location="1-1",
            interface="CircuitPython CDC",
        )
    return SelectedTarget(drive=drive, serial=serial)


def _json_document(result: Result, *, exit_code: int = 0) -> dict[str, Any]:
    assert result.exit_code == exit_code, result.output
    assert result.stderr == ""
    assert result.stdout.endswith("\n")
    assert len(result.stdout.splitlines()) == 1
    payload = cast("dict[str, Any]", json.loads(result.stdout))
    assert set(payload) == _ENVELOPE_KEYS
    assert payload["schema_version"] == 1
    return payload


def _selection(target: SelectedTarget) -> Callable[..., SelectedTarget]:
    def select(**_kwargs: object) -> SelectedTarget:
        return target

    return select


def _mount(target: SelectedTarget) -> Path:
    assert target.drive is not None
    return target.drive.mount


def _capture(
    data: bytes,
    *,
    interrupted: bool = False,
    traceback: bool = False,
) -> SerialCapture:
    return SerialCapture(
        raw_output=data,
        started_at=1.0,
        ended_at=2.0,
        interrupted=interrupted,
        traceback_detected=traceback,
    )


def _fake_transport(
    events: list[tuple[str, object]],
    *,
    interrupt_output: bytes = b"interrupted\n",
    reset_output: bytes = b"ready\n",
    monitor_output: bytes = b"",
    reset_traceback: bool = False,
) -> type:
    class FakeSerialTransport:
        def __init__(self, port: str) -> None:
            self.port = port
            events.append(("open", port))

        def interrupt(
            self,
            *,
            on_data: Callable[[bytes], object] | None = None,
        ) -> SerialCapture:
            events.append(("interrupt", self.port))
            if on_data is not None and interrupt_output:
                on_data(interrupt_output)
            return _capture(interrupt_output, interrupted=True)

        def reset(
            self,
            *,
            capture: float,
            on_data: Callable[[bytes], object] | None = None,
        ) -> SerialCapture:
            events.append(("reset", capture))
            if on_data is not None and reset_output:
                on_data(reset_output)
            return _capture(reset_output, traceback=reset_traceback)

        def monitor(
            self,
            *,
            duration: float | None,
            on_data: Callable[[bytes], object] | None = None,
            retain_output: bool = True,
        ) -> SerialCapture:
            events.append(("monitor", duration))
            if on_data is not None and monitor_output:
                on_data(monitor_output)
            return _capture(monitor_output if retain_output else b"")

    return FakeSerialTransport


def test_root_help_lists_the_command_tree_and_version(
    runner: CliRunner,
) -> None:
    help_result = runner.invoke(cli.app, ["--help"])

    assert help_result.exit_code == 0
    assert "Safely coordinate CircuitPython projects" in help_result.stdout
    for command in (
        "init",
        "status",
        "doctor",
        "deploy",
        "monitor",
        "logs",
        "interrupt",
        "reset",
        "repl",
        "probe",
        "libs",
        "hardware",
    ):
        assert command in help_result.stdout

    version_result = runner.invoke(cli.app, ["--version"])
    assert version_result.exit_code == 0
    assert version_result.stdout == f"{__version__}\n"
    assert version_result.stderr == ""


@pytest.mark.parametrize(
    ("group", "summary"),
    [
        ("probe", "Run bounded, read-only board probes."),
        ("libs", "Manage CircuitPython libraries through circup."),
        ("hardware", "Validate the project's hardware description."),
    ],
)
def test_command_group_help(
    runner: CliRunner,
    group: str,
    summary: str,
) -> None:
    result = runner.invoke(cli.app, [group, "--help"])

    assert result.exit_code == 0
    assert summary in result.stdout


def test_status_json_is_exactly_one_success_document_and_forwards_overrides(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def select(**kwargs: object) -> SelectedTarget:
        calls.append(kwargs)
        return target

    monkeypatch.setattr(cli, "select_target", select)
    explicit_mount = project.root / "explicit-mount"

    result = runner.invoke(
        cli.app,
        [
            "--mount",
            str(explicit_mount),
            "--port",
            "COM99",
            "status",
            "--json",
        ],
    )

    payload = _json_document(result)
    assert payload["command"] == "status"
    assert payload["ok"] is True
    assert payload["errors"] == []
    assert payload["result"] == {
        "board_id": "test_board",
        "board_name": "Test Board",
        "circuitpython_version": "10.0.0",
        "mount": str(_mount(target)),
        "port": "COM7",
        "storage": {"free_bytes": 4096, "total_bytes": 8192},
    }
    assert calls == [
        {
            "mount": explicit_mount,
            "port": "COM99",
            "require_mount": True,
            "require_port": True,
        }
    ]


def test_json_error_is_exactly_one_document_with_a_stable_code(
    runner: CliRunner,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del project

    def fail_selection(**_kwargs: object) -> SelectedTarget:
        raise DiscoveryError(
            "ambiguous_mount",
            "Choose an explicit mount.",
            details={"mounts": ["/one", "/two"]},
        )

    monkeypatch.setattr(cli, "select_target", fail_selection)

    result = runner.invoke(cli.app, ["status", "--json"])

    payload = _json_document(result, exit_code=int(ExitCode.DISCOVERY))
    assert payload["command"] == "status"
    assert payload["ok"] is False
    assert payload["result"] == {}
    assert payload["errors"] == [
        {
            "code": "ambiguous_mount",
            "message": "Choose an explicit mount.",
            "details": {"mounts": ["/one", "/two"]},
        }
    ]


def test_unexpected_json_command_failure_is_sanitized_into_one_envelope(
    runner: CliRunner,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del project

    def fail(**_kwargs: object) -> SelectedTarget:
        raise AssertionError("private implementation detail")

    monkeypatch.setattr(cli, "select_target", fail)

    result = runner.invoke(cli.app, ["status", "--json"])

    payload = _json_document(result, exit_code=int(ExitCode.INTERNAL))
    assert payload["command"] == "status"
    assert payload["errors"][0]["code"] == "internal_error"
    assert "private implementation detail" not in result.stdout
    assert result.stderr == ""


def test_doctor_reports_read_only_diagnostics(
    runner: CliRunner,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Project | None] = []
    doctor_result: dict[str, Any] = {
        "versions": {
            "patchcord": __version__,
            "python": "3.14.0",
            "platform": "test",
            "dependencies": {"circup": "3.0.4"},
        },
        "capabilities": {"deployment": {"available": True, "backend": "patchcord-filesystem"}},
        "project": {"found": True, "valid": True, "root": str(project.root)},
        "drives": [],
        "serial_ports": [],
    }

    def collect(selected: Project | None) -> dict[str, Any]:
        seen.append(selected)
        return doctor_result

    monkeypatch.setattr(cli, "collect_doctor", collect)

    result = runner.invoke(cli.app, ["doctor", "--json"])

    payload = _json_document(result)
    assert payload["command"] == "doctor"
    assert payload["result"] == doctor_result
    assert seen == [project]
    assert not project.state_dir.exists()


def test_doctor_failure_is_normalized(
    runner: CliRunner,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del project

    def fail(_project: Project | None) -> dict[str, Any]:
        raise RuntimeError("backend internals must not leak")

    monkeypatch.setattr(cli, "collect_doctor", fail)

    result = runner.invoke(cli.app, ["doctor", "--json"])

    payload = _json_document(result, exit_code=int(ExitCode.INTERNAL))
    assert payload["errors"][0]["code"] == "doctor_failed"
    assert "backend internals" not in payload["errors"][0]["message"]


def test_hardware_validate_offline_never_discovers_or_connects(
    runner: CliRunner,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del project

    def unexpected_selection(**_kwargs: object) -> SelectedTarget:
        raise AssertionError("offline validation must not discover hardware")

    monkeypatch.setattr(cli, "select_target", unexpected_selection)

    result = runner.invoke(cli.app, ["hardware", "validate", "--offline", "--json"])

    payload = _json_document(result)
    assert payload["command"] == "hardware validate"
    assert payload["result"] == {
        "valid": True,
        "connected": False,
        "diagnostics": [],
    }
    assert payload["diagnostics"] == {"mode": "offline"}


def test_hardware_validate_requires_both_mount_and_port_for_connected_checks(
    runner: CliRunner,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partial_target = _target(project, with_serial=False)
    monkeypatch.setattr(cli, "select_target", _selection(partial_target))

    class UnexpectedValidator:
        def __init__(self, _port: str) -> None:
            raise AssertionError("connected validation needs a complete target")

    monkeypatch.setattr(cli, "ConnectedHardwareValidator", UnexpectedValidator)

    result = runner.invoke(cli.app, ["hardware", "validate", "--json"])

    payload = _json_document(result)
    assert payload["ok"] is True
    assert payload["result"]["connected"] is False
    assert payload["target"]["mount"] == str(_mount(partial_target))
    assert payload["target"]["port"] is None


def test_hardware_validate_runs_connected_checks_when_target_is_complete(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    transport_events: list[tuple[str, object]] = []
    monkeypatch.setattr(cli, "select_target", _selection(target))
    monkeypatch.setattr(cli, "SerialTransport", _fake_transport(transport_events))
    monkeypatch.setattr(
        cli,
        "execution_capability",
        lambda: ExecutionCapability(True, "circremote", "0.12.0-test", None),
    )

    class FakeValidator:
        def __init__(
            self,
            port: str,
            *,
            execution_output: Callable[[str], object] | None = None,
        ) -> None:
            calls.append(("init", port))
            if execution_output is not None:
                execution_output("connected probe\n")

        def validate_connected(self, document: HardwareDocument) -> list[Diagnostic]:
            calls.append(("validate", document.board.id))
            return [
                Diagnostic(
                    severity=DiagnosticSeverity.WARNING,
                    code="connected_notice",
                    message="Connected validation ran.",
                )
            ]

    monkeypatch.setattr(cli, "ConnectedHardwareValidator", FakeValidator)

    result = runner.invoke(cli.app, ["hardware", "validate", "--json"])

    payload = _json_document(result)
    assert payload["ok"] is True
    assert payload["result"]["connected"] is True
    assert payload["result"]["diagnostics"][0]["code"] == "connected_notice"
    assert payload["diagnostics"] == {
        "mode": "connected",
        "backend": "circremote",
        "backend_version": "0.12.0-test",
    }
    assert payload["target"]["port"] == "COM7"
    assert calls == [("init", "COM7"), ("validate", "test_board")]
    assert transport_events == [("open", "COM7"), ("reset", 0)]
    assert b"connected probe\n" in project.serial_log.read_bytes()


def test_hardware_validate_rejects_unaccepted_backend_before_serial_io(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del project
    monkeypatch.setattr(cli, "select_target", _selection(target))
    monkeypatch.setattr(
        cli,
        "execution_capability",
        lambda: ExecutionCapability(
            False,
            "circremote",
            "0.12.0",
            "fails_isolated_configuration_gate",
        ),
    )

    class UnexpectedTransport:
        def __init__(self, _port: str) -> None:
            raise AssertionError("an unavailable backend must not touch the serial port")

    monkeypatch.setattr(cli, "SerialTransport", UnexpectedTransport)

    result = runner.invoke(cli.app, ["hardware", "validate", "--json"])

    payload = _json_document(result, exit_code=int(ExitCode.DEPENDENCY))
    assert payload["errors"][0]["code"] == "execution_backend_unavailable"
    assert payload["target"]["port"] == "COM7"


def test_hardware_validate_stops_after_offline_errors(
    runner: CliRunner,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.hardware_file.write_text("[]\n", encoding="utf-8")

    def unexpected_selection(**_kwargs: object) -> SelectedTarget:
        raise AssertionError("invalid offline data must gate connected validation")

    monkeypatch.setattr(cli, "select_target", unexpected_selection)

    result = runner.invoke(cli.app, ["hardware", "validate", "--json"])

    payload = _json_document(result, exit_code=int(ExitCode.VALIDATION))
    assert payload["ok"] is False
    assert payload["result"]["valid"] is False
    assert payload["result"]["connected"] is False
    assert payload["errors"][0]["code"] == "root_not_mapping"


def test_deploy_copies_files_and_returns_manifest_and_startup(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (project.device_dir / "support.py").write_text("VALUE = 1\n", encoding="utf-8")
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(cli, "select_target", _selection(target))
    monkeypatch.setattr(
        cli,
        "SerialTransport",
        _fake_transport(events, reset_output=b"boot complete\n"),
    )

    result = runner.invoke(cli.app, ["deploy", "--capture", "0.25", "--json"])

    payload = _json_document(result)
    assert payload["command"] == "deploy"
    assert payload["ok"] is True
    assert payload["result"]["startup_output"] == "boot complete\n"
    assert payload["result"]["capture_seconds"] == 0.25
    assert payload["result"]["traceback_detected"] is False
    assert [entry["path"] for entry in payload["result"]["manifest"]["created"]] == [
        "support.py",
        "code.py",
    ]
    assert (_mount(target) / "support.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (_mount(target) / "code.py").read_bytes() == (
        project.device_dir / "code.py"
    ).read_bytes()
    assert events == [("open", "COM7"), ("interrupt", "COM7"), ("reset", 0.25)]
    operation = json.loads(project.operations_log.read_text(encoding="utf-8"))
    assert operation["command"] == "deploy"
    assert operation["ok"] is True


def test_deploy_refuses_protected_files_without_copying_them(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (project.device_dir / "boot.py").write_text("print('protected')\n", encoding="utf-8")
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(cli, "select_target", _selection(target))
    monkeypatch.setattr(cli, "SerialTransport", _fake_transport(events))

    result = runner.invoke(cli.app, ["deploy", "--json"])

    payload = _json_document(result, exit_code=int(ExitCode.DEPLOYMENT))
    assert payload["errors"][0]["code"] == "protected_files_refused"
    assert payload["errors"][0]["details"] == {"paths": ["boot.py"]}
    assert not (_mount(target) / "boot.py").exists()
    assert not (_mount(target) / "code.py").exists()
    assert events == []


def test_deploy_rejects_board_identity_mismatch_before_serial_io(
    runner: CliRunner,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mismatched = _target(project, board_id="different_board")
    monkeypatch.setattr(cli, "select_target", _selection(mismatched))

    class UnexpectedTransport:
        def __init__(self, _port: str) -> None:
            raise AssertionError("board mismatch must gate serial I/O")

    monkeypatch.setattr(cli, "SerialTransport", UnexpectedTransport)

    result = runner.invoke(cli.app, ["deploy", "--json"])

    payload = _json_document(result, exit_code=int(ExitCode.DEPLOYMENT))
    assert payload["errors"] == [
        {
            "code": "board_id_mismatch",
            "message": "The connected board does not match hardware.yaml.",
            "details": {"declared": "test_board", "connected": "different_board"},
        }
    ]
    assert not (_mount(mismatched) / "code.py").exists()


def test_deploy_refuses_unknown_board_identity_before_serial_io(
    runner: CliRunner,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unknown = _target(project, board_id=None)
    monkeypatch.setattr(cli, "select_target", _selection(unknown))

    class UnexpectedTransport:
        def __init__(self, _port: str) -> None:
            raise AssertionError("unknown board identity must gate serial I/O")

    monkeypatch.setattr(cli, "SerialTransport", UnexpectedTransport)

    result = runner.invoke(cli.app, ["deploy", "--json"])

    payload = _json_document(result, exit_code=int(ExitCode.DEPLOYMENT))
    assert payload["errors"][0]["code"] == "board_identity_unavailable"
    assert payload["target"]["board_id"] is None


def test_deploy_reports_captured_startup_traceback_as_failure(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    traceback_output = (
        b"Traceback (most recent call last):\n"
        b'  File "code.py", line 1, in <module>\n'
        b"RuntimeError: broken\n"
    )
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(cli, "select_target", _selection(target))
    monkeypatch.setattr(
        cli,
        "SerialTransport",
        _fake_transport(
            events,
            reset_output=traceback_output,
            reset_traceback=True,
        ),
    )

    result = runner.invoke(cli.app, ["deploy", "--capture", "1", "--json"])

    payload = _json_document(result, exit_code=int(ExitCode.DEPLOYMENT))
    error = payload["errors"][0]
    assert error["code"] == "board_startup_traceback"
    assert error["details"]["startup_output"] == traceback_output.decode()
    assert error["details"]["traceback_detected"] is True
    assert (_mount(target) / "code.py").is_file()
    assert events == [("open", "COM7"), ("interrupt", "COM7"), ("reset", 1.0)]
    operation = json.loads(project.operations_log.read_text(encoding="utf-8"))
    assert operation["ok"] is False
    assert operation["result"]["error_code"] == "board_startup_traceback"
    assert "manifest" in operation["result"]


def test_deploy_attempts_recovery_when_interrupt_capture_fails(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(cli, "select_target", _selection(target))

    class FailingInterruptTransport:
        def __init__(self, _port: str) -> None:
            events.append("open")

        def interrupt(
            self,
            *,
            on_data: Callable[[bytes], object] | None = None,
        ) -> SerialCapture:
            events.append("interrupt")
            if on_data is not None:
                on_data(b"KeyboardInterrupt\n")
            raise TransportError("serial_read_failed", "Capture failed.")

        def reset(
            self,
            *,
            capture: float,
            on_data: Callable[[bytes], object] | None = None,
        ) -> SerialCapture:
            del capture, on_data
            events.append("recovery_reset")
            return _capture(b"")

    monkeypatch.setattr(cli, "SerialTransport", FailingInterruptTransport)

    result = runner.invoke(cli.app, ["deploy", "--json"])

    payload = _json_document(result, exit_code=int(ExitCode.TRANSPORT))
    assert payload["errors"][0]["code"] == "serial_read_failed"
    assert events == ["open", "interrupt", "recovery_reset"]


def test_monitor_streams_to_stdout_explicit_output_and_persistent_log(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    board_output = b"first\nsecond\n"
    destination = project.root / "captured" / "board.log"
    monkeypatch.setattr(cli, "select_target", _selection(target))
    monkeypatch.setattr(
        cli,
        "SerialTransport",
        _fake_transport(events, monitor_output=board_output),
    )

    result = runner.invoke(
        cli.app,
        ["monitor", "--seconds", "0.5", "--output", str(destination)],
    )

    assert result.exit_code == 0
    assert result.stdout == board_output.decode()
    assert destination.read_bytes() == board_output
    assert board_output in project.serial_log.read_bytes()
    assert events == [("open", "COM7"), ("monitor", 0.5)]


def test_monitor_json_emits_one_bounded_result(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(cli, "select_target", _selection(target))
    monkeypatch.setattr(
        cli,
        "SerialTransport",
        _fake_transport(events, monitor_output=b"sample\n"),
    )

    result = runner.invoke(cli.app, ["monitor", "--seconds", "0.5", "--json"])

    payload = _json_document(result)
    assert payload["result"]["output"] == "sample\n"
    assert payload["result"]["bytes"] == 7
    assert payload["target"]["port"] == "COM7"
    assert events == [("open", "COM7"), ("monitor", 0.5)]


def test_logs_reads_a_bounded_tail_without_opening_serial(
    runner: CliRunner,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.logs_dir.mkdir(parents=True)
    project.serial_log.write_text("one\ntwo\nthree\n", encoding="utf-8")

    class UnexpectedTransport:
        def __init__(self, _port: str) -> None:
            raise AssertionError("logs must not open a serial port")

    monkeypatch.setattr(cli, "SerialTransport", UnexpectedTransport)

    result = runner.invoke(cli.app, ["logs", "--tail", "2", "--json"])

    payload = _json_document(result)
    assert payload["command"] == "logs"
    assert payload["result"] == {"text": "two\nthree\n", "line_count": 2}


def test_logs_rejects_conflicting_filters_as_json(
    runner: CliRunner,
    project: Project,
) -> None:
    del project

    result = runner.invoke(
        cli.app,
        ["logs", "--tail", "2", "--since", "1m", "--json"],
    )

    payload = _json_document(result, exit_code=int(ExitCode.USAGE))
    assert payload["errors"][0]["code"] == "logs_filter_conflict"


def test_click_parameter_errors_still_use_the_json_envelope(
    runner: CliRunner,
    project: Project,
) -> None:
    del project

    result = runner.invoke(cli.app, ["logs", "--tail", "-1", "--json"])

    payload = _json_document(result, exit_code=int(ExitCode.USAGE))
    assert payload["command"] == "logs"
    assert payload["errors"][0]["code"] == "invalid_usage"


def test_empty_serial_override_is_a_structured_discovery_error(
    runner: CliRunner,
    project: Project,
) -> None:
    del project

    result = runner.invoke(
        cli.app,
        ["--port", "", "monitor", "--seconds", "0", "--json"],
    )

    payload = _json_document(result, exit_code=int(ExitCode.DISCOVERY))
    assert payload["command"] == "monitor"
    assert payload["errors"][0]["code"] == "invalid_serial_port"


def test_oversized_log_tail_is_a_structured_usage_error(
    runner: CliRunner,
    project: Project,
) -> None:
    project.logs_dir.mkdir(parents=True)
    project.serial_log.write_text("one\n", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        ["logs", "--tail", "1" + ("0" * 80), "--json"],
    )

    payload = _json_document(result, exit_code=int(ExitCode.USAGE))
    assert payload["command"] == "logs"
    assert payload["errors"][0]["code"] == "invalid_usage"


def test_log_duration_datetime_underflow_is_a_structured_usage_error(
    runner: CliRunner,
    project: Project,
) -> None:
    del project

    result = runner.invoke(
        cli.app,
        ["logs", "--since", "999999999d", "--json"],
    )

    payload = _json_document(result, exit_code=int(ExitCode.USAGE))
    assert payload["command"] == "logs"
    assert payload["errors"][0]["code"] == "invalid_duration"


def test_unresolvable_monitor_output_path_is_a_structured_error(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del project
    monkeypatch.setattr(cli, "select_target", _selection(target))

    def fail_expanduser(_path: Path) -> Path:
        raise RuntimeError("unknown user")

    monkeypatch.setattr(Path, "expanduser", fail_expanduser)

    result = runner.invoke(
        cli.app,
        ["monitor", "--seconds", "0", "--output", "~missing-user/log", "--json"],
    )

    payload = _json_document(result, exit_code=int(ExitCode.TRANSPORT))
    assert payload["command"] == "monitor"
    assert payload["errors"][0]["code"] == "monitor_output_failed"
    assert "unknown user" not in result.stdout


@pytest.mark.parametrize(
    "arguments",
    [
        ["deploy", "--capture", "nan", "--json"],
        ["monitor", "--seconds", "inf", "--json"],
        ["reset", "--capture", "nan", "--json"],
        ["repl", "--eval", "1", "--timeout", "inf", "--json"],
    ],
)
def test_non_finite_numeric_options_are_json_usage_errors(
    runner: CliRunner,
    project: Project,
    arguments: list[str],
) -> None:
    del project

    result = runner.invoke(cli.app, arguments)

    payload = _json_document(result, exit_code=int(ExitCode.USAGE))
    assert payload["errors"][0]["code"] == "invalid_usage"


def test_interrupt_returns_and_persists_bounded_console_output(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(cli, "select_target", _selection(target))
    monkeypatch.setattr(
        cli,
        "SerialTransport",
        _fake_transport(events, interrupt_output=b"KeyboardInterrupt\n>>> "),
    )

    result = runner.invoke(cli.app, ["interrupt", "--json"])

    payload = _json_document(result)
    assert payload["result"] == {
        "output": "KeyboardInterrupt\n>>> ",
        "interrupted": True,
    }
    assert b"KeyboardInterrupt\n>>> " in project.serial_log.read_bytes()
    assert events == [("open", "COM7"), ("interrupt", "COM7")]


def test_reset_forwards_capture_and_reports_traceback_classification(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(cli, "select_target", _selection(target))
    monkeypatch.setattr(
        cli,
        "SerialTransport",
        _fake_transport(events, reset_output=b"soft reboot\nready\n"),
    )

    result = runner.invoke(cli.app, ["reset", "--capture", "2.5", "--json"])

    payload = _json_document(result)
    assert payload["result"] == {
        "output": "soft reboot\nready\n",
        "capture_seconds": 2.5,
        "traceback_detected": False,
    }
    assert b"soft reboot\nready\n" in project.serial_log.read_bytes()
    assert events == [("open", "COM7"), ("reset", 2.5)]


def test_repl_interactive_uses_miniterm_and_records_session_boundaries(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ports: list[str] = []
    monkeypatch.setattr(cli, "select_target", _selection(target))

    def miniterm(port: str) -> int:
        ports.append(port)
        return 0

    monkeypatch.setattr(cli, "run_miniterm", miniterm)

    result = runner.invoke(cli.app, ["repl"])

    assert result.exit_code == 0
    assert result.stdout == ""
    assert ports == ["COM7"]
    operations = [
        json.loads(line) for line in project.operations_log.read_text(encoding="utf-8").splitlines()
    ]
    assert [entry["command"] for entry in operations] == [
        "repl interactive start",
        "repl interactive end",
    ]
    assert operations[-1]["result"] == {"returncode": 0}


def test_repl_interactive_records_failed_session_end(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "select_target", _selection(target))

    def fail(_port: str) -> int:
        raise TransportError(
            "interactive_repl_failed",
            "miniterm failed.",
            details={"returncode": 2},
        )

    monkeypatch.setattr(cli, "run_miniterm", fail)

    result = runner.invoke(cli.app, ["repl"])

    assert result.exit_code == int(ExitCode.TRANSPORT)
    operations = [
        json.loads(line) for line in project.operations_log.read_text(encoding="utf-8").splitlines()
    ]
    assert operations[-1]["command"] == "repl interactive end"
    assert operations[-1]["ok"] is False
    assert operations[-1]["result"] == {"returncode": 2}


def test_repl_eval_uses_bounded_execution_options_and_json(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, float, bool]] = []
    monkeypatch.setattr(cli, "select_target", _selection(target))

    def run(
        port: str,
        source: str,
        *,
        timeout: float,
        reset: bool,
    ) -> ExecutionResult:
        calls.append((port, source, timeout, reset))
        return ExecutionResult(
            output="42\n",
            payload={"completed": True},
            backend_version="0.12.0",
            stderr="",
        )

    monkeypatch.setattr(cli, "run_script", run)

    result = runner.invoke(
        cli.app,
        [
            "repl",
            "--eval",
            "print(42)",
            "--timeout",
            "7",
            "--no-reset",
            "--json",
        ],
    )

    payload = _json_document(result)
    assert payload["result"] == {"output": "42\n", "completed": True, "reset": False}
    assert payload["diagnostics"] == {
        "backend": "circremote",
        "backend_version": "0.12.0",
    }
    assert calls == [("COM7", "print(42)", 7.0, False)]
    assert b"42\n" in project.serial_log.read_bytes()


def test_repl_file_uses_bounded_file_adapter(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = project.root / "probe.py"
    script.write_text("print('file')\n", encoding="utf-8")
    calls: list[tuple[str, Path, float, bool]] = []
    monkeypatch.setattr(cli, "select_target", _selection(target))

    def run(
        port: str,
        path: Path,
        *,
        timeout: float,
        reset: bool,
    ) -> ExecutionResult:
        calls.append((port, path, timeout, reset))
        return ExecutionResult(
            output="file\n",
            payload={"completed": True},
            backend_version="0.12.0",
            stderr="",
        )

    monkeypatch.setattr(cli, "run_file", run)

    result = runner.invoke(cli.app, ["repl", "--file", str(script), "--json"])

    payload = _json_document(result)
    assert payload["result"]["output"] == "file\n"
    assert payload["result"]["reset"] is True
    assert calls == [("COM7", script, 30.0, True)]


def test_repl_rejects_eval_and_file_conflict_before_discovery(
    runner: CliRunner,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = project.root / "script.py"

    def unexpected_selection(**_kwargs: object) -> SelectedTarget:
        raise AssertionError("input conflicts must gate target discovery")

    monkeypatch.setattr(cli, "select_target", unexpected_selection)

    result = runner.invoke(
        cli.app,
        ["repl", "--eval", "pass", "--file", str(script), "--json"],
    )

    payload = _json_document(result, exit_code=int(ExitCode.USAGE))
    assert payload["errors"][0]["code"] == "repl_input_conflict"


def test_repl_rejects_json_for_interactive_sessions(
    runner: CliRunner,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del project

    def unexpected_selection(**_kwargs: object) -> SelectedTarget:
        raise AssertionError("interactive JSON conflicts must gate discovery")

    monkeypatch.setattr(cli, "select_target", unexpected_selection)

    result = runner.invoke(cli.app, ["repl", "--json"])

    payload = _json_document(result, exit_code=int(ExitCode.USAGE))
    assert payload["errors"][0]["code"] == "interactive_json_unsupported"


@pytest.mark.parametrize(
    ("subcommand", "probe_name", "probe_result"),
    [
        (
            "pins",
            "probe_pins",
            {
                "board_id": "test_board",
                "pins": [{"name": "D5", "identity": 5}],
            },
        ),
        (
            "i2c",
            "probe_i2c",
            {
                "addresses": [0x3C, 0x76],
                "addresses_hex": ["0x3c", "0x76"],
            },
        ),
    ],
)
def test_probe_commands_emit_structured_results_and_log_board_output(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
    subcommand: str,
    probe_name: str,
    probe_result: dict[str, Any],
) -> None:
    calls: list[str] = []
    execution = ExecutionResult(
        output=f"{subcommand} probe output\n",
        payload={},
        backend_version="0.12.0",
        stderr="",
    )
    monkeypatch.setattr(cli, "select_target", _selection(target))

    def probe(port: str) -> tuple[dict[str, Any], ExecutionResult]:
        calls.append(port)
        return probe_result, execution

    monkeypatch.setattr(cli, probe_name, probe)

    result = runner.invoke(cli.app, ["probe", subcommand, "--json"])

    payload = _json_document(result)
    assert payload["command"] == f"probe {subcommand}"
    assert payload["result"] == probe_result
    assert payload["diagnostics"] == {
        "backend": "circremote",
        "backend_version": "0.12.0",
    }
    assert calls == ["COM7"]
    assert execution.output.encode() in project.serial_log.read_bytes()


@pytest.mark.parametrize(
    ("arguments", "expected_mode", "expected_packages", "expected_auto"),
    [
        ([], "requirements", [], False),
        (
            ["adafruit_requests", "adafruit_bus_device"],
            "packages",
            [
                "adafruit_requests",
                "adafruit_bus_device",
            ],
            False,
        ),
        (["--auto"], "auto", [], True),
    ],
)
def test_libs_install_delegates_each_supported_mode(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    expected_mode: str,
    expected_packages: list[str],
    expected_auto: bool,
) -> None:
    calls: list[tuple[Path, Path, list[str], bool]] = []
    monkeypatch.setattr(cli, "select_target", _selection(target))

    def install(
        mount: Path,
        requirements: Path,
        *,
        packages: list[str],
        auto: bool,
    ) -> CircupResult:
        calls.append((mount, requirements, packages, auto))
        return CircupResult(
            returncode=0,
            stdout="installed\n",
            stderr="",
            backend_version="3.0.4",
        )

    monkeypatch.setattr(cli.circup, "install", install)

    result = runner.invoke(cli.app, ["libs", "install", *arguments, "--json"])

    payload = _json_document(result)
    assert payload["result"] == {
        "mode": expected_mode,
        "packages": expected_packages,
        "returncode": 0,
    }
    assert payload["diagnostics"] == {
        "backend": "circup",
        "backend_version": "3.0.4",
    }
    assert calls == [
        (
            _mount(target),
            project.requirements_file,
            expected_packages,
            expected_auto,
        )
    ]


def test_libs_install_normalizes_package_auto_conflict(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del project
    monkeypatch.setattr(cli, "select_target", _selection(target))

    def conflict(
        _mount: Path,
        _requirements: Path,
        *,
        packages: list[str],
        auto: bool,
    ) -> CircupResult:
        assert packages == ["adafruit_requests"]
        assert auto is True
        raise DependencyError(
            "circup_arguments_conflict",
            "Named packages and --auto cannot be used together.",
        )

    monkeypatch.setattr(cli.circup, "install", conflict)

    result = runner.invoke(
        cli.app,
        ["libs", "install", "adafruit_requests", "--auto", "--json"],
    )

    payload = _json_document(result, exit_code=int(ExitCode.DEPENDENCY))
    assert payload["errors"][0]["code"] == "circup_arguments_conflict"


def test_libs_freeze_reports_the_atomically_generated_requirements(
    runner: CliRunner,
    project: Project,
    target: SelectedTarget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(cli, "select_target", _selection(target))

    def freeze(mount: Path, destination: Path) -> CircupResult:
        calls.append((mount, destination))
        destination.write_text(
            "adafruit_bus_device==5.2.10\nadafruit_requests==4.1.10\n",
            encoding="utf-8",
        )
        return CircupResult(
            returncode=0,
            stdout="frozen\n",
            stderr="",
            backend_version="3.0.4",
        )

    monkeypatch.setattr(cli.circup, "freeze", freeze)

    result = runner.invoke(cli.app, ["libs", "freeze", "--json"])

    payload = _json_document(result)
    assert payload["result"] == {
        "requirements": [
            "adafruit_bus_device==5.2.10",
            "adafruit_requests==4.1.10",
        ],
        "path": str(project.requirements_file),
        "returncode": 0,
    }
    assert payload["diagnostics"] == {
        "backend": "circup",
        "backend_version": "3.0.4",
    }
    assert calls == [(_mount(target), project.requirements_file)]
