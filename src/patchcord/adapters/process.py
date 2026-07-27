"""Bounded subprocess execution shared by documented CLI adapters."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from patchcord.errors import DependencyError


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Captured subprocess streams and status."""

    argv0: str
    returncode: int
    stdout: str
    stderr: str


def environment_executable(name: str) -> str | None:
    """Find a console script installed beside Patchcord's Python or on PATH."""

    suffix = ".exe" if os.name == "nt" else ""
    sibling = Path(sys.executable).with_name(f"{name}{suffix}")
    if sibling.is_file():
        return str(sibling)
    return shutil.which(name)


def run_process(
    argv: Sequence[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    remove_env: Sequence[str] = (),
) -> ProcessResult:
    """Run a bounded non-interactive child without inheriting Patchcord stdout."""

    if not argv:
        raise ValueError("argv must not be empty")
    process_env = os.environ.copy()
    if env is not None:
        process_env.update(env)
    for name in remove_env:
        process_env.pop(name, None)
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=process_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise DependencyError(
            "dependency_executable_not_found",
            f"Required executable is unavailable: {argv[0]}",
            details={"executable": argv[0]},
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DependencyError(
            "dependency_timeout",
            f"Upstream operation did not finish within {timeout:g} seconds.",
            details={"executable": argv[0], "timeout_seconds": timeout},
        ) from exc
    except OSError as exc:
        raise DependencyError(
            "dependency_process_failed",
            "The upstream executable could not be started.",
            details={"executable": argv[0]},
        ) from exc
    return ProcessResult(
        argv0=argv[0],
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
