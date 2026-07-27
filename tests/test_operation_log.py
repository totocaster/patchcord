from __future__ import annotations

import json
from pathlib import Path

import pytest

from patchcord.errors import PatchcordError
from patchcord.models import TargetInfo
from patchcord.operation_log import record_operation
from patchcord.project import Project


def test_operation_log_redacts_sensitive_fields_and_omits_board_output(tmp_path: Path) -> None:
    project = Project(tmp_path)

    record_operation(
        project,
        command="deploy",
        target=TargetInfo(
            board_id="pyportal_titano",
            board_id_source="legacy_override",
            mount="/Volumes/CIRCUITPY",
        ),
        ok=True,
        result={"settings_path": "settings.toml", "created": ["code.py"]},
        diagnostics={"api_token": "do-not-log"},
    )

    payload = json.loads(project.operations_log.read_text(encoding="utf-8"))
    assert payload["result"]["settings_path"] == "[redacted]"
    assert payload["diagnostics"]["api_token"] == "[redacted]"
    assert payload["result"]["created"] == ["code.py"]
    assert payload["target"]["board_id_source"] == "legacy_override"


def test_operation_log_filesystem_failure_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)

    def fail(_project: Project) -> None:
        raise PermissionError("host wording")

    monkeypatch.setattr(Project, "ensure_state", fail)

    with pytest.raises(PatchcordError) as raised:
        record_operation(
            project,
            command="deploy",
            target=TargetInfo(),
            ok=False,
        )

    assert raised.value.code == "operation_log_write_failed"
    assert "host wording" not in raised.value.message
