from __future__ import annotations

from pathlib import Path

import pytest

from patchcord.adapters import discovery
from patchcord.adapters.discovery import Drive, SerialPort
from patchcord.errors import DiscoveryError


def _drive(path: Path, board_id: str = "test_board") -> Drive:
    return Drive(path, board_id, "Test Board", "9.2.0", 100, 200)


def _port(device: str) -> SerialPort:
    return SerialPort(device, None, None, None, None, None, None)


def test_selects_only_drive_and_port(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    drive = _drive(tmp_path)
    port = _port("/dev/test")
    monkeypatch.setattr(discovery, "list_circuitpython_drives", lambda: [drive])
    monkeypatch.setattr(discovery, "list_repl_ports", lambda: [port])

    selected = discovery.select_target(require_mount=True, require_port=True)

    assert selected.drive == drive
    assert selected.serial == port


def test_ambiguous_mount_requires_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        discovery,
        "list_circuitpython_drives",
        lambda: [_drive(tmp_path / "a"), _drive(tmp_path / "b")],
    )

    with pytest.raises(DiscoveryError) as raised:
        discovery.select_target(require_mount=True)

    assert raised.value.code == "ambiguous_mount"


def test_explicit_unrecognized_port_is_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "list_repl_ports", list)

    selected = discovery.select_target(port="COM99", require_port=True)

    assert selected.serial is not None
    assert selected.serial.device == "COM99"


@pytest.mark.parametrize("device", ["", " ", "\t"])
def test_empty_explicit_port_is_rejected_before_discovery(device: str) -> None:
    with pytest.raises(DiscoveryError) as raised:
        discovery.inspect_port(device)

    assert raised.value.code == "invalid_serial_port"


def test_unresolvable_explicit_mount_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_expanduser(_path: Path) -> Path:
        raise RuntimeError("unknown user")

    monkeypatch.setattr(Path, "expanduser", fail_expanduser)

    with pytest.raises(DiscoveryError) as raised:
        discovery.inspect_mount(Path("~missing-user"))

    assert raised.value.code == "mount_not_found"
    assert "unknown user" not in raised.value.message


def test_optional_ambiguous_side_can_be_omitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected_port = _port("/dev/selected")
    monkeypatch.setattr(
        discovery,
        "list_circuitpython_drives",
        lambda: [_drive(tmp_path / "a"), _drive(tmp_path / "b")],
    )
    monkeypatch.setattr(discovery, "list_repl_ports", lambda: [selected_port])

    selected = discovery.select_target(
        require_port=True,
        strict_mount_ambiguity=False,
    )

    assert selected.drive is None
    assert selected.serial == selected_port


def test_optional_drive_discovery_failure_does_not_block_serial_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_port = _port("/dev/selected")

    def fail_drives() -> list[Drive]:
        raise DiscoveryError("drive_discovery_failed", "Could not enumerate drives.")

    monkeypatch.setattr(discovery, "list_circuitpython_drives", fail_drives)
    monkeypatch.setattr(discovery, "list_repl_ports", lambda: [selected_port])

    selected = discovery.select_target(
        require_port=True,
        strict_mount_ambiguity=False,
    )

    assert selected.drive is None
    assert selected.serial == selected_port


def test_optional_port_discovery_failure_does_not_block_drive_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    selected_drive = _drive(tmp_path)

    def fail_ports() -> list[SerialPort]:
        raise DiscoveryError("serial_discovery_failed", "Could not enumerate ports.")

    monkeypatch.setattr(discovery, "list_circuitpython_drives", lambda: [selected_drive])
    monkeypatch.setattr(discovery, "list_repl_ports", fail_ports)

    selected = discovery.select_target(
        require_mount=True,
        strict_port_ambiguity=False,
    )

    assert selected.drive == selected_drive
    assert selected.serial is None
