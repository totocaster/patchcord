"""Exercise an installed Patchcord distribution without importing the source tree."""

from __future__ import annotations

import os
import subprocess
import sys
from importlib.metadata import version
from importlib.resources import files

import patchcord


def main() -> None:
    """Check metadata, the CLI entry point, and required package resources."""

    installed_version = version("patchcord")
    expected_version = os.environ.get("EXPECTED_VERSION")
    if expected_version is not None:
        assert installed_version == expected_version
    assert patchcord.__version__ == installed_version

    result = subprocess.run(
        [sys.executable, "-m", "patchcord", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == installed_version
    assert result.stderr == ""

    package = files("patchcord")
    for path in (
        ("resources", "AGENTS.md"),
        ("resources", "hardware-v1.schema.json"),
        ("resources", "probe_i2c.py.txt"),
        ("resources", "probe_pins.py.txt"),
    ):
        resource = package.joinpath(*path)
        assert resource.is_file(), f"missing packaged resource: {'/'.join(path)}"
        assert resource.read_text(encoding="utf-8")


if __name__ == "__main__":
    main()
