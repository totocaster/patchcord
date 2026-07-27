from __future__ import annotations

from pathlib import Path

import pytest

from patchcord import doctor
from patchcord.adapters.circup import CircupCapability
from patchcord.adapters.discovery import SerialPort
from patchcord.adapters.execution import ExecutionCapability
from patchcord.errors import DiscoveryError
from patchcord.project import Project


def test_doctor_reports_backend_versions_and_serial_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    port_path = tmp_path / "serial"
    port_path.write_bytes(b"")
    monkeypatch.setattr(doctor, "_SERIAL_PORTS_ARE_FILESYSTEM_PATHS", True)
    monkeypatch.setattr(doctor, "list_circuitpython_drives", list)
    monkeypatch.setattr(
        doctor,
        "list_repl_ports",
        lambda: [SerialPort(str(port_path), None, None, None, None, None, None)],
    )
    monkeypatch.setattr(
        doctor,
        "execution_capability",
        lambda: ExecutionCapability(False, "circremote", "0.12.0", "rejected"),
    )
    monkeypatch.setattr(
        doctor,
        "circup_capability",
        lambda: CircupCapability(True, "circup", "3.0.4", None),
    )

    result = doctor.collect_doctor(None)

    assert result["capabilities"]["libraries"] == {
        "available": True,
        "availability_scope": "backend",
        "backend": "circup",
        "backend_version": "3.0.4",
        "reason": None,
        "target_compatibility": "unchecked",
        "target_reason": "no_read_only_upstream_check",
    }
    assert result["serial_ports"][0]["readable"] is True
    assert result["serial_ports"][0]["writable"] is True


def test_doctor_keeps_partial_results_when_discovery_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_drives() -> list[object]:
        raise DiscoveryError("drive_discovery_failed", "Permission denied.")

    monkeypatch.setattr(doctor, "list_circuitpython_drives", fail_drives)
    monkeypatch.setattr(doctor, "list_repl_ports", list)
    monkeypatch.setattr(
        doctor,
        "execution_capability",
        lambda: ExecutionCapability(False, "circremote", "0.12.0", "rejected"),
    )
    monkeypatch.setattr(
        doctor,
        "circup_capability",
        lambda: CircupCapability(True, "circup", "3.0.4", None),
    )

    result = doctor.collect_doctor(None)

    assert result["capabilities"]["discovery"]["available"] is False
    assert result["discovery_errors"][0]["code"] == "drive_discovery_failed"


def test_doctor_does_not_report_false_filesystem_permissions_for_windows_com_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor, "_SERIAL_PORTS_ARE_FILESYSTEM_PATHS", False)
    monkeypatch.setattr(doctor, "list_circuitpython_drives", list)
    monkeypatch.setattr(
        doctor,
        "list_repl_ports",
        lambda: [SerialPort("COM7", None, None, None, None, None, None)],
    )

    result = doctor.collect_doctor(None)

    assert result["serial_ports"][0]["readable"] is None
    assert result["serial_ports"][0]["writable"] is None


def test_doctor_requires_device_code_file_for_a_valid_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor, "list_circuitpython_drives", list)
    monkeypatch.setattr(doctor, "list_repl_ports", list)
    project = Project(tmp_path)
    project.device_dir.mkdir()
    project.hardware_file.write_text(
        "schema_version: 1\nboard:\n  id: test_board\nparts: {}\nnets: {}\n",
        encoding="utf-8",
        newline="\n",
    )
    project.requirements_file.write_text("", encoding="utf-8")

    result = doctor.collect_doctor(project)

    assert result["project"]["files"]["device"] is True
    assert result["project"]["files"]["device/code.py"] is False
    assert result["project"]["valid"] is False
