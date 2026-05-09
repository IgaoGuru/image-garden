#!/usr/bin/env python3
"""Friendly Image Garden installer TUI.

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
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

APP_NAME = "Image Garden"
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


@dataclass(frozen=True)
class SelectOption:
    """One interactive selector row."""

    label: str
    hint: str = ""


def enable_windows_virtual_terminal() -> bool:
    """Enable ANSI escape sequences in classic Windows consoles."""
    if os.name != "nt" or not sys.stdout.isatty():
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.GetStdHandle(-11)
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_vt = 0x0004
        return bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
    except (AttributeError, OSError, ValueError):
        return False


def supports_color() -> bool:
    """Return whether ANSI colors should be emitted."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.name == "nt":
        return enable_windows_virtual_terminal() or bool(
            os.environ.get("WT_SESSION") or os.environ.get("TERM")
        )
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
    print(style("✦ Image Garden", Ansi.bold, Ansi.cyan))
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


@contextmanager
def raw_terminal() -> Generator[None]:
    """Temporarily put stdin in raw mode when possible."""
    if os.name == "nt" or not sys.stdin.isatty():
        yield
        return
    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def read_windows_key() -> str:
    """Read one Windows navigation key."""
    import msvcrt

    first = msvcrt.getwch()
    if first in {"\x00", "à"}:
        second = msvcrt.getwch()
        return {"H": "up", "P": "down"}.get(second, "")
    return {"\r": "enter", "\x03": "ctrl-c"}.get(first, first.lower())


def read_posix_key() -> str:
    """Read one POSIX navigation key."""
    first = sys.stdin.read(1)
    if first == "\x1b":
        rest = sys.stdin.read(2)
        return {"[A": "up", "[B": "down"}.get(rest, "escape")
    return {"\x03": "ctrl-c", "\r": "enter", "\n": "enter"}.get(
        first, first.lower()
    )


def read_key() -> str:
    """Read one navigation key."""
    return read_windows_key() if os.name == "nt" else read_posix_key()


def clear_lines(count: int) -> None:
    """Clear the previous rendered prompt."""
    if count <= 0:
        return
    print(f"\033[{count}A", end="")
    for _ in range(count):
        print("\033[2K\033[1B", end="")
    print(f"\033[{count}A", end="")


def select_index(
    message: str,
    options: Sequence[SelectOption],
    *,
    default: int = 0,
) -> int | None:
    """Select one option using arrow keys, clack-style."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return default
    cursor = default
    rendered = 0
    with raw_terminal():
        while True:
            clear_lines(rendered)
            lines = [
                f"{style('◆', Ansi.green)}  {style(message, Ansi.bold)}",
                f"{style('│', Ansi.dim)}  {style('↑↓ move, enter confirm, q cancel', Ansi.dim)}",
                f"{style('│', Ansi.dim)}",
            ]
            for index, option in enumerate(options):
                active = index == cursor
                marker = style("❯", Ansi.cyan) if active else " "  # noqa: RUF001
                radio = (
                    style("●", Ansi.green) if active else style("○", Ansi.dim)
                )
                label = (
                    style(option.label, Ansi.bold) if active else option.label
                )
                hint = (
                    f" {style(option.hint, Ansi.dim)}" if option.hint else ""
                )
                lines.append(
                    f"{style('│', Ansi.dim)} {marker} {radio} {label}{hint}"
                )
            lines.append(style("└", Ansi.dim))
            print("\n".join(lines))
            rendered = len(lines)
            key = read_key()
            if key == "up":
                cursor = (cursor - 1) % len(options)
            elif key == "down":
                cursor = (cursor + 1) % len(options)
            elif key in {"enter", " "}:
                clear_lines(rendered)
                print(f"{style('◇', Ansi.green)}  {style(message, Ansi.bold)}")
                print(f"{style('│', Ansi.dim)}  {options[cursor].label}")
                print(style("└", Ansi.dim))
                return cursor
            elif key in {"q", "escape", "ctrl-c"}:
                clear_lines(rendered)
                print(f"{style('■', Ansi.red)}  {style(message, Ansi.bold)}")
                print(
                    f"{style('│', Ansi.dim)}  {style('Cancelled', Ansi.dim)}"
                )
                print(style("└", Ansi.dim))
                return None


def prompt_yes_no(question: str, *, default: bool) -> bool:
    """Ask yes/no question."""
    if sys.stdin.isatty() and sys.stdout.isatty():
        selected = select_index(
            question,
            [SelectOption("Yes"), SelectOption("No")],
            default=0 if default else 1,
        )
        if selected is not None:
            return selected == 0
    suffix = "Y/n" if default else "y/N"
    response = prompt(f"{question} [{suffix}] ")
    if not response:
        return default
    return response.lower() in {"y", "yes"}


def pause_before_exit() -> None:
    """Keep terminal visible after installer completes."""
    if sys.stdin.isatty() and sys.stdout.isatty():
        print()
        prompt("Press Enter to return to your terminal… ")


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


def default_app_data_dir() -> Path:
    """Return default app data directory used by image-garden app."""
    env_data = os.environ.get("IMAGE_GARDEN_DATA_DIR") or os.environ.get(
        "CONSTELLATION_DATA_DIR"
    )
    if env_data:
        return Path(env_data).expanduser()
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local"
        )
        return Path(root) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    root = os.environ.get("XDG_DATA_HOME") or str(
        Path.home() / ".local" / "share"
    )
    return Path(root) / "image-garden"


def app_paths(app_dir: Path) -> AppPaths:
    """Build path set."""
    resolved = app_dir.expanduser()
    return AppPaths(
        app_dir=resolved,
        studio_dir=resolved / STUDIO_DIR,
        viewer_dist=resolved / VIEWER_DIST,
        playview_dist=resolved / PLAYVIEW_DIST,
        model_path=default_app_data_dir() / MODEL_RELATIVE_PATH,
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


def print_next_step_box(command: str) -> None:
    """Print the primary next step in a clear green box."""
    horizontal_margin = 6
    vertical_padding = 1
    inner_width = len(command) + (horizontal_margin * 2)
    border_top = "┌" + "─" * inner_width + "┐"
    border_bottom = "└" + "─" * inner_width + "┘"
    empty = "│" + " " * inner_width + "│"
    command_line = (
        "│"
        + " " * horizontal_margin
        + command
        + " " * horizontal_margin
        + "│"
    )
    print(style("Run this next:", Ansi.green, Ansi.bold))
    print(style(border_top, Ansi.green, Ansi.bold))
    for _ in range(vertical_padding):
        print(style(empty, Ansi.green, Ansi.bold))
    print(style(command_line, Ansi.green, Ansi.bold))
    for _ in range(vertical_padding):
        print(style(empty, Ansi.green, Ansi.bold))
    print(style(border_bottom, Ansi.green, Ansi.bold))


def choose_action(paths: AppPaths) -> InstallOptions:
    """Ask user for install mode."""
    header("Installer")
    print(f"Install folder: {style(str(paths.app_dir), Ansi.bold)}\n")
    selected = select_index(
        "Choose install path",
        [
            SelectOption(
                "Recommended install / update", "installs CLI; launch later"
            ),
            SelectOption("Install and launch now", "start app after setup"),
            SelectOption("Advanced options", "choose model/download behavior"),
            SelectOption("Repair install", "recreate Python environment"),
            SelectOption("Uninstall app files", "keeps your photos"),
            SelectOption("Quit"),
        ],
    )
    if selected is None or selected == 5:
        return InstallOptions(
            action=Action.QUIT,
            launch=False,
            install_model=False,
            reset_environment=False,
        )
    if selected == 1:
        return InstallOptions(
            action=Action.INSTALL,
            launch=True,
            install_model=True,
            reset_environment=False,
        )
    if selected == 2:
        return advanced_options()
    if selected == 3:
        return InstallOptions(
            action=Action.REPAIR,
            launch=False,
            install_model=True,
            reset_environment=True,
        )
    if selected == 4:
        return InstallOptions(
            action=Action.UNINSTALL,
            launch=False,
            install_model=False,
            reset_environment=False,
        )
    return InstallOptions(
        action=Action.INSTALL,
        launch=False,
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
    launch = prompt_yes_no("Launch Image Garden after install?", default=False)
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
    """Launch Image Garden app."""
    header("Ready")
    print(style("Install complete.", Ansi.green, Ansi.bold))
    print("Starting local app in this terminal and opening browser.\n")
    command = [
        uv_path,
        "--project",
        str(paths.studio_dir),
        "run",
        "--no-dev",
        "image-garden",
        "start",
    ]
    if not embedding_enabled:
        print(style("Embeddings disabled on this platform.", Ansi.yellow))
    code = run_command(command, cwd=paths.app_dir)
    pause_before_exit()
    return code


def write_launcher(
    paths: AppPaths,
    uv_path: str,
    *,
    embedding_enabled: bool,
) -> None:
    """Write stable CLI launch helpers."""
    del embedding_enabled
    if os.name == "nt":
        app_launcher = paths.app_dir / "Image Garden.cmd"
        app_launcher.write_text(
            "@echo off\r\n"
            f"set IMAGE_GARDEN_INSTALL_DIR={paths.app_dir}\r\n"
            f"{uv_path} --project \"{paths.studio_dir}\" run --no-dev image-garden %*\r\n",
            encoding="utf-8",
        )
        bin_dir = default_windows_bin_dir()
        bin_dir.mkdir(parents=True, exist_ok=True)
        shim = bin_dir / "image-garden.cmd"
        shim.write_text(app_launcher.read_text(encoding="utf-8"), encoding="utf-8")
        return
    app_launcher = paths.app_dir / "image-garden"
    launcher_text = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"export IMAGE_GARDEN_INSTALL_DIR={sh_quote(str(paths.app_dir))}\n"
        f"exec {sh_quote(uv_path)} --project {sh_quote(str(paths.studio_dir))} run --no-dev image-garden \"$@\"\n"
    )
    app_launcher.write_text(launcher_text, encoding="utf-8")
    app_launcher.chmod(0o755)
    bin_dir = Path.home() / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "image-garden"
    shim.write_text(launcher_text, encoding="utf-8")
    shim.chmod(0o755)


def default_windows_bin_dir() -> Path:
    """Return Windows user-local bin dir for shim."""
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / "Image Garden" / "bin"
    return Path.home() / ".image-garden" / "bin"


def sh_quote(value: str) -> str:
    """Return shell-safe single-quoted string."""
    return "'" + value.replace("'", "'\\''") + "'"


def quote_ps(value: str) -> str:
    """Return PowerShell-safe single-quoted string."""
    return "'" + value.replace("'", "''") + "'"


def uninstall(paths: AppPaths) -> int:
    """Remove app files after confirmation."""
    header("Uninstall")
    target = paths.app_dir.parent if paths.app_dir.name == "current" else paths.app_dir
    print(f"This removes app files in:\n  {target}\n")
    print(
        "Photo library untouched. App data/cache may remain in system app data."
    )
    if not prompt_yes_no("Continue?", default=False):
        pause_before_exit()
        return 0
    shutil.rmtree(target)
    print(style("Removed Image Garden app files.", Ansi.green))
    pause_before_exit()
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
    print(style("Image Garden installed.", Ansi.green, Ansi.bold))
    print()
    print_next_step_box("image-garden start")
    print()
    print("Stop:")
    print("  Press Ctrl+C in the terminal running image-garden start")
    print()
    print("Open later:")
    print("  image-garden open")
    print()
    print("Logs:")
    print("  image-garden logs")
    if os.name != "nt" and str(
        Path.home() / ".local" / "bin"
    ) not in os.environ.get("PATH", ""):
        print()
        print(
            style(
                "Note: ~/.local/bin is not on PATH in this terminal.",
                Ansi.yellow,
            )
        )
        direct = Path.home() / ".local" / "bin" / "image-garden"
        print(f"You can run directly: {direct} start")
        print("Background mode is also available: image-garden start --background")
    pause_before_exit()
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""
    parser = argparse.ArgumentParser(description="Image Garden installer TUI")
    parser.add_argument(
        "--app-dir",
        type=Path,
        required=True,
        help="Extracted Image Garden app directory.",
    )
    parser.add_argument(
        "--recommended",
        action="store_true",
        help="Run recommended install without prompts.",
    )
    parser.add_argument(
        "--launch",
        action="store_true",
        help="Launch after install.",
    )
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="Do not launch after install (default).",
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
            launch=bool(args.launch) and not bool(args.no_launch),
            install_model=True,
            reset_environment=False,
        )
    else:
        options = choose_action(paths)
    if bool(args.launch):
        options = replace(options, launch=True)
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
