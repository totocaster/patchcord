from __future__ import annotations

from pathlib import Path

import pytest

from patchcord.adapters.deployment import (
    _register_casefold_path,  # pyright: ignore[reportPrivateUsage]
    build_plan,
    execute_plan,
)
from patchcord.errors import DeploymentError


def _device(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "device"
    mount = tmp_path / "CIRCUITPY"
    source.mkdir()
    mount.mkdir()
    (source / "code.py").write_text("print('new')\n", encoding="utf-8")
    return source, mount


def test_deploy_copies_support_files_before_code_and_never_deletes(tmp_path: Path) -> None:
    source, mount = _device(tmp_path)
    (source / "lib.py").write_text("VALUE = 1\n", encoding="utf-8")
    (mount / "unrelated.txt").write_text("keep", encoding="utf-8")

    plan = build_plan(source, mount)
    manifest = execute_plan(plan)

    assert [entry.path for entry in manifest.entries] == ["lib.py", "code.py"]
    assert (mount / "code.py").read_text(encoding="utf-8") == "print('new')\n"
    assert (mount / "unrelated.txt").read_text(encoding="utf-8") == "keep"


def test_deploy_reports_skipped_files(tmp_path: Path) -> None:
    source, mount = _device(tmp_path)
    (mount / "code.py").write_bytes((source / "code.py").read_bytes())

    manifest = execute_plan(build_plan(source, mount))

    assert manifest.entries[0].action == "skipped"


@pytest.mark.parametrize(
    ("name", "flag"),
    [
        ("boot.py", "allow_boot"),
        ("BOOT.PY", "allow_boot"),
        ("settings.toml", "allow_settings"),
        ("SETTINGS.TOML", "allow_settings"),
    ],
)
def test_protected_files_require_exact_flag(tmp_path: Path, name: str, flag: str) -> None:
    source, mount = _device(tmp_path)
    (source / name).write_text("secret = 'value'\n", encoding="utf-8")

    with pytest.raises(DeploymentError) as raised:
        build_plan(source, mount)

    assert raised.value.code == "protected_files_refused"
    plan = build_plan(source, mount, **{flag: True})
    execute_plan(plan)
    assert (mount / name).exists()


def test_settings_copy_is_opaque_and_never_publishes_a_digest(tmp_path: Path) -> None:
    source, mount = _device(tmp_path)
    (source / "settings.toml").write_text('WIFI_PASSWORD = "low-entropy"\n', encoding="utf-8")
    (mount / "settings.toml").write_text('WIFI_PASSWORD = "old"\n', encoding="utf-8")

    manifest = execute_plan(build_plan(source, mount, allow_settings=True))
    entry = next(item for item in manifest.entries if item.path == "settings.toml")

    assert entry.action == "updated"
    assert entry.sha256 is None
    assert entry.size is None


def test_source_symlink_is_refused(tmp_path: Path) -> None:
    source, mount = _device(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("danger", encoding="utf-8")
    (source / "linked.py").symlink_to(outside)

    with pytest.raises(DeploymentError) as raised:
        build_plan(source, mount)

    assert raised.value.code == "unsafe_source_symlink"


def test_target_symlink_is_refused(tmp_path: Path) -> None:
    source, mount = _device(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (mount / "lib").symlink_to(outside, target_is_directory=True)
    (source / "lib").mkdir()
    (source / "lib" / "module.py").write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(DeploymentError) as raised:
        build_plan(source, mount)

    assert raised.value.code == "unsafe_target_symlink"


def test_case_insensitive_source_collision_is_refused() -> None:
    paths: dict[str, str] = {}
    _register_casefold_path(paths, Path("Helper.py"))

    with pytest.raises(DeploymentError) as raised:
        _register_casefold_path(paths, Path("helper.py"))

    assert raised.value.code == "case_insensitive_path_collision"
