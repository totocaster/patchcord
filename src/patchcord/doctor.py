"""Read-only host, dependency, project, and target diagnostics."""

from __future__ import annotations

import os
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from patchcord import __version__
from patchcord.adapters.circup import capability as circup_capability
from patchcord.adapters.discovery import list_circuitpython_drives, list_repl_ports
from patchcord.adapters.execution import capability as execution_capability
from patchcord.errors import DiscoveryError
from patchcord.hardware.validation import validate_hardware_file
from patchcord.project import Project

_DISTRIBUTIONS = {
    "adafruit-board-toolkit": "adafruit-board-toolkit",
    "circup": "circup",
    "filelock": "filelock",
    "psutil": "psutil",
    "pydantic": "pydantic",
    "pyserial": "pyserial",
    "rich": "rich",
    "ruamel.yaml": "ruamel-yaml",
    "shellingham": "shellingham",
    "typer": "typer",
    "circremote": "circremote",
}
_SERIAL_PORTS_ARE_FILESYSTEM_PATHS = os.name != "nt"


def _distribution_version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def _serial_permissions(device: str) -> tuple[bool | None, bool | None]:
    if not _SERIAL_PORTS_ARE_FILESYSTEM_PATHS:
        return None, None
    return os.access(device, os.R_OK), os.access(device, os.W_OK)


def collect_doctor(project: Project | None) -> dict[str, Any]:
    """Collect diagnostics without opening a serial port or changing state."""

    dependencies = {
        name: _distribution_version(distribution) for name, distribution in _DISTRIBUTIONS.items()
    }
    discovery_errors: list[dict[str, str]] = []
    try:
        drives = list_circuitpython_drives()
    except DiscoveryError as error:
        drives = []
        discovery_errors.append({"code": error.code, "message": error.message})
    try:
        ports = list_repl_ports()
    except DiscoveryError as error:
        ports = []
        discovery_errors.append({"code": error.code, "message": error.message})
    execution = execution_capability()
    libraries = circup_capability()
    bounded_available = execution.available
    capabilities = {
        "discovery": {
            "available": not discovery_errors,
            "backend": "adafruit-board-toolkit+psutil",
            "reason": discovery_errors[0]["code"] if discovery_errors else None,
        },
        "deployment": {"available": True, "backend": "patchcord-filesystem"},
        "serial_control": {"available": True, "backend": "pyserial"},
        "interactive_repl": {"available": True, "backend": "serial.tools.miniterm"},
        "bounded_execution": {
            "available": bounded_available,
            "backend": execution.backend,
            "backend_version": execution.backend_version,
            "reason": execution.reason,
        },
        "hardware_offline_validation": {"available": True, "backend": "pydantic+ruamel.yaml"},
        "hardware_connected_validation": {
            "available": bounded_available,
            "backend": execution.backend,
            "backend_version": execution.backend_version,
            "reason": execution.reason,
        },
        "libraries": {
            "available": libraries.available,
            "availability_scope": "backend",
            "backend": libraries.backend,
            "backend_version": libraries.backend_version,
            "reason": libraries.reason,
            "target_compatibility": "unchecked",
            "target_reason": "no_read_only_upstream_check",
        },
    }
    project_record: dict[str, Any]
    if project is None:
        project_record = {"found": False, "valid": False, "root": None}
    else:
        required = {
            "device": project.device_dir.is_dir(),
            "device/code.py": (project.device_dir / "code.py").is_file(),
            "hardware.yaml": project.hardware_file.is_file(),
            "requirements.txt": project.requirements_file.is_file(),
        }
        validation = (
            validate_hardware_file(project.hardware_file, project.requirements_file)
            if required["hardware.yaml"]
            else None
        )
        project_record = {
            "found": True,
            "valid": all(required.values()) and validation is not None and validation.ok,
            "root": str(project.root),
            "files": required,
            "validation_codes": (
                [diagnostic.code for diagnostic in validation.diagnostics]
                if validation is not None
                else []
            ),
        }
    return {
        "versions": {
            "patchcord": __version__,
            "python": platform.python_version(),
            "platform": sys.platform,
            "dependencies": dependencies,
        },
        "capabilities": capabilities,
        "project": project_record,
        "discovery_errors": discovery_errors,
        "drives": [
            {
                "mount": str(drive.mount),
                "board_id": drive.board_id,
                "board_id_source": drive.board_id_source,
                "circuitpython_version": drive.circuitpython_version,
                "readable": os.access(drive.mount, os.R_OK),
                "writable": os.access(drive.mount, os.W_OK),
            }
            for drive in drives
        ],
        "serial_ports": [
            {
                "port": port.device,
                "serial_number": port.serial_number,
                "interface": port.interface,
                "readable": permissions[0],
                "writable": permissions[1],
            }
            for port in ports
            for permissions in [_serial_permissions(port.device)]
        ],
    }
