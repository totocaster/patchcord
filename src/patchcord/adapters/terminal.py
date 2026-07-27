"""Interactive REPL adapter using pyserial's documented miniterm CLI."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence

from patchcord.errors import TransportError

InteractiveRunner = Callable[[Sequence[str]], int]


def _run_interactive(argv: Sequence[str]) -> int:
    completed = subprocess.run(list(argv), check=False)
    return completed.returncode


def run_miniterm(
    port: str,
    *,
    runner: InteractiveRunner = _run_interactive,
) -> int:
    """Open miniterm without adding a Patchcord terminal implementation."""

    argv = [
        sys.executable,
        "-m",
        "serial.tools.miniterm",
        "--raw",
        "--quiet",
        port,
        "115200",
    ]
    try:
        returncode = runner(argv)
    except OSError as exc:
        raise TransportError(
            "interactive_repl_failed",
            "Could not start pyserial miniterm.",
            details={"port": port},
        ) from exc
    if returncode != 0:
        raise TransportError(
            "interactive_repl_failed",
            "pyserial miniterm exited with an error.",
            details={"port": port, "returncode": returncode},
        )
    return returncode
