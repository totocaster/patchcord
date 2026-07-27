"""Narrow adapter for the documented circup command-line interface."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import cast

import appdirs  # pyright: ignore[reportMissingTypeStubs]

from patchcord.adapters.process import ProcessResult, environment_executable, run_process
from patchcord.errors import DependencyError

_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*"
    r"(?:\s*(?:===|==|~=|!=|>=|<=|>|<)\s*[A-Za-z0-9*+!_.-]+)?"
    r"(?:\s+#.*)?$"
)
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_CREDENTIAL_ENVIRONMENT = (
    "CIRCUP_WEBWORKFLOW_PASSWORD",
    "GH_TOKEN",
    "GITHUB_TOKEN",
)

Runner = Callable[..., ProcessResult]


@dataclass(frozen=True, slots=True)
class CircupCapability:
    """Whether circup can run without user bundle configuration."""

    available: bool
    backend: str
    backend_version: str | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class CircupResult:
    """Normalized result of a successful circup operation."""

    returncode: int
    stdout: str
    stderr: str
    backend_version: str


def _user_bundle_configuration() -> tuple[Path, ...]:
    user_data_dir = cast("Callable[..., str]", appdirs.user_data_dir)
    data_directory = Path(user_data_dir(appname="circup", appauthor="adafruit"))
    return tuple(
        path
        for name in ("bundle_config.json", "bundle_config_local.json")
        if (path := data_directory / name).exists()
    )


def capability() -> CircupCapability:
    """Report whether the documented circup CLI can run under Patchcord policy."""

    executable = environment_executable("circup")
    try:
        backend_version = version("circup")
    except PackageNotFoundError:
        backend_version = None
    if executable is None or backend_version is None:
        return CircupCapability(False, "circup", backend_version, "not_installed")
    if _user_bundle_configuration():
        return CircupCapability(
            False,
            "circup",
            backend_version,
            "user_bundle_configuration_present",
        )
    return CircupCapability(True, "circup", backend_version, None)


def _require_backend() -> tuple[str, str]:
    _assert_no_user_bundle_configuration()
    executable = _executable()
    backend_version = _installed_version()
    if backend_version is None:
        raise DependencyError(
            "circup_unavailable",
            "circup is not installed in the Patchcord environment.",
            diagnostics={"backend": "circup", "backend_version": None},
        )
    return executable, backend_version


def _executable() -> str:
    """Compatibility shim kept small for adapter-level tests."""

    executable = environment_executable("circup")
    if executable is None:
        raise DependencyError(
            "circup_unavailable",
            "circup is not installed in the Patchcord environment.",
            diagnostics={
                "backend": "circup",
                "backend_version": _installed_version(),
            },
        )
    return executable


def _assert_no_user_bundle_configuration() -> None:
    configured = _user_bundle_configuration()
    if configured:
        raise DependencyError(
            "circup_user_configuration_present",
            "Patchcord cannot safely disable the installed circup user bundle configuration.",
            details={"files": [path.name for path in configured]},
            diagnostics={
                "backend": "circup",
                "backend_version": _installed_version(),
            },
        )


def _installed_version() -> str | None:
    try:
        return version("circup")
    except PackageNotFoundError:
        return None


def _with_backend_diagnostics(
    error: DependencyError,
    backend_version: str,
) -> DependencyError:
    diagnostics = dict(error.diagnostics)
    diagnostics.update(
        {
            "backend": "circup",
            "backend_version": backend_version,
        }
    )
    return DependencyError(
        error.code,
        error.message,
        details=error.details,
        diagnostics=diagnostics,
    )


def _run(
    mount: Path,
    arguments: Sequence[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    runner: Runner = run_process,
) -> CircupResult:
    executable, backend_version = _require_backend()
    argv = [
        executable,
        "--path",
        str(mount.resolve()),
        "--timeout",
        str(max(1, int(timeout))),
        *arguments,
    ]
    try:
        if cwd is None:
            with tempfile.TemporaryDirectory(prefix="patchcord-circup-work-") as directory:
                result = runner(
                    argv,
                    timeout=timeout,
                    cwd=Path(directory),
                    remove_env=_CREDENTIAL_ENVIRONMENT,
                )
        else:
            result = runner(
                argv,
                timeout=timeout,
                cwd=cwd,
                remove_env=_CREDENTIAL_ENVIRONMENT,
            )
    except DependencyError as error:
        raise _with_backend_diagnostics(error, backend_version) from error
    if result.returncode != 0:
        raise DependencyError(
            "circup_failed",
            "circup could not complete the requested library operation.",
            details={"returncode": result.returncode},
            diagnostics={
                "backend": "circup",
                "backend_version": backend_version,
                "returncode": result.returncode,
            },
        )
    return CircupResult(result.returncode, result.stdout, result.stderr, backend_version)


def install(
    mount: Path,
    requirements: Path,
    *,
    packages: Sequence[str] = (),
    auto: bool = False,
    timeout: float = 600,
    runner: Runner = run_process,
) -> CircupResult:
    """Install named packages, a requirements file, or circup auto-detection."""

    if packages and auto:
        raise DependencyError(
            "circup_arguments_conflict",
            "Named packages and --auto cannot be used together.",
        )
    invalid_packages = [package for package in packages if not _PACKAGE_RE.fullmatch(package)]
    if invalid_packages:
        raise DependencyError(
            "invalid_circup_package",
            "Library package arguments must be circup bundle names.",
            details={"packages": invalid_packages},
        )
    arguments = ["install"]
    if auto:
        arguments.append("--auto")
    elif packages:
        arguments.extend(["--", *packages])
    else:
        requirement_file = requirements.resolve()
        if not requirement_file.is_file():
            raise DependencyError(
                "requirements_not_found",
                "The project's requirements.txt file does not exist.",
                details={"path": str(requirement_file)},
            )
        try:
            content = requirement_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise DependencyError(
                "invalid_requirements",
                "The project's requirements.txt file is not readable UTF-8 text.",
            ) from exc
        _validate_requirements(
            content,
            code="invalid_requirements",
            message="The project's requirements.txt contains an unsupported entry.",
        )
        arguments.extend(["--requirement", str(requirement_file)])
    return _run(mount, arguments, timeout=timeout, runner=runner)


def _validate_requirements(
    content: str,
    *,
    code: str = "invalid_frozen_requirements",
    message: str = "circup generated an unsupported requirements entry.",
) -> None:
    if "\x00" in content:
        raise DependencyError(
            code,
            message,
        )
    for line_number, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not _REQUIREMENT_RE.fullmatch(stripped):
            raise DependencyError(
                code,
                message,
                details={"line": line_number},
            )


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def freeze(
    mount: Path,
    destination: Path,
    *,
    timeout: float = 600,
    runner: Runner = run_process,
) -> CircupResult:
    """Generate requirements through circup and atomically replace the project file."""

    with tempfile.TemporaryDirectory(prefix="patchcord-circup-") as directory:
        working_directory = Path(directory)
        result = _run(
            mount,
            ["freeze", "--requirement"],
            timeout=timeout,
            cwd=working_directory,
            runner=runner,
        )
        generated = working_directory / "requirements.txt"
        if not generated.is_file():
            raise DependencyError(
                "circup_freeze_output_missing",
                "circup did not generate the expected requirements.txt file.",
            )
        try:
            content = generated.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise DependencyError(
                "invalid_frozen_requirements",
                "circup generated an unreadable requirements file.",
            ) from exc
        _validate_requirements(content)
        _atomic_write(destination, content)
        return result
