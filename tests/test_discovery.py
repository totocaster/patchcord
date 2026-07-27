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


def test_inspect_mount_parses_modern_boot_out_board_id(tmp_path: Path) -> None:
    (tmp_path / "boot_out.txt").write_text(
        "Adafruit CircuitPython 10.2.1 on 2026-05-12; Test Board with test_mcu\n"
        "Board ID:test_board\n",
        encoding="utf-8",
    )

    drive = discovery.inspect_mount(tmp_path)

    assert drive.board_id == "test_board"
    assert drive.board_id_source == "boot_out"
    assert drive.board_name == "Test Board with test_mcu"
    assert drive.circuitpython_version == "10.2.1"


def test_inspect_mount_does_not_invent_id_for_legacy_boot_out(tmp_path: Path) -> None:
    (tmp_path / "boot_out.txt").write_text(
        "Adafruit CircuitPython 5.0.0-beta.0 on 2019-11-19; "
        "Adafruit PyPortal Titano with samd51j20\n",
        encoding="utf-8",
    )

    drive = discovery.inspect_mount(tmp_path)

    assert drive.board_id is None
    assert drive.board_id_source is None
    assert drive.board_name == "Adafruit PyPortal Titano with samd51j20"
    assert drive.circuitpython_version == "5.0.0-beta.0"


def test_inspect_mount_does_not_combine_unrelated_partial_banner_lines(
    tmp_path: Path,
) -> None:
    (tmp_path / "boot_out.txt").write_text(
        "CircuitPython 5.0.0\nunrelated text; Not a board banner\nBoard ID:not_trusted\n",
        encoding="utf-8",
    )

    drive = discovery.inspect_mount(tmp_path)

    assert drive.board_id is None
    assert drive.board_id_source is None
    assert drive.board_name is None
    assert drive.circuitpython_version is None


def test_legacy_board_id_can_be_asserted_for_explicit_legacy_mount(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "boot_out.txt").write_text(
        "Adafruit CircuitPython 5.0.0-beta.0; Adafruit PyPortal Titano with samd51j20\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(discovery, "list_repl_ports", list)

    selected = discovery.select_target(
        mount=tmp_path,
        legacy_board_id="pyportal_titano",
        require_mount=True,
    )

    assert selected.drive is not None
    assert selected.drive.board_id == "pyportal_titano"
    assert selected.drive.board_id_source == "legacy_override"
    assert selected.public().board_id_source == "legacy_override"


def test_legacy_board_id_requires_explicit_mount(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(discovery, "list_circuitpython_drives", lambda: [_drive(tmp_path)])

    with pytest.raises(DiscoveryError) as raised:
        discovery.select_target(legacy_board_id="test_board")

    assert raised.value.code == "legacy_board_id_requires_mount"


@pytest.mark.parametrize("board_id", ["", " ", "two words", "test\tboard"])
def test_legacy_board_id_must_be_one_nonempty_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    board_id: str,
) -> None:
    def inspect(_path: Path) -> Drive:
        return _drive(tmp_path, "")

    monkeypatch.setattr(discovery, "inspect_mount", inspect)

    with pytest.raises(DiscoveryError) as raised:
        discovery.select_target(mount=tmp_path, legacy_board_id=board_id)

    assert raised.value.code == "invalid_legacy_board_id"


def test_legacy_board_id_cannot_replace_published_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    published = _drive(tmp_path, "published_board")

    def inspect(_path: Path) -> Drive:
        return published

    monkeypatch.setattr(discovery, "inspect_mount", inspect)

    with pytest.raises(DiscoveryError) as raised:
        discovery.select_target(mount=tmp_path, legacy_board_id="different_board")

    assert raised.value.code == "legacy_board_id_mismatch"
    assert raised.value.details == {
        "override": "different_board",
        "connected": "published_board",
    }


@pytest.mark.parametrize("boot_out", [None, "not a CircuitPython boot banner\n"])
def test_legacy_board_id_requires_parseable_boot_banner(
    tmp_path: Path,
    boot_out: str | None,
) -> None:
    mount = tmp_path / "CIRCUITPY"
    mount.mkdir()
    if boot_out is not None:
        (mount / "boot_out.txt").write_text(boot_out, encoding="utf-8")

    with pytest.raises(DiscoveryError) as raised:
        discovery.select_target(mount=mount, legacy_board_id="test_board")

    assert raised.value.code == "legacy_board_evidence_unavailable"
    assert raised.value.details == {"mount": str(mount)}


def test_legacy_board_id_rejects_unreadable_boot_banner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mount = tmp_path / "CIRCUITPY"
    mount.mkdir()
    boot_out = mount / "boot_out.txt"
    boot_out.write_text("placeholder\n", encoding="utf-8")
    original_read_text = Path.read_text

    def unreadable(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == boot_out:
            raise PermissionError
        return original_read_text(
            path,
            encoding=encoding,
            errors=errors,
        )

    monkeypatch.setattr(Path, "read_text", unreadable)

    with pytest.raises(DiscoveryError) as raised:
        discovery.select_target(mount=mount, legacy_board_id="test_board")

    assert raised.value.code == "legacy_board_evidence_unavailable"


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
