from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from patchcord.cli import app
from patchcord.hardware.validation import validate_hardware_file
from patchcord.project import find_project, init_project

runner = CliRunner()


def test_init_project_creates_minimal_layout_without_overwriting(tmp_path: Path) -> None:
    existing = tmp_path / "requirements.txt"
    existing.write_text("adafruit_requests\n", encoding="utf-8")

    project, created, preserved = init_project(tmp_path, board_id="test_board")

    assert project.root == tmp_path
    assert project.hardware_file.read_text(encoding="utf-8").find('id: "test_board"') >= 0
    assert existing.read_text(encoding="utf-8") == "adafruit_requests\n"
    assert existing in preserved
    assert project.device_dir / "code.py" in created
    assert project.agents_file in created
    assert ".patchcord/" in (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "device/settings.toml" in (tmp_path / ".gitignore").read_text(encoding="utf-8")


def test_init_project_creates_agent_guide_for_iterative_hardware_work(
    tmp_path: Path,
) -> None:
    project, _, _ = init_project(tmp_path)

    guide = project.agents_file.read_text(encoding="utf-8")

    assert "# Patchcord project guide for agents" in guide
    assert "## Git-backed hardware iteration" in guide
    assert "Git is the durable backup and restore" in guide
    assert "`git revert`" in guide
    assert "patchcord hardware validate --offline --json" in guide
    assert "patchcord deploy --json" in guide
    assert "does not delete unrelated or obsolete files" in guide
    assert "Do not put secrets" in guide


def test_init_project_preserves_existing_agent_guide(tmp_path: Path) -> None:
    existing = tmp_path / "AGENTS.md"
    custom_guide = "# Project-specific agent instructions\n"
    existing.write_text(custom_guide, encoding="utf-8")

    project, created, preserved = init_project(tmp_path)

    assert project.agents_file.read_text(encoding="utf-8") == custom_guide
    assert project.agents_file not in created
    assert project.agents_file in preserved


def test_init_project_is_idempotent(tmp_path: Path) -> None:
    init_project(tmp_path)
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    _, created, _ = init_project(tmp_path, board_id="ignored")

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert created == []
    assert after == before


def test_init_project_yaml_escapes_discovered_board_id(tmp_path: Path) -> None:
    project, _, _ = init_project(tmp_path, board_id='board"id')

    report = validate_hardware_file(project.hardware_file, project.requirements_file)

    assert report.document is not None
    assert report.document.board.id == 'board"id'


def test_find_project_walks_parents(tmp_path: Path) -> None:
    project, _, _ = init_project(tmp_path)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    assert find_project(nested) == project


def test_cli_init_json_is_one_document(tmp_path: Path) -> None:
    project_path = tmp_path / "project"

    result = runner.invoke(app, ["init", str(project_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["command"] == "init"
    assert payload["ok"] is True
    assert payload["result"]["root"] == str(project_path)
    assert "AGENTS.md" in payload["result"]["created"]


def test_cli_init_normalizes_non_utf8_gitignore_without_overwriting_it(tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    gitignore = project_path / ".gitignore"
    existing = b"\xff"
    gitignore.write_bytes(existing)

    result = runner.invoke(app, ["init", str(project_path), "--json"])

    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["errors"][0]["code"] == "project_initialization_failed"
    assert gitignore.read_bytes() == existing
    assert result.stderr == ""


def test_cli_init_normalizes_unresolvable_user_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del tmp_path

    def fail_expanduser(_path: Path) -> Path:
        raise RuntimeError("unknown user")

    monkeypatch.setattr(Path, "expanduser", fail_expanduser)

    result = runner.invoke(app, ["init", "~missing-user/project", "--json"])

    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["errors"][0]["code"] == "project_initialization_failed"
    assert "unknown user" not in result.stdout


def test_cli_init_rejects_an_invalid_explicit_mount(tmp_path: Path) -> None:
    project_path = tmp_path / "project"

    result = runner.invoke(
        app,
        ["--mount", str(tmp_path / "missing"), "init", str(project_path), "--json"],
    )

    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["errors"][0]["code"] == "mount_not_found"
    assert not project_path.exists()
