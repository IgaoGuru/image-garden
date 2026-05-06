#!/usr/bin/env python3
"""Friendly Constellation installer TUI.

This file is intentionally stdlib-only. Bootstrap scripts run it with:

    uv run --no-project --python 3.13 scripts/install_tui.py

That lets brand-new machines get a real installer flow before heavyweight app
wheels are installed.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

APP_NAME = "Constellation"
MODEL_RELATIVE_PATH = Path("models") / "clip-image-encoder.onnx"
VIEWER_DIST = Path("viewer-dist")
PLAYVIEW_DIST = Path("playview-dist")
STUDIO_DIR = Path("studio")


class Ansi:
    """ANSI style constants."""

    reset = "\033[0m"
    bold = "\033[1m"
    dim = "\033[2m"
    green = "\033[32m"
    yellow = "\033[33m"
    red = "\033[31m"
    blue = "\033[34m"
    cyan = "\033[36m"


class Action(StrEnum):
    """Installer action."""

    INSTALL = "install"
    ADVANCED = "advanced"
    REPAIR = "repair"
    UNINSTALL = "uninstall"
    QUIT = "quit"


@dataclass(frozen=True)
class InstallOptions:
    """Resolved installer options."""

    action: Action
    launch: bool
    install_model: bool
    reset_environment: bool


@dataclass(frozen=True)
class AppPaths:
    """Important app paths."""

    app_dir: Path
    studio_dir: Path
    viewer_dist: Path
    playview_dist: Path
    model_path: Path


@dataclass(frozen=True)
class CheckResult:
    """Dependency or file check result."""

    label: str
    ok: bool
    detail: str


def supports_color() -> bool:
    """Return whether ANSI colors should be emitted."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.name == "nt":
        return bool(os.environ.get("WT_SESSION") or os.environ.get("TERM"))
    return sys.stdout.isatty()


COLOR = supports_color()


def style(text: str, *styles: str) -> str:
    """Style text if color is enabled."""
    if not COLOR:
        return text
    return "".join(styles) + text + Ansi.reset


def clear_screen() -> None:
    """Clear terminal when interactive."""
    if sys.stdout.isatty():
        print("\033[2J\033[H" if COLOR else "\n" * 4, end="")


def header(title: str) -> None:
    """Print boxed installer header."""
    clear_screen()
    print(style("✦ Constellation", Ansi.bold, Ansi.cyan))
    print(style(title, Ansi.bold))
    print(
        style("Private photo map. Local-first. No cloud account.\n", Ansi.dim)
    )


def prompt(default: str = "") -> str:
    """Read a response from stdin."""
    try:
        return input(default).strip()
    except EOFError:
        return ""


def prompt_yes_no(question: str, *, default: bool) -> bool:
    """Ask yes/no question."""
    suffix = "Y/n" if default else "y/N"
    response = prompt(f"{question} [{suffix}] ")
    if not response:
        return default
    return response.lower() in {"y", "yes"}


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """Run command while streaming output."""
    printable = " ".join(command)
    print(style(f"\n→ {printable}", Ansi.dim))
    try:
        process = subprocess.Popen(  # noqa: S603
            list(command),
            cwd=cwd,
            env=env,
        )
    except OSError as exc:
        print(style(f"Failed to start: {exc}", Ansi.red))
        return 1
    return process.wait()


def find_uv() -> str | None:
    """Find uv executable."""
    executable = shutil.which("uv")
    if executable:
        return executable
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / "uv",
        home / ".cargo" / "bin" / "uv",
    ]
    if os.name == "nt":
        candidates.extend(
            [
                home / ".local" / "bin" / "uv.exe",
                home / ".cargo" / "bin" / "uv.exe",
            ]
        )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def app_paths(app_dir: Path) -> AppPaths:
    """Build path set."""
    resolved = app_dir.expanduser().resolve()
    return AppPaths(
        app_dir=resolved,
        studio_dir=resolved / STUDIO_DIR,
        viewer_dist=resolved / VIEWER_DIST,
        playview_dist=resolved / PLAYVIEW_DIST,
        model_path=resolved / MODEL_RELATIVE_PATH,
    )


def onnx_runtime_supported() -> bool:
    """Return whether current platform has ONNX Runtime wheels."""
    return not (sys.platform == "darwin" and platform.machine() == "x86_64")


def check_install(paths: AppPaths, uv_path: str | None) -> list[CheckResult]:
    """Run dependency and release checks."""
    onnx_supported = onnx_runtime_supported()
    model_ok = paths.model_path.is_file() or not onnx_supported
    if paths.model_path.is_file():
        model_detail = str(paths.model_path)
    elif onnx_supported:
        model_detail = "will download after Python deps"
    else:
        model_detail = "Intel macOS: embeddings disabled; app still runs"
    return [
        CheckResult("uv runtime", uv_path is not None, uv_path or "missing"),
        CheckResult(
            "Python 3.13",
            uv_path is not None,
            "managed by uv; downloaded automatically if missing",
        ),
        CheckResult(
            "app files",
            paths.studio_dir.is_dir(),
            str(paths.studio_dir),
        ),
        CheckResult(
            "viewer assets",
            paths.viewer_dist.is_dir(),
            str(paths.viewer_dist),
        ),
        CheckResult(
            "playview assets",
            paths.playview_dist.is_dir(),
            str(paths.playview_dist),
        ),
        CheckResult("ONNX model", model_ok, model_detail),
    ]


def print_checks(checks: Sequence[CheckResult]) -> None:
    """Print check table."""
    for check in checks:
        icon = style("✓", Ansi.green) if check.ok else style("•", Ansi.yellow)
        print(f"  {icon} {check.label:<16} {check.detail}")


def choose_action(paths: AppPaths) -> InstallOptions:
    """Ask user for install mode."""
    header("Installer")
    print(f"Install folder: {style(str(paths.app_dir), Ansi.bold)}\n")
    print("Choose path:")
    print("  1. Recommended install / update")
    print("  2. Advanced options")
    print("  3. Repair install")
    print("  4. Uninstall app files")
    print("  5. Quit")
    choice = prompt("\nPress Enter for 1: ") or "1"
    if choice == "2":
        return advanced_options()
    if choice == "3":
        return InstallOptions(
            action=Action.REPAIR,
            launch=True,
            install_model=True,
            reset_environment=True,
        )
    if choice == "4":
        return InstallOptions(
            action=Action.UNINSTALL,
            launch=False,
            install_model=False,
            reset_environment=False,
        )
    if choice == "5":
        return InstallOptions(
            action=Action.QUIT,
            launch=False,
            install_model=False,
            reset_environment=False,
        )
    return InstallOptions(
        action=Action.INSTALL,
        launch=True,
        install_model=True,
        reset_environment=False,
    )


def advanced_options() -> InstallOptions:
    """Ask advanced questions."""
    header("Advanced options")
    install_model = prompt_yes_no(
        "Download local image-understanding model if missing?",
        default=True,
    )
    launch = prompt_yes_no("Launch Constellation after install?", default=True)
    reset_environment = prompt_yes_no(
        "Recreate Python environment from scratch?",
        default=False,
    )
    return InstallOptions(
        action=Action.ADVANCED,
        launch=launch,
        install_model=install_model,
        reset_environment=reset_environment,
    )


def validate_release(paths: AppPaths) -> bool:
    """Return whether release contains required files."""
    missing = [
        path
        for path in (paths.studio_dir, paths.viewer_dist, paths.playview_dist)
        if not path.exists()
    ]
    if not missing:
        return True
    print(style("\nRelease incomplete. Missing:", Ansi.red))
    for path in missing:
        print(f"  - {path}")
    return False


def sync_python_environment(
    paths: AppPaths,
    uv_path: str,
    *,
    reset: bool,
    include_onnx: bool,
) -> bool:
    """Create or update Python app environment."""
    venv = paths.studio_dir / ".venv"
    if reset and venv.exists():
        print(style("\nRemoving old Python environment…", Ansi.yellow))
        shutil.rmtree(venv)
    command = [
        uv_path,
        "--directory",
        str(paths.studio_dir),
        "sync",
        "--inexact",
        "--no-dev",
    ]
    if include_onnx:
        command.extend(["--extra", "onnx"])
    code = run_command(command, cwd=paths.app_dir)
    return code == 0


def ensure_model(
    paths: AppPaths, uv_path: str, *, install_model: bool
) -> bool:
    """Ensure ONNX model exists, downloading when requested."""
    if not onnx_runtime_supported():
        print(
            style(
                "\nONNX Runtime is unavailable on Intel macOS with "
                "Python 3.13; semantic embeddings disabled.",
                Ansi.yellow,
            )
        )
        return True
    if paths.model_path.is_file():
        print(style("\n✓ ONNX model ready", Ansi.green))
        return True
    if not install_model:
        print(style("\nSkipping ONNX model download.", Ansi.yellow))
        return True
    code = run_command(
        [
            uv_path,
            "--project",
            str(paths.studio_dir),
            "run",
            "--no-dev",
            "constellation-download-onnx",
            "--output",
            str(paths.model_path),
        ],
        cwd=paths.app_dir,
    )
    return code == 0


def launch_app(
    paths: AppPaths,
    uv_path: str,
    *,
    embedding_enabled: bool,
) -> int:
    """Launch Constellation app."""
    header("Ready")
    print(style("Install complete.", Ansi.green, Ansi.bold))
    print("Opening local app in browser. Keep this window open.\n")
    command = [
        uv_path,
        "--project",
        str(paths.studio_dir),
        "run",
        "--no-dev",
        "constellation-app",
        "--viewer-dist",
        str(paths.viewer_dist),
        "--playview-dist",
        str(paths.playview_dist),
    ]
    if not embedding_enabled:
        command.extend(["--embedding-engine", "none"])
    return run_command(command, cwd=paths.app_dir)


def write_launcher(
    paths: AppPaths,
    uv_path: str,
    *,
    embedding_enabled: bool,
) -> None:
    """Write local launch helper."""
    embedding_args = "" if embedding_enabled else "--embedding-engine none "
    if os.name == "nt":
        launcher = paths.app_dir / "Constellation.ps1"
        launcher.write_text(
            '$ErrorActionPreference = "Stop"\n'
            "$Root = Split-Path -Parent $MyInvocation.MyCommand.Path\n"
            f'& {quote_ps(uv_path)} --project (Join-Path $Root "studio") '
            "run --no-dev constellation-app "
            f"{embedding_args}"
            '--viewer-dist (Join-Path $Root "viewer-dist") '
            '--playview-dist (Join-Path $Root "playview-dist") @args\n',
            encoding="utf-8",
        )
        return
    launcher = paths.app_dir / "constellation"
    launcher.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        f'exec {sh_quote(uv_path)} --project "$ROOT/studio" run --no-dev constellation-app '
        f"{embedding_args}"
        '--viewer-dist "$ROOT/viewer-dist" '
        '--playview-dist "$ROOT/playview-dist" "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)


def sh_quote(value: str) -> str:
    """Return shell-safe single-quoted string."""
    return "'" + value.replace("'", "'\\''") + "'"


def quote_ps(value: str) -> str:
    """Return PowerShell-safe single-quoted string."""
    return "'" + value.replace("'", "''") + "'"


def uninstall(paths: AppPaths) -> int:
    """Remove app files after confirmation."""
    header("Uninstall")
    print(f"This removes app files in:\n  {paths.app_dir}\n")
    print(
        "Photo library untouched. App data/cache may remain in system app data."
    )
    if not prompt_yes_no("Continue?", default=False):
        return 0
    shutil.rmtree(paths.app_dir)
    print(style("Removed Constellation app files.", Ansi.green))
    return 0


def install(paths: AppPaths, options: InstallOptions) -> int:
    """Perform install/update/repair."""
    uv_path = find_uv()
    embedding_enabled = options.install_model and onnx_runtime_supported()
    if options.install_model and not embedding_enabled:
        options = replace(options, install_model=False)
    header("Checking system")
    checks = check_install(paths, uv_path)
    print_checks(checks)
    if uv_path is None:
        print(
            style(
                "\nuv missing. Bootstrap script should install it first.",
                Ansi.red,
            )
        )
        return 1
    if not validate_release(paths):
        return 1
    print(style("\nPreparing Python runtime and dependencies…", Ansi.bold))
    print("uv will download Python 3.13 if this Mac/PC does not have it.")
    if not sync_python_environment(
        paths,
        uv_path,
        reset=options.reset_environment,
        include_onnx=embedding_enabled,
    ):
        return 1
    if not ensure_model(paths, uv_path, install_model=options.install_model):
        return 1
    write_launcher(paths, uv_path, embedding_enabled=embedding_enabled)
    if options.launch:
        return launch_app(
            paths,
            uv_path,
            embedding_enabled=embedding_enabled,
        )
    header("Done")
    print(style("Install complete.", Ansi.green, Ansi.bold))
    print(f"Run later from: {paths.app_dir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""
    parser = argparse.ArgumentParser(description="Constellation installer TUI")
    parser.add_argument(
        "--app-dir",
        type=Path,
        required=True,
        help="Extracted Constellation app directory.",
    )
    parser.add_argument(
        "--recommended",
        action="store_true",
        help="Run recommended install without prompts.",
    )
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="Do not launch after install.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = app_paths(Path(args.app_dir))
    if bool(args.recommended):
        options = InstallOptions(
            action=Action.INSTALL,
            launch=not bool(args.no_launch),
            install_model=True,
            reset_environment=False,
        )
    else:
        options = choose_action(paths)
    if bool(args.no_launch):
        options = replace(options, launch=False)
    if options.action == Action.QUIT:
        return 0
    if options.action == Action.UNINSTALL:
        return uninstall(paths)
    return install(paths, options)


if __name__ == "__main__":
    if platform.system().lower() == "darwin":
        os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
    raise SystemExit(main())
