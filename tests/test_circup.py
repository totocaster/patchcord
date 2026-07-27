from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from patchcord.adapters import circup
from patchcord.adapters.process import ProcessResult
from patchcord.errors import DependencyError


@pytest.fixture(autouse=True)
def isolated_adapter_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(circup, "_user_bundle_configuration", lambda: ())
    monkeypatch.setattr(circup, "_installed_version", lambda: "3.0.4")


class RecordingRunner:
    def __init__(self, *, frozen: str | None = None, returncode: int = 0) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.frozen = frozen
        self.returncode = returncode

    def __call__(self, argv: Sequence[str], **kwargs: Any) -> ProcessResult:
        self.calls.append((list(argv), kwargs))
        cwd = kwargs.get("cwd")
        if self.frozen is not None and isinstance(cwd, Path):
            (cwd / "requirements.txt").write_text(self.frozen, encoding="utf-8")
        return ProcessResult(argv[0], self.returncode, "progress", "")


def test_install_uses_explicit_mount_and_absolute_requirements(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    mount = tmp_path / "CIRCUITPY"
    mount.mkdir()
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("adafruit_requests\n", encoding="utf-8")
    runner = RecordingRunner()
    monkeypatch.setattr(circup, "_executable", lambda: "/venv/bin/circup")

    circup.install(mount, requirements, runner=runner)

    argv = runner.calls[0][0]
    assert argv == [
        "/venv/bin/circup",
        "--path",
        str(mount),
        "--timeout",
        "600",
        "install",
        "--requirement",
        str(requirements),
    ]
    cwd = runner.calls[0][1]["cwd"]
    assert isinstance(cwd, Path)
    assert cwd != tmp_path
    assert "CIRCUP_WEBWORKFLOW_PASSWORD" in runner.calls[0][1]["remove_env"]


def test_auto_and_packages_conflict(tmp_path: Path) -> None:
    with pytest.raises(DependencyError) as raised:
        circup.install(tmp_path, tmp_path / "requirements.txt", packages=["x"], auto=True)

    assert raised.value.code == "circup_arguments_conflict"


def test_named_packages_are_separated_from_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    monkeypatch.setattr(circup, "_executable", lambda: "/venv/bin/circup")

    circup.install(
        tmp_path,
        tmp_path / "requirements.txt",
        packages=["adafruit_requests"],
        runner=runner,
    )

    assert runner.calls[0][0][-3:] == ["install", "--", "adafruit_requests"]


@pytest.mark.parametrize("package", ["--upgrade", "../module.py", "https://example.invalid/x"])
def test_package_arguments_cannot_inject_options_or_paths(
    tmp_path: Path,
    package: str,
) -> None:
    with pytest.raises(DependencyError) as raised:
        circup.install(
            tmp_path,
            tmp_path / "requirements.txt",
            packages=[package],
        )

    assert raised.value.code == "invalid_circup_package"


@pytest.mark.parametrize(
    "content",
    [
        "--index-url https://example.invalid\n",
        "../local_module.py\n",
        "https://example.invalid/module.py\n",
    ],
)
def test_project_requirements_reject_options_urls_and_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: str,
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(content, encoding="utf-8")
    monkeypatch.setattr(circup, "_executable", lambda: "/venv/bin/circup")

    with pytest.raises(DependencyError) as raised:
        circup.install(tmp_path, requirements, runner=RecordingRunner())

    assert raised.value.code == "invalid_requirements"


def test_freeze_atomically_replaces_requirements(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    mount = tmp_path / "CIRCUITPY"
    mount.mkdir()
    destination = tmp_path / "requirements.txt"
    destination.write_text("old\n", encoding="utf-8")
    runner = RecordingRunner(frozen="adafruit_bus_device==5.2.10\n")
    monkeypatch.setattr(circup, "_executable", lambda: "/venv/bin/circup")

    circup.freeze(mount, destination, runner=runner)

    assert destination.read_text(encoding="utf-8") == "adafruit_bus_device==5.2.10\n"
    assert runner.calls[0][0][-2:] == ["freeze", "--requirement"]


def test_freeze_preserves_original_on_invalid_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    mount = tmp_path / "CIRCUITPY"
    mount.mkdir()
    destination = tmp_path / "requirements.txt"
    destination.write_text("old\n", encoding="utf-8")
    runner = RecordingRunner(frozen="--index-url https://example.invalid\n")
    monkeypatch.setattr(circup, "_executable", lambda: "/venv/bin/circup")

    with pytest.raises(DependencyError):
        circup.freeze(mount, destination, runner=runner)

    assert destination.read_text(encoding="utf-8") == "old\n"


def test_user_bundle_configuration_blocks_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        circup,
        "_user_bundle_configuration",
        lambda: (tmp_path / "bundle_config.json",),
    )

    with pytest.raises(DependencyError) as raised:
        circup.install(
            tmp_path,
            tmp_path / "requirements.txt",
            packages=["adafruit_requests"],
            runner=RecordingRunner(),
        )

    assert raised.value.code == "circup_user_configuration_present"
    assert raised.value.details == {"files": ["bundle_config.json"]}


def test_process_error_keeps_circup_adapter_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(circup, "_executable", lambda: "/venv/bin/circup")

    def runner(_argv: Sequence[str], **_kwargs: Any) -> ProcessResult:
        raise DependencyError(
            "dependency_timeout",
            "Upstream operation timed out.",
            details={"timeout_seconds": 600},
        )

    with pytest.raises(DependencyError) as raised:
        circup.install(
            tmp_path,
            tmp_path / "requirements.txt",
            packages=["adafruit_requests"],
            runner=runner,
        )

    assert raised.value.code == "dependency_timeout"
    assert raised.value.details == {"timeout_seconds": 600}
    assert raised.value.diagnostics == {
        "backend": "circup",
        "backend_version": "3.0.4",
    }
