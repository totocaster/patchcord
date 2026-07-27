"""Patchcord's public package interface."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("patchcord")
except PackageNotFoundError:  # pragma: no cover - editable installs expose metadata
    __version__ = "0.1.0"

__all__ = ["__version__"]
