"""Project-scoped process and serial-port advisory locks."""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from patchcord.errors import ExitCode, PatchcordError
from patchcord.project import Project


def _safe_lock_name(value: str) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()[:16]
    return f"serial-{digest}.lock"


@contextmanager
def _owned_lock(
    path: Path,
    *,
    timeout: float,
    locked_code: str,
    locked_message: str,
    unavailable_code: str,
    details: dict[str, Any],
) -> Generator[None]:
    lock = FileLock(path)
    try:
        lock.acquire(timeout=timeout)
    except Timeout as exc:
        raise PatchcordError(
            locked_code,
            locked_message,
            exit_code=ExitCode.LOCKED,
            details=details,
        ) from exc
    except OSError as exc:
        raise PatchcordError(
            unavailable_code,
            "Patchcord could not create or acquire the required project lock.",
            exit_code=ExitCode.LOCKED,
            details=details,
        ) from exc
    try:
        yield
    finally:
        active_error = sys.exception()
        try:
            lock.release()
        except OSError as exc:
            if active_error is None:
                raise PatchcordError(
                    "lock_release_failed",
                    "Patchcord could not release a project lock cleanly.",
                    exit_code=ExitCode.LOCKED,
                    details=details,
                ) from exc


@contextmanager
def serial_lock(project: Project, port: str, *, timeout: float = 0) -> Generator[None]:
    """Own one serial port within a Patchcord project."""

    try:
        project.ensure_state()
    except OSError as exc:
        raise PatchcordError(
            "serial_lock_unavailable",
            "Patchcord could not prepare the project lock directory.",
            exit_code=ExitCode.LOCKED,
            details={"port": port, "lock": str(project.locks_dir)},
        ) from exc
    lock_path = project.locks_dir / _safe_lock_name(port)
    with _owned_lock(
        lock_path,
        timeout=timeout,
        locked_code="serial_port_locked",
        locked_message=f"Serial port {port} is already in use by this Patchcord project.",
        unavailable_code="serial_lock_unavailable",
        details={"port": port, "lock": str(lock_path)},
    ):
        yield


@contextmanager
def project_lock(project: Project, *, timeout: float = 0) -> Generator[None]:
    """Serialize state-mutating operations in one project."""

    try:
        project.ensure_state()
    except OSError as exc:
        raise PatchcordError(
            "project_lock_unavailable",
            "Patchcord could not prepare the project lock directory.",
            exit_code=ExitCode.LOCKED,
            details={"lock": str(project.locks_dir)},
        ) from exc
    lock_path = project.locks_dir / "project.lock"
    with _owned_lock(
        lock_path,
        timeout=timeout,
        locked_code="project_locked",
        locked_message="Another Patchcord operation is changing this project.",
        unavailable_code="project_lock_unavailable",
        details={"lock": str(lock_path)},
    ):
        yield
