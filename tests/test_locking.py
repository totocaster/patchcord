from __future__ import annotations

from pathlib import Path

import pytest

from patchcord.errors import ExitCode, PatchcordError
from patchcord.locking import project_lock, serial_lock
from patchcord.project import Project


def test_project_and_serial_locks_create_project_local_files(tmp_path: Path) -> None:
    project = Project(tmp_path)

    with project_lock(project), serial_lock(project, "COM7"):
        lock_names = {path.name for path in project.locks_dir.iterdir()}

    assert "project.lock" in lock_names
    assert any(name.startswith("serial-") for name in lock_names)


def test_lock_directory_permission_failure_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)

    def fail(_project: Project) -> None:
        raise PermissionError("host wording")

    monkeypatch.setattr(Project, "ensure_state", fail)

    with pytest.raises(PatchcordError) as raised, project_lock(project):
        pass

    assert raised.value.code == "project_lock_unavailable"
    assert raised.value.exit_code is ExitCode.LOCKED
    assert "host wording" not in raised.value.message
