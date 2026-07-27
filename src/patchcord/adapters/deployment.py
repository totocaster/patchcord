"""Deliberately small, non-deleting mounted-filesystem deployment adapter."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from patchcord.errors import DeploymentError

_PROTECTED = {"boot.py", "settings.toml"}


@dataclass(frozen=True, slots=True)
class DeployEntry:
    """One file in a deployment manifest."""

    path: str
    action: Literal["created", "updated", "skipped"]
    sha256: str | None
    size: int | None


@dataclass(frozen=True, slots=True)
class DeployPlan:
    """A complete copy plan prepared before any board file changes."""

    source: Path
    mount: Path
    files: tuple[Path, ...]
    refused: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeployManifest:
    """Evidence of the files created, updated, skipped, and refused."""

    entries: tuple[DeployEntry, ...]
    refused: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, list[dict[str, str | int | None]]]:
        grouped: dict[str, list[dict[str, str | int | None]]] = {
            "created": [],
            "updated": [],
            "skipped": [],
            "refused": [{"path": path} for path in self.refused],
        }
        for entry in self.entries:
            grouped[entry.action].append(
                {
                    "path": entry.path,
                    "sha256": entry.sha256,
                    "size": entry.size,
                }
            )
        return grouped


def sha256_file(path: Path) -> str:
    """Hash a regular file without loading it wholly into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_safe_source(source: Path, candidate: Path) -> None:
    if candidate.is_symlink():
        raise DeploymentError(
            "unsafe_source_symlink",
            "Deployment refuses symbolic links in device/.",
            details={"path": str(candidate.relative_to(source))},
        )
    try:
        candidate.relative_to(source)
    except ValueError as exc:
        raise DeploymentError(
            "unsafe_source_path",
            "A deployment source path escapes device/.",
            details={"path": str(candidate)},
        ) from exc


def _assert_safe_target(mount: Path, relative: Path) -> Path:
    destination = mount / relative
    current = mount
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise DeploymentError(
                "unsafe_target_symlink",
                "Deployment refuses a target path containing a symbolic link.",
                details={"path": relative.as_posix()},
            )
    try:
        destination.resolve(strict=False).relative_to(mount.resolve())
    except (OSError, ValueError) as exc:
        raise DeploymentError(
            "unsafe_target_path",
            "A deployment target path escapes the selected mount.",
            details={"path": relative.as_posix()},
        ) from exc
    return destination


def _register_casefold_path(paths: dict[str, str], relative: Path) -> None:
    relative_text = relative.as_posix()
    folded = relative_text.casefold()
    first_spelling = paths.get(folded)
    if first_spelling is not None and first_spelling != relative_text:
        raise DeploymentError(
            "case_insensitive_path_collision",
            "Deployment source paths collide on the CircuitPython filesystem.",
            details={"paths": sorted([first_spelling, relative_text])},
        )
    paths[folded] = relative_text


def build_plan(
    source: Path,
    mount: Path,
    *,
    allow_boot: bool = False,
    allow_settings: bool = False,
) -> DeployPlan:
    """Validate the entire deployment tree before a copy begins."""

    source = source.resolve()
    mount = mount.resolve()
    if not source.is_dir():
        raise DeploymentError(
            "device_directory_missing",
            "The project device/ directory does not exist.",
            details={"source": str(source)},
        )
    if not mount.is_dir():
        raise DeploymentError(
            "mount_not_found",
            "The selected CircuitPython mount does not exist.",
            details={"mount": str(mount)},
        )
    code_file = source / "code.py"
    if not code_file.is_file():
        raise DeploymentError(
            "code_file_missing",
            "device/code.py is required for deployment.",
            details={"path": str(code_file)},
        )

    files: list[Path] = []
    refused: list[str] = []
    casefold_paths: dict[str, str] = {}
    for candidate in source.rglob("*"):
        _assert_safe_source(source, candidate)
        relative = candidate.relative_to(source)
        relative_text = relative.as_posix()
        _register_casefold_path(casefold_paths, relative)
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise DeploymentError(
                "unsupported_source_file",
                "Deployment supports regular files only.",
                details={"path": str(candidate.relative_to(source))},
            )
        _assert_safe_target(mount, relative)
        protected_name = relative_text.casefold() if len(relative.parts) == 1 else ""
        if protected_name == "boot.py" and not allow_boot:
            refused.append(relative_text)
            continue
        if protected_name == "settings.toml" and not allow_settings:
            refused.append(relative_text)
            continue
        files.append(relative)

    if refused:
        raise DeploymentError(
            "protected_files_refused",
            "Protected board files require explicit deployment flags.",
            details={"paths": sorted(refused)},
        )

    # A code.py update triggers CircuitPython reload, so it is always copied last.
    files.sort(key=lambda item: (item.as_posix() == "code.py", item.as_posix()))
    return DeployPlan(source=source, mount=mount, files=tuple(files), refused=())


def _copy_and_flush(
    source: Path,
    destination: Path,
    *,
    hash_content: bool = True,
) -> tuple[str | None, int | None]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256() if hash_content else None
    size = 0
    with source.open("rb") as input_handle, destination.open("wb") as output_handle:
        while chunk := input_handle.read(1024 * 1024):
            output_handle.write(chunk)
            if digest is not None:
                digest.update(chunk)
                size += len(chunk)
        output_handle.flush()
        os.fsync(output_handle.fileno())
    if digest is None:
        return None, None
    return digest.hexdigest(), size


def execute_plan(plan: DeployPlan) -> DeployManifest:
    """Execute a validated plan without deleting any target file."""

    entries: list[DeployEntry] = []
    for relative in plan.files:
        source = plan.source / relative
        destination = _assert_safe_target(plan.mount, relative)
        _assert_safe_source(plan.source, source)
        opaque_settings = len(relative.parts) == 1 and relative.name.casefold() == "settings.toml"
        if opaque_settings:
            if destination.is_file():
                opaque_action: Literal["created", "updated"] = "updated"
            elif destination.exists():
                raise DeploymentError(
                    "target_not_regular_file",
                    "A target path exists but is not a regular file.",
                    details={"path": relative.as_posix()},
                )
            else:
                opaque_action = "created"
            try:
                _copy_and_flush(source, destination, hash_content=False)
            except OSError as exc:
                raise DeploymentError(
                    "deployment_copy_failed",
                    "Could not copy the protected settings file to the board.",
                    details={"path": relative.as_posix()},
                ) from exc
            entries.append(DeployEntry(relative.as_posix(), opaque_action, None, None))
            continue
        try:
            source_hash = sha256_file(source)
            source_size = source.stat().st_size
        except OSError as exc:
            raise DeploymentError(
                "source_read_failed",
                f"Could not inspect {relative.as_posix()} in device/.",
                details={"path": relative.as_posix()},
            ) from exc
        if destination.is_file():
            try:
                unchanged = destination.stat().st_size == source_size and (
                    sha256_file(destination) == source_hash
                )
            except OSError as exc:
                raise DeploymentError(
                    "target_read_failed",
                    f"Could not inspect {relative.as_posix()} on the board.",
                    details={"path": relative.as_posix()},
                ) from exc
            if unchanged:
                try:
                    verified_hash = sha256_file(source)
                    verified_size = source.stat().st_size
                except OSError as exc:
                    raise DeploymentError(
                        "source_read_failed",
                        f"Could not recheck {relative.as_posix()} in device/.",
                        details={"path": relative.as_posix()},
                    ) from exc
                if verified_hash != source_hash or verified_size != source_size:
                    raise DeploymentError(
                        "source_changed_during_deploy",
                        "A deployment source file changed while Patchcord was using it.",
                        details={"path": relative.as_posix()},
                    )
                entries.append(
                    DeployEntry(relative.as_posix(), "skipped", source_hash, source_size)
                )
                continue
            action: Literal["created", "updated"] = "updated"
        elif destination.exists():
            raise DeploymentError(
                "target_not_regular_file",
                "A target path exists but is not a regular file.",
                details={"path": relative.as_posix()},
            )
        else:
            action = "created"
        try:
            copied_hash, copied_size = _copy_and_flush(source, destination)
        except OSError as exc:
            raise DeploymentError(
                "deployment_copy_failed",
                f"Could not copy {relative.as_posix()} to the board.",
                details={"path": relative.as_posix()},
            ) from exc
        entries.append(DeployEntry(relative.as_posix(), action, copied_hash, copied_size))
    return DeployManifest(entries=tuple(entries), refused=plan.refused)
