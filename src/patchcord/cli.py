"""Patchcord command-line interface."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Annotated, Any, NoReturn, Protocol, runtime_checkable

import typer
from rich.console import Console
from rich.table import Table
from typer.core import TyperGroup

from patchcord import __version__
from patchcord.adapters import circup
from patchcord.adapters.deployment import DeployManifest, build_plan, execute_plan
from patchcord.adapters.discovery import SelectedTarget, select_target
from patchcord.adapters.execution import capability as execution_capability
from patchcord.adapters.execution import run_file, run_script
from patchcord.adapters.serial import SerialCapture, SerialTransport
from patchcord.adapters.terminal import run_miniterm
from patchcord.doctor import collect_doctor
from patchcord.errors import (
    BoardExecutionError,
    DependencyError,
    DeploymentError,
    DiscoveryError,
    ExitCode,
    PatchcordError,
    ProjectError,
)
from patchcord.hardware.connected import ConnectedHardwareValidator
from patchcord.hardware.validation import (
    DiagnosticSeverity,
    HardwareValidationReport,
    validate_hardware_file,
)
from patchcord.locking import project_lock, serial_lock
from patchcord.models import ErrorRecord, TargetInfo
from patchcord.operation_log import record_operation
from patchcord.output import emit_error, emit_result
from patchcord.probes import probe_i2c, probe_pins
from patchcord.project import Project, find_project, find_project_candidate, init_project
from patchcord.serial_log import MAX_TAIL_LINES, SerialLog, parse_duration


class JsonUsageGroup(TyperGroup):
    """Keep every non-interactive failure inside Patchcord's JSON envelope."""

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        observed_args = list(sys.argv[1:] if args is None else args)
        if "--json" not in observed_args:
            return super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=standalone_mode,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        try:
            result = super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except Exception as error:
            if isinstance(error, _UsageFailure):
                normalized = PatchcordError(
                    "invalid_usage",
                    error.format_message(),
                    exit_code=ExitCode.USAGE,
                )
            else:
                normalized = PatchcordError(
                    "internal_error",
                    "Patchcord could not complete the command.",
                    exit_code=ExitCode.INTERNAL,
                )
            emit_error(_usage_command(observed_args), normalized, json_output=True)
            result = int(normalized.exit_code)
        if standalone_mode:
            raise SystemExit(result if isinstance(result, int) else 0)
        return result


@runtime_checkable
class _UsageFailure(Protocol):
    def format_message(self) -> str:
        """Return the CLI framework's stable usage message."""

        ...


def _usage_command(arguments: Sequence[str]) -> str:
    commands = {
        "deploy",
        "doctor",
        "hardware",
        "init",
        "interrupt",
        "libs",
        "logs",
        "monitor",
        "probe",
        "repl",
        "reset",
        "status",
    }
    groups = {
        "hardware": {"validate"},
        "libs": {"freeze", "install"},
        "probe": {"i2c", "pins"},
    }
    skip_next = False
    for index, argument in enumerate(arguments):
        if skip_next:
            skip_next = False
            continue
        if argument in {"--legacy-board-id", "--mount", "--port"}:
            skip_next = True
            continue
        if argument.startswith(("--legacy-board-id=", "--mount=", "--port=")):
            continue
        if argument not in commands:
            continue
        if argument in groups and index + 1 < len(arguments):
            child = arguments[index + 1]
            if child in groups[argument]:
                return f"{argument} {child}"
        return argument
    return "patchcord"


app = typer.Typer(
    name="patchcord",
    cls=JsonUsageGroup,
    help="Safely coordinate CircuitPython projects and connected boards.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
probe_app = typer.Typer(help="Run bounded, read-only board probes.", no_args_is_help=True)
libs_app = typer.Typer(help="Manage CircuitPython libraries through circup.", no_args_is_help=True)
hardware_app = typer.Typer(
    help="Validate the project's hardware description.",
    no_args_is_help=True,
)
app.add_typer(probe_app, name="probe")
app.add_typer(libs_app, name="libs")
app.add_typer(hardware_app, name="hardware")


@dataclass(slots=True)
class AppState:
    """Global explicit target overrides."""

    mount: Path | None = None
    port: str | None = None
    legacy_board_id: str | None = None


def _state(ctx: typer.Context) -> AppState:
    state = ctx.find_root().obj
    if not isinstance(state, AppState):
        state = AppState()
        ctx.find_root().obj = state
    return state


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


def _finite_float(value: float) -> float:
    if not isfinite(value):
        raise typer.BadParameter("must be a finite number")
    return value


def _finite_optional_float(value: float | None) -> float | None:
    if value is not None and not isfinite(value):
        raise typer.BadParameter("must be a finite number")
    return value


@app.callback()
def app_callback(
    ctx: typer.Context,
    mount: Annotated[
        Path | None,
        typer.Option("--mount", help="Explicit CIRCUITPY mount path."),
    ] = None,
    port: Annotated[
        str | None,
        typer.Option("--port", help="Explicit CircuitPython serial port."),
    ] = None,
    legacy_board_id: Annotated[
        str | None,
        typer.Option(
            "--legacy-board-id",
            help="Assert an official board ID when an explicit legacy drive omits it.",
        ),
    ] = None,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the Patchcord version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Set explicit target selection shared by board commands."""

    del version
    ctx.obj = AppState(mount=mount, port=port, legacy_board_id=legacy_board_id)


def _target_info(target: SelectedTarget | None) -> TargetInfo | None:
    return target.public() if target is not None else None


def _fail(
    command: str,
    error: PatchcordError,
    *,
    json_output: bool,
    target: SelectedTarget | None = None,
) -> NoReturn:
    emit_error(command, error, json_output=json_output, target=_target_info(target))
    raise typer.Exit(code=int(error.exit_code))


def _require_project(command: str, *, json_output: bool) -> Project:
    try:
        return find_project()
    except ProjectError as error:
        _fail(command, error, json_output=json_output)


def _hardware_report(project: Project) -> HardwareValidationReport:
    return validate_hardware_file(project.hardware_file, project.requirements_file)


@app.command("init")
def init_command(
    ctx: typer.Context,
    path: Annotated[
        Path,
        typer.Argument(help="Project directory to initialize."),
    ] = Path(),
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Create missing files for a Patchcord project."""

    state = _state(ctx)
    board_id = ""
    try:
        target = select_target(
            mount=state.mount,
            port=state.port,
            legacy_board_id=state.legacy_board_id,
            strict_port_ambiguity=False,
        )
        if target.drive and target.drive.board_id:
            board_id = target.drive.board_id
    except DiscoveryError as error:
        if state.mount is not None or state.port is not None or state.legacy_board_id is not None:
            _fail("init", error, json_output=json_output)
        target = None
    initialization_path = path
    try:
        initializes_current_directory = path.expanduser().resolve() == Path.cwd().resolve()
        if initializes_current_directory:
            initialization_path = find_project(path).root
    except (OSError, RuntimeError, ProjectError):
        pass
    try:
        project, created, preserved = init_project(initialization_path, board_id=board_id)
    except (OSError, RuntimeError, UnicodeError):
        _fail(
            "init",
            ProjectError(
                "project_initialization_failed",
                "Could not initialize the project.",
                details={"path": str(initialization_path)},
            ),
            json_output=json_output,
        )
        return
    created_paths = [item.relative_to(project.root).as_posix() for item in created]
    preserved_paths = [item.relative_to(project.root).as_posix() for item in preserved]
    result = {
        "root": str(project.root),
        "created": created_paths,
        "preserved": preserved_paths,
        "board_id": board_id or None,
    }

    def human(console: Console) -> None:
        console.print(f"Initialized Patchcord project at [bold]{project.root}[/bold]")
        for file_path in created_paths:
            console.print(f"  created {file_path}")
        for file_path in preserved_paths:
            console.print(f"  preserved {file_path}")

    emit_result(
        "init",
        result,
        json_output=json_output,
        target=target.public() if target else None,
        human=human,
    )


@app.command("status")
def status_command(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Report the selected CircuitPython board, mount, and serial port."""

    state = _state(ctx)
    try:
        target = select_target(
            mount=state.mount,
            port=state.port,
            legacy_board_id=state.legacy_board_id,
            require_mount=True,
            require_port=True,
        )
    except PatchcordError as error:
        _fail("status", error, json_output=json_output)
        return
    drive = target.drive
    if drive is None:  # narrowed by require_mount, retained for adapter substitutability
        _fail(
            "status",
            DiscoveryError("mount_not_found", "No CircuitPython drive was selected."),
            json_output=json_output,
            target=target,
        )
        return
    result: dict[str, Any] = {
        "board_id": drive.board_id,
        "board_id_source": drive.board_id_source,
        "board_name": drive.board_name,
        "circuitpython_version": drive.circuitpython_version,
        "mount": str(drive.mount),
        "port": target.serial.device if target.serial else None,
        "storage": {"free_bytes": drive.free_bytes, "total_bytes": drive.total_bytes},
    }

    def human(console: Console) -> None:
        table = Table(show_header=False)
        table.add_column("Field", style="bold")
        table.add_column("Value")
        for key, value in (
            ("Board ID", drive.board_id or "unknown"),
            ("Board ID source", drive.board_id_source or "unknown"),
            ("Board", drive.board_name or "unknown"),
            ("CircuitPython", drive.circuitpython_version or "unknown"),
            ("Mount", str(drive.mount)),
            ("Serial", target.serial.device if target.serial else "unknown"),
            ("Free bytes", str(drive.free_bytes) if drive.free_bytes is not None else "unknown"),
        ):
            table.add_row(key, value)
        console.print(table)

    emit_result("status", result, json_output=json_output, target=target.public(), human=human)


@app.command("doctor")
def doctor_command(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Perform read-only host and project diagnostics."""

    project = find_project_candidate()
    try:
        result = collect_doctor(project)
    except PatchcordError as error:
        _fail("doctor", error, json_output=json_output)
    except (OSError, RuntimeError) as exc:
        del exc
        _fail(
            "doctor",
            PatchcordError("doctor_failed", "Diagnostics could not be completed."),
            json_output=json_output,
        )
        return

    def human(console: Console) -> None:
        versions = result["versions"]
        console.print(f"Patchcord {versions['patchcord']} on Python {versions['python']}")
        project_data = result["project"]
        if project_data["found"]:
            console.print(f"Project: {project_data['root']}")
        else:
            console.print("Project: not found")
        console.print(f"CircuitPython drives: {len(result['drives'])}")
        console.print(f"CircuitPython REPL ports: {len(result['serial_ports'])}")
        capabilities = result["capabilities"]
        for name, capability in capabilities.items():
            if not capability["available"]:
                marker = "[yellow]unavailable[/yellow]"
            elif capability.get("target_compatibility") == "unchecked":
                marker = (
                    "[green]backend available[/green]; "
                    "[yellow]target compatibility unchecked[/yellow]"
                )
            else:
                marker = "[green]available[/green]"
            console.print(f"  {name}: {marker}")

    emit_result("doctor", result, json_output=json_output, human=human)


def _write_raw_stdout(data: bytes) -> None:
    stream = getattr(sys.stdout, "buffer", None)
    if stream is None:
        sys.stdout.write(data.decode("utf-8", errors="replace"))
        sys.stdout.flush()
        return
    stream.write(data)
    stream.flush()


def _deploy_reset(
    transport: SerialTransport,
    serial_log: SerialLog,
    *,
    port: str,
    capture: float,
    operation: str,
) -> tuple[SerialCapture | None, PatchcordError | None]:
    """Try a logged reset, then retry without logging so evidence cannot block safety."""

    first_error: PatchcordError | None = None
    try:
        with serial_log.session(operation=operation, port=port) as log_session:
            return transport.reset(capture=capture, on_data=log_session.write), None
    except PatchcordError as error:
        first_error = error
    try:
        reset_result = transport.reset(capture=capture)
    except PatchcordError as retry_error:
        return (
            None,
            DeploymentError(
                "deployment_reset_failed",
                "Patchcord could not confirm that the board resumed normal execution.",
                details={
                    "first_error": first_error.code,
                    "retry_error": retry_error.code,
                },
            ),
        )
    return reset_result, first_error


def _deploy_project(
    project: Project,
    target: SelectedTarget,
    *,
    capture: float,
    allow_boot: bool,
    allow_settings: bool,
) -> tuple[DeployManifest, SerialCapture]:
    if target.drive is None or target.serial is None:
        raise DiscoveryError(
            "target_incomplete",
            "Deployment requires a mount and serial port.",
        )
    drive = target.drive
    serial_port = target.serial.device
    with project_lock(project):
        report = _hardware_report(project)
        if not report.ok or report.document is None:
            raise DeploymentError(
                "project_hardware_invalid",
                "hardware.yaml must pass offline validation before deployment.",
                details={"codes": [diagnostic.code for diagnostic in report.errors]},
            )
        if drive.board_id is None:
            raise DeploymentError(
                "board_identity_unavailable",
                "Deployment requires a board ID from the selected CircuitPython drive.",
                details={"declared": report.document.board.id},
            )
        if drive.board_id != report.document.board.id:
            raise DeploymentError(
                "board_id_mismatch",
                "The connected board does not match hardware.yaml.",
                details={
                    "declared": report.document.board.id,
                    "connected": drive.board_id,
                },
            )
        plan = build_plan(
            project.device_dir,
            drive.mount,
            allow_boot=allow_boot,
            allow_settings=allow_settings,
        )
        transport = SerialTransport(serial_port)
        serial_log = SerialLog(project.serial_log)
        with serial_lock(project, serial_port):
            interrupt_may_have_started = False
            try:
                with serial_log.session(
                    operation="deploy_interrupt",
                    port=serial_port,
                ) as log_session:
                    interrupt_may_have_started = True
                    transport.interrupt(on_data=log_session.write)
                manifest = execute_plan(plan)
            except (Exception, KeyboardInterrupt) as operation_error:
                if interrupt_may_have_started:
                    recovery, recovery_error = _deploy_reset(
                        transport,
                        serial_log,
                        port=serial_port,
                        capture=0,
                        operation="deploy_recovery_reset",
                    )
                    if recovery is None:
                        assert recovery_error is not None
                        raise recovery_error from operation_error
                if isinstance(operation_error, (PatchcordError, KeyboardInterrupt)):
                    raise
                raise DeploymentError(
                    "deployment_execution_failed",
                    "Deployment failed during the board update.",
                ) from operation_error
            startup, startup_error = _deploy_reset(
                transport,
                serial_log,
                port=serial_port,
                capture=capture,
                operation="deploy_startup",
            )
            if startup is None:
                assert startup_error is not None
                raise DeploymentError(
                    "deployment_startup_failed",
                    "Files were copied, but Patchcord could not confirm board startup.",
                    details={
                        "manifest": manifest.as_dict(),
                        "reset_error": startup_error.code,
                    },
                ) from startup_error
            if startup_error is not None:
                raise DeploymentError(
                    "deployment_startup_logging_failed",
                    "The board restarted, but startup evidence could not be logged.",
                    details={
                        "manifest": manifest.as_dict(),
                        "logging_error": startup_error.code,
                    },
                ) from startup_error
    return manifest, startup


@app.command("deploy")
def deploy_command(
    ctx: typer.Context,
    capture: Annotated[
        float,
        typer.Option(
            "--capture",
            min=0,
            help="Seconds of startup output to capture.",
            callback=_finite_float,
        ),
    ] = 5.0,
    allow_boot: Annotated[
        bool,
        typer.Option("--allow-boot", help="Allow writing device/boot.py."),
    ] = False,
    allow_settings: Annotated[
        bool,
        typer.Option("--allow-settings", help="Allow opaque copying of device/settings.toml."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Deploy device/ without deleting unrelated board files."""

    command = "deploy"
    project = _require_project(command, json_output=json_output)
    state = _state(ctx)
    target: SelectedTarget | None = None
    manifest_result: dict[str, Any] | None = None
    try:
        target = select_target(
            mount=state.mount,
            port=state.port,
            legacy_board_id=state.legacy_board_id,
            require_mount=True,
            require_port=True,
        )
        manifest, startup = _deploy_project(
            project,
            target,
            capture=capture,
            allow_boot=allow_boot,
            allow_settings=allow_settings,
        )
        manifest_result = manifest.as_dict()
        result = {
            "manifest": manifest_result,
            "startup_output": startup.text,
            "capture_seconds": capture,
            "traceback_detected": startup.traceback_detected,
        }
        if startup.traceback_detected:
            raise DeploymentError(
                "board_startup_traceback",
                "Deployment completed, but startup output contains an uncaught traceback.",
                details=result,
            )
        record_operation(
            project,
            command=command,
            target=target.public(),
            ok=True,
            result={"manifest": manifest_result, "traceback_detected": False},
            diagnostics={"backend": "patchcord-filesystem"},
        )
    except PatchcordError as error:
        if target is not None:
            failure_result: dict[str, Any] = {"error_code": error.code}
            manifest_evidence: object = manifest_result or error.details.get("manifest")
            if isinstance(manifest_evidence, dict):
                failure_result["manifest"] = manifest_evidence
            with suppress(PatchcordError):
                record_operation(
                    project,
                    command=command,
                    target=target.public(),
                    ok=False,
                    result=failure_result,
                )
        _fail(command, error, json_output=json_output, target=target)

    def human(console: Console) -> None:
        counts = {key: len(value) for key, value in manifest_result.items()}
        console.print(
            "Deployed "
            f"{counts['created']} created, {counts['updated']} updated, "
            f"{counts['skipped']} unchanged."
        )
        if startup.text:
            console.print(startup.text, end="")

    emit_result(
        command,
        result,
        json_output=json_output,
        target=target.public(),
        diagnostics={"backend": "patchcord-filesystem"},
        human=human,
    )


@app.command("monitor")
def monitor_command(
    ctx: typer.Context,
    seconds: Annotated[
        float | None,
        typer.Option(
            "--seconds",
            min=0,
            help="Stop after this many seconds.",
            callback=_finite_optional_float,
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Append raw board output to this file."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one result after monitoring stops."),
    ] = False,
) -> None:
    """Stream serial output until interrupted or the duration expires."""

    command = "monitor"
    if json_output and seconds is None:
        _fail(
            command,
            PatchcordError(
                "bounded_json_required",
                "monitor --json requires --seconds.",
                exit_code=ExitCode.USAGE,
            ),
            json_output=True,
        )
    project = _require_project(command, json_output=json_output)
    state = _state(ctx)
    target: SelectedTarget | None = None
    try:
        target = select_target(
            mount=state.mount,
            port=state.port,
            legacy_board_id=state.legacy_board_id,
            require_port=True,
            strict_mount_ambiguity=False,
        )
        if target.serial is None:
            raise DiscoveryError("serial_port_not_found", "No serial port was selected.")
        transport = SerialTransport(target.serial.device)
        serial_log = SerialLog(project.serial_log)
        output_handle = None
        if output is not None:
            output_path = output.expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_handle = output_path.open("ab")
        try:
            with (
                serial_lock(project, target.serial.device),
                serial_log.session(
                    operation=command,
                    port=target.serial.device,
                ) as log_session,
            ):

                def on_data(data: bytes) -> None:
                    if not json_output:
                        _write_raw_stdout(data)
                    log_session.write(data)
                    if output_handle is not None:
                        output_handle.write(data)
                        output_handle.flush()

                capture_result = transport.monitor(
                    duration=seconds,
                    on_data=on_data,
                    retain_output=json_output,
                )
        finally:
            if output_handle is not None:
                output_handle.close()
    except (OSError, RuntimeError, PatchcordError) as error:
        if isinstance(error, PatchcordError):
            _fail(command, error, json_output=json_output, target=target)
        _fail(
            command,
            PatchcordError(
                "monitor_output_failed",
                "Could not write monitor output.",
                exit_code=ExitCode.TRANSPORT,
            ),
            json_output=json_output,
            target=target,
        )
    if json_output:
        emit_result(
            command,
            {
                "output": capture_result.text,
                "bytes": len(capture_result.raw_output),
                "duration_seconds": capture_result.duration,
                "interrupted": capture_result.interrupted,
            },
            json_output=True,
            target=target.public(),
            diagnostics={"backend": "pyserial"},
        )


@app.command("logs")
def logs_command(
    tail: Annotated[
        int | None,
        typer.Option(
            "--tail",
            min=0,
            max=MAX_TAIL_LINES,
            help="Show the last N log lines.",
        ),
    ] = None,
    since: Annotated[
        str | None,
        typer.Option("--since", help="Show sessions within a duration such as 10m."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Read the persistent host log without opening the board port."""

    command = "logs"
    project = _require_project(command, json_output=json_output)
    if tail is not None and since is not None:
        _fail(
            command,
            PatchcordError(
                "logs_filter_conflict",
                "--tail and --since are mutually exclusive.",
                exit_code=ExitCode.USAGE,
            ),
            json_output=json_output,
        )
    try:
        if since is not None:
            parse_duration(since)
        text = SerialLog(project.serial_log).read(tail=tail, since=since)
    except ValueError as exc:
        _fail(
            command,
            PatchcordError("invalid_duration", str(exc), exit_code=ExitCode.USAGE),
            json_output=json_output,
        )
    except PatchcordError as error:
        _fail(command, error, json_output=json_output)
    result = {"text": text, "line_count": len(text.splitlines())}

    def human(_console: Console) -> None:
        sys.stdout.write(text)

    emit_result(command, result, json_output=json_output, human=human)


@app.command("interrupt")
def interrupt_command(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Send Ctrl-C and return the resulting bounded console output."""

    command = "interrupt"
    project = _require_project(command, json_output=json_output)
    state = _state(ctx)
    target: SelectedTarget | None = None
    try:
        target = select_target(
            mount=state.mount,
            port=state.port,
            legacy_board_id=state.legacy_board_id,
            require_port=True,
            strict_mount_ambiguity=False,
        )
        if target.serial is None:
            raise DiscoveryError("serial_port_not_found", "No serial port was selected.")
        transport = SerialTransport(target.serial.device)
        with (
            serial_lock(project, target.serial.device),
            SerialLog(project.serial_log).session(
                operation=command,
                port=target.serial.device,
            ) as log_session,
        ):
            capture_result = transport.interrupt(on_data=log_session.write)
    except PatchcordError as error:
        _fail(command, error, json_output=json_output, target=target)
    result = {
        "output": capture_result.text,
        "interrupt_sent": True,
        "interrupted": True,
    }

    def human(_console: Console) -> None:
        sys.stdout.write(capture_result.text)

    emit_result(command, result, json_output=json_output, target=target.public(), human=human)


@app.command("reset")
def reset_command(
    ctx: typer.Context,
    capture: Annotated[
        float,
        typer.Option(
            "--capture",
            min=0,
            help="Seconds of startup output to capture.",
            callback=_finite_float,
        ),
    ] = 5.0,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Interrupt, soft-reset, and capture startup output."""

    command = "reset"
    project = _require_project(command, json_output=json_output)
    state = _state(ctx)
    target: SelectedTarget | None = None
    try:
        target = select_target(
            mount=state.mount,
            port=state.port,
            legacy_board_id=state.legacy_board_id,
            require_port=True,
            strict_mount_ambiguity=False,
        )
        if target.serial is None:
            raise DiscoveryError("serial_port_not_found", "No serial port was selected.")
        transport = SerialTransport(target.serial.device)
        with (
            serial_lock(project, target.serial.device),
            SerialLog(project.serial_log).session(
                operation=command,
                port=target.serial.device,
            ) as log_session,
        ):
            capture_result = transport.reset(capture=capture, on_data=log_session.write)
    except PatchcordError as error:
        _fail(command, error, json_output=json_output, target=target)
    result = {
        "output": capture_result.text,
        "capture_seconds": capture,
        "traceback_detected": capture_result.traceback_detected,
    }

    def human(_console: Console) -> None:
        sys.stdout.write(capture_result.text)

    emit_result(command, result, json_output=json_output, target=target.public(), human=human)


@app.command("repl")
def repl_command(
    ctx: typer.Context,
    eval_code: Annotated[
        str | None,
        typer.Option("--eval", help="Execute a bounded CircuitPython snippet."),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option("--file", help="Execute a local script without deploying it."),
    ] = None,
    no_reset: Annotated[
        bool,
        typer.Option("--no-reset", help="Do not reset after bounded execution."),
    ] = False,
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout",
            min=1,
            help="Bounded execution timeout in seconds.",
            callback=_finite_float,
        ),
    ] = 30.0,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Open miniterm or execute explicitly supplied code through circremote."""

    command = "repl"
    project = _require_project(command, json_output=json_output)
    if eval_code is not None and file is not None:
        _fail(
            command,
            PatchcordError(
                "repl_input_conflict",
                "--eval and --file are mutually exclusive.",
                exit_code=ExitCode.USAGE,
            ),
            json_output=json_output,
        )
    interactive = eval_code is None and file is None
    if interactive and json_output:
        _fail(
            command,
            PatchcordError(
                "interactive_json_unsupported",
                "Interactive REPL sessions do not support --json.",
                exit_code=ExitCode.USAGE,
            ),
            json_output=True,
        )
    state = _state(ctx)
    target: SelectedTarget | None = None
    try:
        target = select_target(
            mount=state.mount,
            port=state.port,
            legacy_board_id=state.legacy_board_id,
            require_port=True,
            strict_mount_ambiguity=False,
        )
        if target.serial is None:
            raise DiscoveryError("serial_port_not_found", "No serial port was selected.")
        with serial_lock(project, target.serial.device):
            if interactive:
                record_operation(
                    project,
                    command="repl interactive start",
                    target=target.public(),
                    ok=True,
                )
                try:
                    returncode = run_miniterm(target.serial.device)
                except PatchcordError as error:
                    record_operation(
                        project,
                        command="repl interactive end",
                        target=target.public(),
                        ok=False,
                        result={"returncode": error.details.get("returncode")},
                        diagnostics={"error_code": error.code},
                    )
                    raise
                except KeyboardInterrupt:
                    record_operation(
                        project,
                        command="repl interactive end",
                        target=target.public(),
                        ok=False,
                        result={"returncode": None},
                        diagnostics={"error_code": "user_interrupted"},
                    )
                    raise
                else:
                    record_operation(
                        project,
                        command="repl interactive end",
                        target=target.public(),
                        ok=True,
                        result={"returncode": returncode},
                    )
                return
            if eval_code is not None:
                execution = run_script(
                    target.serial.device,
                    eval_code,
                    timeout=timeout,
                    reset=not no_reset,
                )
            elif file is not None:
                execution = run_file(
                    target.serial.device,
                    file,
                    timeout=timeout,
                    reset=not no_reset,
                )
            else:  # pragma: no cover - guarded by interactive above
                raise BoardExecutionError("repl_input_missing", "No REPL input was selected.")
            SerialLog(project.serial_log).append_session(
                execution.output.encode("utf-8"),
                operation="repl bounded",
                port=target.serial.device,
            )
    except PatchcordError as error:
        _fail(command, error, json_output=json_output, target=target)
    result = {
        "output": execution.output,
        "completed": execution.payload.get("completed", True),
        "reset": not no_reset,
    }

    def human(_console: Console) -> None:
        sys.stdout.write(execution.output)

    emit_result(
        command,
        result,
        json_output=json_output,
        target=target.public(),
        diagnostics={
            "backend": "circremote",
            "backend_version": execution.backend_version,
        },
        human=human,
    )


@probe_app.command("pins")
def probe_pins_command(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """List runtime pin aliases from the connected board module."""

    command = "probe pins"
    project = _require_project(command, json_output=json_output)
    state = _state(ctx)
    target: SelectedTarget | None = None
    try:
        target = select_target(
            mount=state.mount,
            port=state.port,
            legacy_board_id=state.legacy_board_id,
            require_port=True,
            strict_mount_ambiguity=False,
        )
        if target.serial is None:
            raise DiscoveryError("serial_port_not_found", "No serial port was selected.")
        with serial_lock(project, target.serial.device):
            result, execution = probe_pins(target.serial.device)
        SerialLog(project.serial_log).append_session(
            execution.output.encode("utf-8"),
            operation=command,
            port=target.serial.device,
        )
    except PatchcordError as error:
        _fail(command, error, json_output=json_output, target=target)

    def human(console: Console) -> None:
        table = Table("Pin alias", "Physical identity")
        for pin in result["pins"]:
            table.add_row(str(pin["name"]), str(pin["identity"]))
        console.print(table)

    emit_result(
        command,
        result,
        json_output=json_output,
        target=target.public(),
        diagnostics={"backend": "circremote", "backend_version": execution.backend_version},
        human=human,
    )


@probe_app.command("i2c")
def probe_i2c_command(
    ctx: typer.Context,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Scan CircuitPython's default I²C bus."""

    command = "probe i2c"
    project = _require_project(command, json_output=json_output)
    state = _state(ctx)
    target: SelectedTarget | None = None
    try:
        target = select_target(
            mount=state.mount,
            port=state.port,
            legacy_board_id=state.legacy_board_id,
            require_port=True,
            strict_mount_ambiguity=False,
        )
        if target.serial is None:
            raise DiscoveryError("serial_port_not_found", "No serial port was selected.")
        with serial_lock(project, target.serial.device):
            result, execution = probe_i2c(target.serial.device)
        SerialLog(project.serial_log).append_session(
            execution.output.encode("utf-8"),
            operation=command,
            port=target.serial.device,
        )
    except PatchcordError as error:
        _fail(command, error, json_output=json_output, target=target)

    def human(console: Console) -> None:
        if result["addresses_hex"]:
            console.print(" ".join(result["addresses_hex"]))
        else:
            console.print("No I²C addresses found.")

    emit_result(
        command,
        result,
        json_output=json_output,
        target=target.public(),
        diagnostics={"backend": "circremote", "backend_version": execution.backend_version},
        human=human,
    )


@libs_app.command("install")
def libs_install_command(
    ctx: typer.Context,
    packages: Annotated[
        list[str] | None,
        typer.Argument(help="Optional circup bundle package names."),
    ] = None,
    auto: Annotated[
        bool,
        typer.Option("--auto", help="Let circup inspect code.py on the selected board."),
    ] = False,
    py: Annotated[
        bool,
        typer.Option("--py", help="Install source .py libraries instead of compiled .mpy files."),
    ] = False,
    allow_unsupported: Annotated[
        bool,
        typer.Option(
            "--allow-unsupported",
            help="Allow circup to operate on an unsupported CircuitPython release.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Install board libraries through circup."""

    command = "libs install"
    project = _require_project(command, json_output=json_output)
    selected_packages = packages or []
    state = _state(ctx)
    target: SelectedTarget | None = None
    try:
        target = select_target(
            mount=state.mount,
            port=state.port,
            legacy_board_id=state.legacy_board_id,
            require_mount=True,
            strict_port_ambiguity=False,
        )
        if target.drive is None:
            raise DiscoveryError("mount_not_found", "No CircuitPython mount was selected.")
        with project_lock(project):
            upstream = circup.install(
                target.drive.mount,
                project.requirements_file,
                packages=selected_packages,
                auto=auto,
                py=py,
                board_id=target.drive.board_id,
                circuitpython_version=target.drive.circuitpython_version,
                allow_unsupported=allow_unsupported,
            )
    except PatchcordError as error:
        _fail(command, error, json_output=json_output, target=target)
    mode = "auto" if auto else ("packages" if selected_packages else "requirements")
    result = {
        "mode": mode,
        "packages": selected_packages,
        "py": py,
        "allow_unsupported": allow_unsupported,
        "returncode": upstream.returncode,
    }

    def human(console: Console) -> None:
        if upstream.stdout:
            console.print(upstream.stdout, end="")
        if upstream.stderr:
            console.print(upstream.stderr, end="", style="yellow")

    emit_result(
        command,
        result,
        json_output=json_output,
        target=target.public(),
        diagnostics={
            "backend": "circup",
            "backend_version": upstream.backend_version,
        },
        human=human,
    )


@libs_app.command("freeze")
def libs_freeze_command(
    ctx: typer.Context,
    allow_unsupported: Annotated[
        bool,
        typer.Option(
            "--allow-unsupported",
            help="Allow circup to operate on an unsupported CircuitPython release.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Atomically update requirements.txt from the selected board through circup."""

    command = "libs freeze"
    project = _require_project(command, json_output=json_output)
    state = _state(ctx)
    target: SelectedTarget | None = None
    try:
        target = select_target(
            mount=state.mount,
            port=state.port,
            legacy_board_id=state.legacy_board_id,
            require_mount=True,
            strict_port_ambiguity=False,
        )
        if target.drive is None:
            raise DiscoveryError("mount_not_found", "No CircuitPython mount was selected.")
        with project_lock(project):
            upstream = circup.freeze(
                target.drive.mount,
                project.requirements_file,
                board_id=target.drive.board_id,
                circuitpython_version=target.drive.circuitpython_version,
                allow_unsupported=allow_unsupported,
            )
        requirements = project.requirements_file.read_text(encoding="utf-8")
    except (OSError, PatchcordError) as error:
        if isinstance(error, PatchcordError):
            _fail(command, error, json_output=json_output, target=target)
        _fail(
            command,
            ProjectError(
                "requirements_file_unreadable",
                "Could not read the frozen requirements file.",
            ),
            json_output=json_output,
            target=target,
        )
    result = {
        "requirements": requirements.splitlines(),
        "path": str(project.requirements_file),
        "allow_unsupported": allow_unsupported,
        "returncode": upstream.returncode,
    }

    def human(console: Console) -> None:
        console.print(f"Updated {project.requirements_file}")
        if upstream.stdout:
            console.print(upstream.stdout, end="")

    emit_result(
        command,
        result,
        json_output=json_output,
        target=target.public(),
        diagnostics={
            "backend": "circup",
            "backend_version": upstream.backend_version,
        },
        human=human,
    )


@hardware_app.command("validate")
def hardware_validate_command(
    ctx: typer.Context,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Do not connect to or interrupt attached hardware."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit one machine-readable JSON result."),
    ] = False,
) -> None:
    """Validate hardware.yaml offline and, when available, against the board."""

    command = "hardware validate"
    project = _require_project(command, json_output=json_output)
    report = _hardware_report(project)
    target = None
    connected = False
    connected_backend_version: str | None = None
    if report.ok and report.document is not None and not offline:
        state = _state(ctx)
        try:
            target = select_target(
                mount=state.mount,
                port=state.port,
                legacy_board_id=state.legacy_board_id,
            )
        except PatchcordError as error:
            _fail(command, error, json_output=json_output)
        if target.drive is not None and target.serial is not None:
            capability = execution_capability()
            if not capability.available or capability.backend_version is None:
                _fail(
                    command,
                    DependencyError(
                        "execution_backend_unavailable",
                        "Connected validation requires an accepted circremote backend.",
                        details={
                            "reason": capability.reason,
                            "backend_version": capability.backend_version,
                        },
                        diagnostics={
                            "backend": capability.backend,
                            "backend_version": capability.backend_version,
                        },
                    ),
                    json_output=json_output,
                    target=target,
                )
            connected_backend_version = capability.backend_version
            if not json_output:
                typer.echo(
                    "Connected validation will interrupt and reset the selected board.",
                    err=True,
                )
            serial_log = SerialLog(project.serial_log)
            try:
                with (
                    serial_lock(project, target.serial.device),
                    serial_log.session(
                        operation=command,
                        port=target.serial.device,
                    ) as log_session,
                ):
                    validator = ConnectedHardwareValidator(
                        target.serial.device,
                        execution_output=lambda output: log_session.write(output.encode("utf-8")),
                    )
                    try:
                        report = validate_hardware_file(
                            project.hardware_file,
                            project.requirements_file,
                            connected=validator,
                        )
                    finally:
                        # Restore code.py even if the bounded execution backend failed.
                        SerialTransport(target.serial.device).reset(
                            capture=0,
                            on_data=log_session.write,
                        )
            except PatchcordError as error:
                _fail(command, error, json_output=json_output, target=target)
            connected = True
    diagnostics = [item.model_dump(mode="json") for item in report.diagnostics]
    result = {
        "valid": report.ok,
        "connected": connected,
        "diagnostics": diagnostics,
    }
    errors = [
        ErrorRecord(
            code=item.code,
            message=item.message,
            details={"path": item.path, **item.details},
        )
        for item in report.errors
    ]

    def human(console: Console) -> None:
        if not report.diagnostics:
            mode = "offline and connected" if connected else "offline"
            console.print(f"hardware.yaml is valid ({mode}).", style="green")
            return
        for diagnostic in report.diagnostics:
            style = "red" if diagnostic.severity is DiagnosticSeverity.ERROR else "yellow"
            location = f" {diagnostic.path}" if diagnostic.path else ""
            console.print(
                f"{diagnostic.severity.value}: {diagnostic.code}{location}: {diagnostic.message}",
                style=style,
            )

    result_diagnostics: dict[str, Any] = {
        "mode": "connected" if connected else "offline",
    }
    if connected:
        result_diagnostics.update(
            {
                "backend": "circremote",
                "backend_version": connected_backend_version,
            }
        )
    emit_result(
        command,
        result,
        json_output=json_output,
        ok=report.ok,
        errors=errors,
        target=target.public() if target is not None else None,
        diagnostics=result_diagnostics,
        human=human,
    )
    if not report.ok:
        raise typer.Exit(code=int(ExitCode.VALIDATION))


def main() -> None:
    """Console-script entry point."""

    app()
