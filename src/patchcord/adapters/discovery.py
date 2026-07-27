"""Board drive and serial-port discovery using maintained upstream metadata."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import psutil
from adafruit_board_toolkit import circuitpython_serial

from patchcord.errors import DiscoveryError
from patchcord.models import TargetInfo

_VERSION_RE = re.compile(r"(?:Adafruit )?CircuitPython\s+([^\s;]+)", re.IGNORECASE)
_BOARD_ID_RE = re.compile(r"^\s*Board ID\s*:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
_BOARD_LINE_RE = re.compile(r"^[^;\n]+;\s*(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Drive:
    """One mounted CircuitPython filesystem."""

    mount: Path
    board_id: str | None
    board_name: str | None
    circuitpython_version: str | None
    free_bytes: int | None
    total_bytes: int | None


@dataclass(frozen=True, slots=True)
class SerialPort:
    """One CircuitPython REPL port recognized by Adafruit Board Toolkit."""

    device: str
    description: str | None
    serial_number: str | None
    vid: int | None
    pid: int | None
    location: str | None
    interface: str | None


@dataclass(frozen=True, slots=True)
class SelectedTarget:
    """A target assembled only from explicit or unambiguous candidates."""

    drive: Drive | None
    serial: SerialPort | None

    def public(self) -> TargetInfo:
        return TargetInfo(
            board_id=self.drive.board_id if self.drive else None,
            board_name=self.drive.board_name if self.drive else None,
            circuitpython_version=self.drive.circuitpython_version if self.drive else None,
            mount=str(self.drive.mount) if self.drive else None,
            port=self.serial.device if self.serial else None,
            serial_number=self.serial.serial_number if self.serial else None,
        )


def _parse_boot_out(path: Path) -> tuple[str | None, str | None, str | None]:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None, None
    version_match = _VERSION_RE.search(content)
    id_match = _BOARD_ID_RE.search(content)
    board_match = _BOARD_LINE_RE.search(content)
    return (
        id_match.group(1) if id_match else None,
        board_match.group(1) if board_match else None,
        version_match.group(1) if version_match else None,
    )


def inspect_mount(path: Path) -> Drive:
    """Validate and inspect one explicit CircuitPython mount."""

    try:
        mount = path.expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise DiscoveryError(
            "mount_not_found",
            "The selected mount path could not be resolved.",
            details={"mount": str(path)},
        ) from exc
    if not mount.is_dir():
        raise DiscoveryError(
            "mount_not_found",
            f"The selected mount does not exist or is not a directory: {mount}",
            details={"mount": str(mount)},
        )
    boot_out = mount / "boot_out.txt"
    if not boot_out.is_file() and mount.name.upper() != "CIRCUITPY":
        raise DiscoveryError(
            "not_circuitpython_mount",
            f"The selected mount does not have CircuitPython drive markers: {mount}",
            details={"mount": str(mount)},
        )
    board_id, board_name, version = _parse_boot_out(boot_out)
    try:
        usage = shutil.disk_usage(mount)
        free_bytes: int | None = usage.free
        total_bytes: int | None = usage.total
    except OSError:
        free_bytes = None
        total_bytes = None
    return Drive(mount, board_id, board_name, version, free_bytes, total_bytes)


def list_circuitpython_drives() -> list[Drive]:
    """Enumerate mounted filesystems before checking CircuitPython markers."""

    candidates: dict[Path, Drive] = {}
    try:
        partitions = psutil.disk_partitions(all=False)
    except (OSError, psutil.Error) as exc:
        raise DiscoveryError(
            "drive_discovery_failed",
            "Could not enumerate mounted filesystems.",
        ) from exc
    for partition in partitions:
        mountpoint = Path(partition.mountpoint)
        try:
            resolved = mountpoint.resolve()
            if resolved in candidates:
                continue
            marker = resolved / "boot_out.txt"
            if resolved.name.upper() != "CIRCUITPY" and not marker.is_file():
                continue
            candidates[resolved] = inspect_mount(resolved)
        except (OSError, DiscoveryError):
            continue
    return sorted(candidates.values(), key=lambda drive: str(drive.mount))


def list_repl_ports() -> list[SerialPort]:
    """Return CircuitPython REPL ports from Adafruit Board Toolkit."""

    try:
        upstream_ports = circuitpython_serial.repl_comports()
        ports = [
            SerialPort(
                device=port.device,
                description=port.description,
                serial_number=port.serial_number,
                vid=port.vid,
                pid=port.pid,
                location=port.location,
                interface=port.interface,
            )
            for port in upstream_ports
        ]
    except Exception as exc:
        raise DiscoveryError(
            "serial_discovery_failed",
            "Could not enumerate CircuitPython serial ports.",
        ) from exc
    return sorted(ports, key=lambda item: item.device)


def inspect_port(device: str) -> SerialPort:
    """Resolve an explicit port, trusting the user's override when unrecognized."""

    if not device.strip():
        raise DiscoveryError(
            "invalid_serial_port",
            "Serial port override must not be empty.",
        )
    for port in list_repl_ports():
        if port.device == device:
            return port
    return SerialPort(
        device=device,
        description=None,
        serial_number=None,
        vid=None,
        pid=None,
        location=None,
        interface=None,
    )


def _choose_drive(
    override: Path | None,
    *,
    required: bool,
    strict_ambiguity: bool,
) -> Drive | None:
    if override is not None:
        return inspect_mount(override)
    try:
        drives = list_circuitpython_drives()
    except DiscoveryError:
        if not required and not strict_ambiguity:
            return None
        raise
    if len(drives) == 1:
        return drives[0]
    if len(drives) > 1:
        if not strict_ambiguity and not required:
            return None
        raise DiscoveryError(
            "ambiguous_mount",
            "Multiple CircuitPython drives are connected; select one with --mount.",
            details={"mounts": [str(item.mount) for item in drives]},
        )
    if required:
        raise DiscoveryError(
            "mount_not_found",
            "No CircuitPython drive was found.",
        )
    return None


def _choose_port(
    override: str | None,
    *,
    required: bool,
    strict_ambiguity: bool,
) -> SerialPort | None:
    if override is not None:
        return inspect_port(override)
    try:
        ports = list_repl_ports()
    except DiscoveryError:
        if not required and not strict_ambiguity:
            return None
        raise
    if len(ports) == 1:
        return ports[0]
    if len(ports) > 1:
        if not strict_ambiguity and not required:
            return None
        raise DiscoveryError(
            "ambiguous_serial_port",
            "Multiple CircuitPython REPL ports are connected; select one with --port.",
            details={"ports": [item.device for item in ports]},
        )
    if required:
        raise DiscoveryError(
            "serial_port_not_found",
            "No CircuitPython REPL serial port was found.",
        )
    return None


def select_target(
    *,
    mount: Path | None = None,
    port: str | None = None,
    require_mount: bool = False,
    require_port: bool = False,
    strict_mount_ambiguity: bool = True,
    strict_port_ambiguity: bool = True,
) -> SelectedTarget:
    """Select one target without asking an upstream tool to choose a device."""

    drive = _choose_drive(
        mount,
        required=require_mount,
        strict_ambiguity=strict_mount_ambiguity,
    )
    serial = _choose_port(
        port,
        required=require_port,
        strict_ambiguity=strict_port_ambiguity,
    )
    return SelectedTarget(drive=drive, serial=serial)
