from __future__ import annotations

import sys

import pytest

from patchcord.adapters import process
from patchcord.errors import DependencyError


def test_run_process_can_remove_inherited_credentials() -> None:
    result = process.run_process(
        [
            sys.executable,
            "-c",
            "import os; print('PATCHCORD_SECRET_TEST' in os.environ)",
        ],
        timeout=5,
        env={"PATCHCORD_SECRET_TEST": "do-not-inherit"},
        remove_env=("PATCHCORD_SECRET_TEST",),
    )

    assert result.returncode == 0
    assert result.stdout == "False\n"


def test_process_spawn_oserror_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("raw host wording")

    monkeypatch.setattr(process.subprocess, "run", fail)

    with pytest.raises(DependencyError) as raised:
        process.run_process(["broken"], timeout=1)

    assert raised.value.code == "dependency_process_failed"
    assert "raw host wording" not in raised.value.message
