"""Patchcord project discovery, initialization, and durable local paths."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from patchcord.errors import ProjectError

PROJECT_MARKERS = ("hardware.yaml", "requirements.txt", "device")
GITIGNORE_ENTRIES = (".patchcord/", "device/settings.toml")

MINIMAL_CODE = '''"""CircuitPython application entry point."""\n'''

MINIMAL_HARDWARE = """\
schema_version: 1

# Official CircuitPython board.board_id. `patchcord init` fills this when possible.
board:
  id: ""

# Add project-local parts and logical electrical nets here.
parts: {}
nets: {}

notes: |
  Describe project-specific assembly notes here.
"""


@dataclass(frozen=True, slots=True)
class Project:
    """Resolved paths belonging to one Patchcord project."""

    root: Path

    @property
    def device_dir(self) -> Path:
        return self.root / "device"

    @property
    def hardware_file(self) -> Path:
        return self.root / "hardware.yaml"

    @property
    def requirements_file(self) -> Path:
        return self.root / "requirements.txt"

    @property
    def state_dir(self) -> Path:
        return self.root / ".patchcord"

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def locks_dir(self) -> Path:
        return self.state_dir / "locks"

    @property
    def serial_log(self) -> Path:
        return self.logs_dir / "serial.log"

    @property
    def operations_log(self) -> Path:
        return self.logs_dir / "operations.jsonl"

    def ensure_state(self) -> None:
        """Create gitignored runtime directories when an operation needs them."""

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)


def find_project(start: Path | None = None) -> Project:
    """Find the nearest Patchcord project at or above *start*."""

    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if all((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return Project(candidate)
    raise ProjectError(
        "project_not_found",
        "No Patchcord project was found in this directory or its parents.",
        details={"start": os.fspath(current)},
    )


def find_project_candidate(start: Path | None = None) -> Project | None:
    """Find a partial project so ``doctor`` can report missing required files."""

    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return Project(candidate)
    return None


def init_project(path: Path, *, board_id: str = "") -> tuple[Project, list[Path], list[Path]]:
    """Create missing project files, preserving every existing file."""

    root = path.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    project = Project(root)
    created: list[Path] = []
    preserved: list[Path] = []

    project.device_dir.mkdir(parents=True, exist_ok=True)
    code_file = project.device_dir / "code.py"
    files = {
        code_file: MINIMAL_CODE,
        project.hardware_file: MINIMAL_HARDWARE.replace(
            'id: ""',
            f"id: {json.dumps(board_id, ensure_ascii=False)}",
            1,
        ),
        project.requirements_file: "",
    }
    for file_path, content in files.items():
        if file_path.exists():
            preserved.append(file_path)
            continue
        file_path.write_text(content, encoding="utf-8", newline="\n")
        created.append(file_path)

    gitignore = root / ".gitignore"
    if gitignore.exists():
        existing = gitignore.read_text(encoding="utf-8").splitlines()
        preserved.append(gitignore)
    else:
        existing = []
        created.append(gitignore)
    missing_entries = [entry for entry in GITIGNORE_ENTRIES if entry not in existing]
    if missing_entries:
        prefix = "\n" if existing and gitignore.read_text(encoding="utf-8").strip() else ""
        with gitignore.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(prefix)
            handle.write("# Patchcord local state and secrets\n")
            handle.writelines(f"{entry}\n" for entry in missing_entries)

    return project, created, preserved
