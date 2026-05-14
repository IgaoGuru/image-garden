from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from constellation_studio import cli

if TYPE_CHECKING:
    import pytest


def test_pyproject_exposes_image_garden_studio_entry_points() -> None:
    pyproject = Path(__file__).parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data["project"]
    scripts = project["scripts"]

    assert project["name"] == "image-garden-studio"
    assert project["license"] == "GPL-3.0-only"
    assert scripts["image-garden"] == "constellation_studio.cli:main"
    assert scripts["image-garden-studio"] == "constellation_studio.cli:main"
    assert scripts["image-garden-app"] == "constellation_studio.app:main"
    assert scripts["image-garden-backend"] == "constellation_studio.backend:main"
    assert scripts["image-garden-download-onnx"] == (
        "constellation_studio.download_onnx:main"
    )


def test_read_write_running_state_clears_stale_pid(tmp_path: Path) -> None:
    paths = cli.resolve_paths(
        install_root=tmp_path / "app", data_dir=tmp_path / "data"
    )
    state = cli.RuntimeState(
        pid=999_999_999,
        url="http://127.0.0.1:1234/",
        host="127.0.0.1",
        port=1234,
        started_at="2026-01-01T00:00:00+00:00",
        version="test",
        install_root=str(paths.install_root),
        data_dir=str(paths.data_dir),
        log_path=str(paths.log_file),
    )

    cli.write_state(paths, state)

    assert cli.read_state(paths) == state
    assert cli.running_state(paths) is None
    assert not paths.state_file.exists()
    assert not paths.pid_file.exists()


def test_write_cli_shim_points_at_install_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    paths = cli.resolve_paths(
        install_root=tmp_path / "app", data_dir=tmp_path / "data"
    )
    paths.studio_dir.mkdir(parents=True)

    cli.write_cli_shim(paths)

    shim = paths.windows_shim if cli.os.name == "nt" else paths.posix_shim
    text = shim.read_text(encoding="utf-8")
    assert "IMAGE_GARDEN_INSTALL_DIR" in text
    assert str(paths.install_root) in text
    assert "image-garden" in text


def test_doctor_checks_report_required_release_dirs(tmp_path: Path) -> None:
    install = tmp_path / "app"
    (install / "studio").mkdir(parents=True)
    (install / "studio" / "pyproject.toml").write_text("", encoding="utf-8")
    (install / "viewer-dist").mkdir()
    (install / "playview-dist").mkdir()
    paths = cli.resolve_paths(install_root=install, data_dir=tmp_path / "data")

    checks = cli.doctor_checks(paths)
    by_label = {check.label: check for check in checks}

    assert by_label["install root"].status == "ok"
    assert by_label["studio project"].status == "ok"
    assert by_label["viewer assets"].status == "ok"
    assert by_label["playview assets"].status == "ok"


def test_switch_current_release_updates_symlink(tmp_path: Path) -> None:
    base = tmp_path / "install"
    first = base / "releases" / "one"
    second = base / "releases" / "two"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    cli.switch_current_release(base, first)
    assert (base / "current").resolve() == first.resolve()

    cli.switch_current_release(base, second)
    assert (base / "current").resolve() == second.resolve()


def test_runtime_state_json_shape(tmp_path: Path) -> None:
    paths = cli.resolve_paths(
        install_root=tmp_path / "app", data_dir=tmp_path / "data"
    )
    state = cli.RuntimeState(
        pid=1,
        url="http://127.0.0.1:1/",
        host="127.0.0.1",
        port=1,
        started_at="now",
        version="v",
        install_root="root",
        data_dir="data",
        log_path="log",
    )

    cli.write_state(paths, state)
    raw = json.loads(paths.state_file.read_text(encoding="utf-8"))

    assert raw["pid"] == 1
    assert raw["url"] == "http://127.0.0.1:1/"
