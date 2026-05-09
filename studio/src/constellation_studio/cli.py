"""User-facing Image Garden CLI lifecycle manager."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Literal, cast

from constellation_studio import app as app_launcher
from constellation_studio.app import default_app_data_dir
from constellation_studio.download_onnx import (
    DEFAULT_ONNX_SHA256,
    download_onnx_model,
    sha256_file,
)

APP_NAME = "Image Garden"
DEFAULT_HOST = "127.0.0.1"
MODEL_RELATIVE_PATH = Path("models") / "clip-image-encoder.onnx"
RUNTIME_DIR_NAME = "runtime"
STATE_FILE_NAME = "server.json"
PID_FILE_NAME = "server.pid"
LOG_FILE_NAME = "server.log"
LAST_ERROR_FILE_NAME = "last-error.log"

CheckStatus = Literal["ok", "warn", "fail"]


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """Persisted background server state."""

    pid: int
    url: str
    host: str
    port: int
    started_at: str
    version: str
    install_root: str
    data_dir: str
    log_path: str


@dataclass(frozen=True, slots=True)
class Paths:
    """Resolved CLI paths."""

    install_root: Path
    studio_dir: Path
    viewer_dist: Path
    playview_dist: Path
    data_dir: Path
    runtime_dir: Path
    state_file: Path
    pid_file: Path
    log_file: Path
    last_error_file: Path
    model_path: Path
    user_bin_dir: Path
    posix_shim: Path
    windows_shim: Path


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """One diagnostic check result."""

    label: str
    status: CheckStatus
    detail: str


def now_iso() -> str:
    """Return current UTC timestamp."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def package_install_root() -> Path:
    """Infer install root from this package location."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "studio" / "pyproject.toml").is_file():
            return parent
    return here.parents[3]


def configured_install_root() -> Path:
    """Return configured install root or infer it."""
    env_root = os.environ.get("IMAGE_GARDEN_INSTALL_DIR") or os.environ.get(
        "CONSTELLATION_INSTALL_DIR"
    )
    if env_root:
        return Path(env_root).expanduser()
    return package_install_root()


def user_bin_dir() -> Path:
    """Return user-local bin directory for CLI shims."""
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "Image Garden" / "bin"
        return Path.home() / ".image-garden" / "bin"
    return Path.home() / ".local" / "bin"


def resolve_paths(
    *,
    install_root: Path | None = None,
    data_dir: Path | None = None,
) -> Paths:
    """Resolve all runtime paths."""
    root = (install_root or configured_install_root()).expanduser()
    app_data = (data_dir or default_app_data_dir()).expanduser().resolve()
    runtime_dir = app_data / RUNTIME_DIR_NAME
    bin_dir = user_bin_dir()
    return Paths(
        install_root=root,
        studio_dir=root / "studio",
        viewer_dist=root / "viewer-dist",
        playview_dist=root / "playview-dist",
        data_dir=app_data,
        runtime_dir=runtime_dir,
        state_file=runtime_dir / STATE_FILE_NAME,
        pid_file=runtime_dir / PID_FILE_NAME,
        log_file=runtime_dir / LOG_FILE_NAME,
        last_error_file=runtime_dir / LAST_ERROR_FILE_NAME,
        model_path=app_data / MODEL_RELATIVE_PATH,
        user_bin_dir=bin_dir,
        posix_shim=bin_dir / "image-garden",
        windows_shim=bin_dir / "image-garden.cmd",
    )


def read_version(install_root: Path) -> str:
    """Read installed version marker."""
    for candidate in (
        install_root / "VERSION",
        install_root / "version.txt",
        install_root / "studio" / "VERSION",
    ):
        if candidate.is_file():
            value = candidate.read_text(encoding="utf-8").strip()
            if value:
                return value
    return "dev"


def is_onnx_runtime_supported() -> bool:
    """Return whether this platform should use ONNX Runtime."""
    import platform

    return not (sys.platform == "darwin" and platform.machine() == "x86_64")


def onnx_runtime_available() -> bool:
    """Return whether ONNX Runtime is importable in this environment."""
    return importlib.util.find_spec("onnxruntime") is not None


def process_exists(pid: int) -> bool:
    """Return whether process exists."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return os.name == "nt"
    return True


def read_state(paths: Paths) -> RuntimeState | None:
    """Read persisted runtime state."""
    if not paths.state_file.is_file():
        return None
    try:
        loaded = cast(
            "object",
            json.loads(paths.state_file.read_text(encoding="utf-8")),
        )
        if not isinstance(loaded, Mapping):
            return None
        raw = cast("Mapping[str, object]", loaded)
        return RuntimeState(
            pid=int(cast("str | int", raw["pid"])),
            url=str(raw["url"]),
            host=str(raw["host"]),
            port=int(cast("str | int", raw["port"])),
            started_at=str(raw["started_at"]),
            version=str(raw["version"]),
            install_root=str(raw["install_root"]),
            data_dir=str(raw["data_dir"]),
            log_path=str(raw["log_path"]),
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def write_state(paths: Paths, state: RuntimeState) -> None:
    """Persist runtime state."""
    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    paths.state_file.write_text(
        json.dumps(asdict(state), indent=2) + "\n",
        encoding="utf-8",
    )
    paths.pid_file.write_text(f"{state.pid}\n", encoding="utf-8")


def clear_state(paths: Paths) -> None:
    """Remove runtime state files."""
    for path in (paths.state_file, paths.pid_file):
        with suppress(FileNotFoundError):
            path.unlink()


def running_state(paths: Paths) -> RuntimeState | None:
    """Return state when the recorded server is still running."""
    state = read_state(paths)
    if state is None:
        return None
    if process_exists(state.pid):
        return state
    clear_state(paths)
    return None


def free_port(host: str = DEFAULT_HOST) -> int:
    """Ask OS for a currently free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def wait_for_url(url: str, *, timeout: float = 30.0) -> bool:
    """Wait until URL serves an HTTP response."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:  # noqa: S310
                return int(response.status) < 500
        except urllib.error.HTTPError as exc:
            return int(exc.code) < 500
        except OSError:
            time.sleep(0.25)
    return False


def app_command(paths: Paths, *, port: int, no_open: bool) -> list[str]:
    """Build command used to run the local app server."""
    command = [
        sys.executable,
        "-m",
        "constellation_studio.app",
        "--host",
        DEFAULT_HOST,
        "--port",
        str(port),
        "--data-dir",
        str(paths.data_dir),
        "--viewer-dist",
        str(paths.viewer_dist),
        "--playview-dist",
        str(paths.playview_dist),
    ]
    if no_open:
        command.append("--no-open")
    if is_onnx_runtime_supported() and onnx_runtime_available():
        if paths.model_path.is_file():
            command.extend(["--onnx-model", str(paths.model_path)])
    else:
        command.extend(["--embedding-engine", "none"])
    return command


def open_log(paths: Paths) -> BinaryIO:
    """Open runtime log file for appending."""
    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    return paths.log_file.open("ab", buffering=0)


def start_process(paths: Paths, *, port: int) -> subprocess.Popen[bytes]:
    """Start background app process."""
    log_file = open_log(paths)
    log_file.write(f"\n--- image-garden start {now_iso()} ---\n".encode())
    command = app_command(paths, port=port, no_open=True)
    try:
        if os.name == "nt":
            process = subprocess.Popen(  # noqa: S603
                command,
                cwd=paths.install_root,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            process = subprocess.Popen(  # noqa: S603
                command,
                cwd=paths.install_root,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        log_file.close()
        return process
    except Exception:
        log_file.close()
        raise


def preflight_start(paths: Paths) -> int:
    """Return 0 when start preflight succeeds."""
    model_status = model_check(paths)
    if model_status.status == "fail":
        print(f"error: {model_status.detail}", file=sys.stderr)
        print("Repair: image-garden model redownload", file=sys.stderr)
        return 1
    return 0


def runtime_state_for_pid(
    paths: Paths,
    *,
    pid: int,
    port: int,
    url: str,
) -> RuntimeState:
    """Build runtime state for a started process."""
    return RuntimeState(
        pid=pid,
        url=url,
        host=DEFAULT_HOST,
        port=port,
        started_at=now_iso(),
        version=read_version(paths.install_root),
        install_root=str(paths.install_root),
        data_dir=str(paths.data_dir),
        log_path=str(paths.log_file),
    )


def cmd_start_background(args: argparse.Namespace) -> int:
    """Start the app in the background and return."""
    paths = resolve_paths()
    existing = running_state(paths)
    if existing is not None:
        print(f"Image Garden is already running: {existing.url}")
        if not bool(args.no_open):
            webbrowser.open(existing.url)
        return 0
    if preflight_start(paths) != 0:
        return 1

    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    port = int(args.port) if int(args.port) > 0 else free_port()
    url = f"http://{DEFAULT_HOST}:{port}/"
    try:
        process = start_process(paths, port=port)
    except OSError as exc:
        paths.last_error_file.write_text(str(exc) + "\n", encoding="utf-8")
        print(f"error: failed to start Image Garden: {exc}", file=sys.stderr)
        return 1

    write_state(
        paths,
        runtime_state_for_pid(paths, pid=process.pid, port=port, url=url),
    )
    if not wait_for_url(url):
        terminate_pid(process.pid, timeout=2.0)
        clear_state(paths)
        message = (
            "Image Garden did not become ready in time. "
            f"See logs: {paths.log_file}"
        )
        paths.last_error_file.write_text(message + "\n", encoding="utf-8")
        print(f"error: {message}", file=sys.stderr)
        return 1

    print("Image Garden started in background")
    print(f"URL: {url}")
    print("Stop: image-garden stop")
    print("Logs: image-garden logs")
    if not bool(args.no_open):
        webbrowser.open(url)
    return 0


def app_parser_args(
    paths: Paths, *, port: int, no_open: bool
) -> argparse.Namespace:
    """Build parsed constellation-app args for in-process foreground run."""
    argv = [
        "--host",
        DEFAULT_HOST,
        "--port",
        str(port),
        "--data-dir",
        str(paths.data_dir),
        "--viewer-dist",
        str(paths.viewer_dist),
        "--playview-dist",
        str(paths.playview_dist),
    ]
    if no_open:
        argv.append("--no-open")
    if is_onnx_runtime_supported() and onnx_runtime_available():
        if paths.model_path.is_file():
            argv.extend(["--onnx-model", str(paths.model_path)])
    else:
        argv.extend(["--embedding-engine", "none"])
    return app_launcher.build_parser().parse_args(argv)


def raise_keyboard_interrupt(_signum: int, _frame: object) -> None:
    """Convert termination signals into normal foreground shutdown."""
    raise KeyboardInterrupt


def cmd_start_foreground(args: argparse.Namespace) -> int:
    """Start the app in this terminal until it is interrupted."""
    paths = resolve_paths()
    existing = running_state(paths)
    if existing is not None:
        print(f"Image Garden is already running: {existing.url}")
        print("Stop it first, or use: image-garden open")
        return 1
    if preflight_start(paths) != 0:
        return 1

    paths.runtime_dir.mkdir(parents=True, exist_ok=True)
    port = int(args.port) if int(args.port) > 0 else free_port()
    url = f"http://{DEFAULT_HOST}:{port}/"
    write_state(
        paths,
        runtime_state_for_pid(paths, pid=os.getpid(), port=port, url=url),
    )
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, raise_keyboard_interrupt)
    try:
        print("Keep this terminal open. Press Ctrl+C to stop.")
        return app_launcher.run(
            app_parser_args(paths, port=port, no_open=bool(args.no_open))
        )
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        clear_state(paths)


def cmd_start(args: argparse.Namespace) -> int:
    """Start the app, foreground by default."""
    if bool(getattr(args, "background", False)):
        return cmd_start_background(args)
    return cmd_start_foreground(args)


def terminate_pid(pid: int, *, timeout: float = 8.0) -> bool:
    """Terminate a process by PID."""
    if not process_exists(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return not process_exists(pid)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_exists(pid):
            return True
        time.sleep(0.2)
    if os.name != "nt":
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            return not process_exists(pid)
    return not process_exists(pid)


def cmd_stop(_args: argparse.Namespace) -> int:
    """Stop the background app."""
    paths = resolve_paths()
    state = read_state(paths)
    if state is None or not process_exists(state.pid):
        clear_state(paths)
        print("Image Garden is stopped.")
        return 0
    if not terminate_pid(state.pid):
        print(
            f"error: failed to stop PID {state.pid}; see {paths.log_file}",
            file=sys.stderr,
        )
        return 1
    clear_state(paths)
    print("Image Garden stopped.")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    """Restart the app."""
    stop_code = cmd_stop(args)
    if stop_code != 0:
        return stop_code
    return cmd_start(args)


def cmd_status(_args: argparse.Namespace) -> int:
    """Print current runtime status."""
    paths = resolve_paths()
    state = running_state(paths)
    print("✦ Image Garden")
    if state is None:
        print("Status: stopped")
    else:
        print("Status: running")
        print(f"URL: {state.url}")
        print(f"PID: {state.pid}")
        print(f"Started: {state.started_at}")
    print(f"Version: {read_version(paths.install_root)}")
    print(f"Install: {paths.install_root}")
    print(f"Data: {paths.data_dir}")
    print(f"Model: {'ready' if paths.model_path.is_file() else 'missing'}")
    print(f"Logs: {paths.log_file}")
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    """Open the running app in the default browser."""
    paths = resolve_paths()
    state = running_state(paths)
    if state is None:
        if bool(args.start):
            return cmd_start(args)
        print("Image Garden is not running.")
        print("Run: image-garden start")
        return 1
    print(f"Opening {state.url}")
    if not bool(args.no_browser):
        webbrowser.open(state.url)
    return 0


def tail_lines(path: Path, count: int) -> list[str]:
    """Return last count text lines from a file."""
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[
        -count:
    ]


def follow_file(path: Path) -> None:
    """Follow a growing text file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with path.open("r", encoding="utf-8", errors="replace") as file:
        file.seek(0, os.SEEK_END)
        while True:
            line = file.readline()
            if line:
                print(line, end="")
            else:
                time.sleep(0.5)


def cmd_logs(args: argparse.Namespace) -> int:
    """Show or follow runtime logs."""
    paths = resolve_paths()
    log_path = paths.last_error_file if bool(args.errors) else paths.log_file
    if bool(args.follow):
        try:
            follow_file(log_path)
        except KeyboardInterrupt:
            return 0
    lines = tail_lines(log_path, int(args.lines))
    if not lines:
        print(f"No logs yet: {log_path}")
        return 0
    print("\n".join(lines))
    return 0


def model_check(paths: Paths) -> DoctorCheck:
    """Return model availability/integrity check."""
    if not paths.model_path.is_file():
        return DoctorCheck("ONNX model", "warn", "missing")
    try:
        digest = sha256_file(paths.model_path)
    except OSError as exc:
        return DoctorCheck("ONNX model", "fail", str(exc))
    if digest != DEFAULT_ONNX_SHA256:
        return DoctorCheck("ONNX model", "fail", "checksum mismatch")
    return DoctorCheck("ONNX model", "ok", str(paths.model_path))


def doctor_checks(paths: Paths) -> list[DoctorCheck]:
    """Build diagnostic check list."""
    state = read_state(paths)
    stale = state is not None and not process_exists(state.pid)
    shim = paths.windows_shim if os.name == "nt" else paths.posix_shim
    return [
        DoctorCheck(
            "install root",
            "ok" if paths.install_root.is_dir() else "fail",
            str(paths.install_root),
        ),
        DoctorCheck(
            "studio project",
            "ok"
            if (paths.studio_dir / "pyproject.toml").is_file()
            else "fail",
            str(paths.studio_dir),
        ),
        DoctorCheck(
            "viewer assets",
            "ok" if paths.viewer_dist.is_dir() else "fail",
            str(paths.viewer_dist),
        ),
        DoctorCheck(
            "playview assets",
            "ok" if paths.playview_dist.is_dir() else "fail",
            str(paths.playview_dist),
        ),
        DoctorCheck(
            "uv runtime",
            "ok" if shutil.which("uv") else "fail",
            shutil.which("uv") or "missing",
        ),
        DoctorCheck(
            "ONNX Runtime",
            "ok"
            if onnx_runtime_available() or not is_onnx_runtime_supported()
            else "warn",
            "available"
            if onnx_runtime_available()
            else "unavailable; embeddings disabled until repair",
        ),
        DoctorCheck(
            "CLI shim",
            "ok" if shim.is_file() else "warn",
            str(shim),
        ),
        DoctorCheck(
            "app data",
            "ok"
            if paths.data_dir.exists() or paths.data_dir.parent.exists()
            else "warn",
            str(paths.data_dir),
        ),
        DoctorCheck(
            "runtime state",
            "warn" if stale else "ok",
            "stale PID" if stale else str(paths.runtime_dir),
        ),
        model_check(paths),
    ]


def print_checks(checks: Sequence[DoctorCheck]) -> None:
    """Print diagnostics."""
    icons: dict[CheckStatus, str] = {"ok": "✓", "warn": "•", "fail": "✗"}
    for check in checks:
        print(f"{icons[check.status]} {check.label:<16} {check.detail}")


def write_cli_shim(paths: Paths) -> None:
    """Create the user-facing image-garden command shim."""
    paths.user_bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        paths.windows_shim.write_text(
            "@echo off\r\n"
            f"set IMAGE_GARDEN_INSTALL_DIR={paths.install_root}\r\n"
            f'uv --project "{paths.studio_dir}" run --no-dev image-garden %*\r\n',
            encoding="utf-8",
        )
        return
    paths.posix_shim.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"export IMAGE_GARDEN_INSTALL_DIR={sh_quote(str(paths.install_root))}\n"
        f'exec uv --project {sh_quote(str(paths.studio_dir))} run --no-dev image-garden "$@"\n',
        encoding="utf-8",
    )
    paths.posix_shim.chmod(0o755)


def sync_environment(paths: Paths, *, recreate: bool = False) -> bool:
    """Run uv sync for the installed Studio project."""
    uv = shutil.which("uv")
    if uv is None:
        print("error: uv missing", file=sys.stderr)
        return False
    venv = paths.studio_dir / ".venv"
    if recreate and venv.exists():
        shutil.rmtree(venv)
    command = [
        uv,
        "--directory",
        str(paths.studio_dir),
        "sync",
        "--inexact",
        "--no-dev",
    ]
    if is_onnx_runtime_supported():
        command.extend(["--extra", "onnx"])
    return subprocess.call(command, cwd=paths.install_root) == 0  # noqa: S603


def cmd_doctor(args: argparse.Namespace) -> int:
    """Diagnose and optionally repair installation."""
    paths = resolve_paths()
    if bool(args.fix):
        state = read_state(paths)
        if state is not None and not process_exists(state.pid):
            clear_state(paths)
        write_cli_shim(paths)
        paths.data_dir.mkdir(parents=True, exist_ok=True)
        if (
            bool(args.recreate_env)
            or not (paths.studio_dir / ".venv").is_dir()
        ):
            sync_environment(paths, recreate=bool(args.recreate_env))
        if bool(args.download_model) and model_check(paths).status != "ok":
            download_onnx_model(paths.model_path, force=True)
    checks = doctor_checks(paths)
    print_checks(checks)
    if any(check.status == "fail" for check in checks):
        print("\nRepair: image-garden doctor --fix")
        return 1
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    """Repair the local install."""
    stop_code = cmd_stop(args)
    if stop_code != 0:
        return stop_code
    args.fix = True
    args.recreate_env = True
    args.download_model = True
    return cmd_doctor(args)


def remove_path(path: Path) -> None:
    """Remove file/symlink/tree when present."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def prompt_yes_no(question: str, *, default: bool = False) -> bool:
    """Prompt for confirmation."""
    suffix = "Y/n" if default else "y/N"
    try:
        answer = input(f"{question} [{suffix}] ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in {"y", "yes"}


def cmd_uninstall(args: argparse.Namespace) -> int:
    """Uninstall app files and optionally app data."""
    paths = resolve_paths()
    if not bool(args.yes):
        print(f"Install files: {install_base(paths)}")
        print(
            f"CLI shim: {paths.posix_shim if os.name != 'nt' else paths.windows_shim}"
        )
        print(
            f"App data: {'remove' if bool(args.remove_data) else 'keep'} {paths.data_dir}"
        )
        print("Photo library is not touched.")
        if not prompt_yes_no("Continue?"):
            print("Aborted.")
            return 0
    cmd_stop(args)
    remove_path(paths.windows_shim if os.name == "nt" else paths.posix_shim)
    remove_path(install_base(paths))
    if bool(args.remove_data):
        remove_path(paths.data_dir)
    elif bool(args.remove_model):
        remove_path(paths.model_path)
    print("Image Garden uninstalled.")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """Reset local app data."""
    paths = resolve_paths()
    if not bool(args.yes):
        print(f"This removes app data/cache in: {paths.data_dir}")
        print("Photo library is not touched.")
        if not prompt_yes_no("Continue?"):
            print("Aborted.")
            return 0
    cmd_stop(args)
    remove_path(paths.data_dir)
    print("Image Garden app data reset.")
    return 0


def install_base(paths: Paths) -> Path:
    """Return install base that may contain releases/current."""
    if paths.install_root.name == "current":
        return paths.install_root.parent
    if paths.install_root.parent.name == "releases":
        return paths.install_root.parent.parent
    return paths.install_root


def current_link(base: Path) -> Path:
    """Return current release pointer path."""
    return base / "current"


def releases_dir(base: Path) -> Path:
    """Return releases directory path."""
    return base / "releases"


def release_candidates(base: Path) -> list[Path]:
    """Return known release directories newest first."""
    root = releases_dir(base)
    if not root.is_dir():
        return []
    return sorted(
        [path for path in root.iterdir() if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def switch_current_release(base: Path, release: Path) -> None:
    """Switch current pointer to release."""
    link = current_link(base)
    if os.name == "nt":
        remove_path(link)
        try:
            link.symlink_to(release, target_is_directory=True)
        except OSError:
            shutil.copytree(release, link)
        return
    temp = base / ".current.tmp"
    with suppress(FileNotFoundError):
        temp.unlink()
    temp.symlink_to(Path("releases") / release.name, target_is_directory=True)
    temp.replace(link)


def cmd_rollback(args: argparse.Namespace) -> int:
    """Roll back current pointer to the previous release."""
    paths = resolve_paths()
    base = install_base(paths)
    releases = release_candidates(base)
    if len(releases) < 2:
        print("No previous release available.")
        return 1
    current = current_link(base)
    current_target = (
        current.resolve() if current.exists() else paths.install_root.resolve()
    )
    previous = next(
        (
            release
            for release in releases
            if release.resolve() != current_target
        ),
        None,
    )
    if previous is None:
        print("No previous release available.")
        return 1
    was_running = running_state(paths) is not None
    if was_running:
        stop_code = cmd_stop(args)
        if stop_code != 0:
            return stop_code
    switch_current_release(base, previous)
    print(f"Rolled back to: {previous.name}")
    if was_running or bool(args.restart):
        shim = paths.windows_shim if os.name == "nt" else paths.posix_shim
        start_command = [str(shim), "start", "--background"]
        if bool(args.no_open):
            start_command.append("--no-open")
        return subprocess.call(start_command)  # noqa: S603
    return 0


def platform_asset_name() -> str:
    """Return release asset for this platform."""
    import platform

    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin" and machine == "arm64":
        return "image-garden-macos-arm64.tar.gz"
    if system == "windows" and machine in {"amd64", "x86_64"}:
        return "image-garden-windows-x64.zip"
    msg = f"unsupported platform for update: {system} {machine}"
    raise RuntimeError(msg)


def cmd_update(args: argparse.Namespace) -> int:
    """Run the bootstrap installer again to update app files."""
    paths = resolve_paths()
    was_running = running_state(paths) is not None
    if was_running:
        stop_code = cmd_stop(args)
        if stop_code != 0:
            return stop_code
    base_url = str(
        args.release_base_url
        or os.environ.get("IMAGE_GARDEN_RELEASE_BASE_URL")
        or os.environ.get("CONSTELLATION_RELEASE_BASE_URL")
        or "https://github.com/IgaoGuru/image-garden/releases/latest/download"
    )
    release_url = str(
        args.release_url
        or os.environ.get("IMAGE_GARDEN_RELEASE_URL")
        or os.environ.get("CONSTELLATION_RELEASE_URL")
        or f"{base_url}/{platform_asset_name()}"
    )
    env = os.environ.copy()
    env["IMAGE_GARDEN_INSTALL_DIR"] = str(install_base(paths))
    env["IMAGE_GARDEN_RELEASE_URL"] = release_url
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell is None:
            print("error: PowerShell not found for update", file=sys.stderr)
            return 1
        script_url = f"{base_url}/install.ps1"
        command = [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"irm {script_url} | iex",
        ]
    else:
        script_url = f"{base_url}/install.sh"
        command = [
            "/bin/bash",
            "-c",
            f"$(curl -fsSL {sh_quote(script_url)})",
            "install.sh",
            "--recommended",
            "--no-launch",
        ]
    code = subprocess.call(command, env=env)  # noqa: S603
    if code != 0:
        return code
    if was_running or bool(args.restart):
        shim = paths.windows_shim if os.name == "nt" else paths.posix_shim
        start_command = [str(shim), "start", "--background"]
        if bool(args.no_open):
            start_command.append("--no-open")
        return subprocess.call(start_command)  # noqa: S603
    return 0


def cmd_model(args: argparse.Namespace) -> int:
    """Manage local model file."""
    paths = resolve_paths()
    action = str(args.model_action)
    if action == "status":
        print(f"Model: {'ready' if paths.model_path.is_file() else 'missing'}")
        print(paths.model_path)
        return 0
    if action in {"download", "redownload"}:
        download_onnx_model(paths.model_path, force=action == "redownload")
        return 0
    print(f"error: unknown model action: {action}", file=sys.stderr)
    return 1


def sh_quote(value: str) -> str:
    """Return POSIX shell-safe single-quoted string."""
    return "'" + value.replace("'", "'\\''") + "'"


def add_start_options(parser: argparse.ArgumentParser) -> None:
    """Add shared start options."""
    parser.add_argument(
        "--no-open", action="store_true", help="Do not open browser."
    )
    parser.add_argument(
        "--port", type=int, default=0, help="Port to bind; default auto."
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="Run in background and return to the shell.",
    )


def build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915
    """Build CLI parser."""
    parser = argparse.ArgumentParser(
        prog="image-garden",
        description="Manage the local Image Garden photo map app.",
    )
    parser.add_argument(
        "--version", action="store_true", help="Print version and exit."
    )
    subparsers = parser.add_subparsers(dest="command")

    start = subparsers.add_parser(
        "start", help="Start app in this terminal and open browser."
    )
    add_start_options(start)
    start.set_defaults(func=cmd_start)

    stop = subparsers.add_parser("stop", help="Stop app.")
    stop.set_defaults(func=cmd_stop)

    restart = subparsers.add_parser("restart", help="Restart app.")
    add_start_options(restart)
    restart.set_defaults(func=cmd_restart)

    status = subparsers.add_parser("status", help="Show app status.")
    status.set_defaults(func=cmd_status)

    open_parser = subparsers.add_parser(
        "open", help="Open running app in browser."
    )
    open_parser.add_argument(
        "--start", action="store_true", help="Start if stopped."
    )
    open_parser.add_argument(
        "--no-browser", action="store_true", help="Print URL only."
    )
    open_parser.add_argument(
        "--no-open", action="store_true", help=argparse.SUPPRESS
    )
    open_parser.add_argument(
        "--port", type=int, default=0, help=argparse.SUPPRESS
    )
    open_parser.set_defaults(func=cmd_open)

    logs = subparsers.add_parser("logs", help="Show app logs.")
    logs.add_argument(
        "-n", "--lines", type=int, default=100, help="Lines to show."
    )
    logs.add_argument(
        "-f", "--follow", action="store_true", help="Follow logs."
    )
    logs.add_argument(
        "--errors", action="store_true", help="Show last error log."
    )
    logs.set_defaults(func=cmd_logs)

    doctor = subparsers.add_parser("doctor", help="Diagnose installation.")
    doctor.add_argument(
        "--fix", action="store_true", help="Fix common problems."
    )
    doctor.add_argument(
        "--recreate-env",
        action="store_true",
        help="Recreate Python env during --fix.",
    )
    doctor.add_argument(
        "--download-model",
        action="store_true",
        help="Download missing model during --fix.",
    )
    doctor.set_defaults(func=cmd_doctor)

    repair = subparsers.add_parser("repair", help="Repair installation.")
    repair.set_defaults(func=cmd_repair)

    update = subparsers.add_parser("update", help="Update Image Garden.")
    update.add_argument(
        "--release-url", default=None, help="Override release archive URL."
    )
    update.add_argument(
        "--release-base-url", default=None, help="Override release base URL."
    )
    update.add_argument(
        "--restart", action="store_true", help="Restart after update."
    )
    update.add_argument(
        "--no-open", action="store_true", help=argparse.SUPPRESS
    )
    update.add_argument("--port", type=int, default=0, help=argparse.SUPPRESS)
    update.set_defaults(func=cmd_update)

    rollback = subparsers.add_parser(
        "rollback", help="Roll back to previous release."
    )
    rollback.add_argument(
        "--restart", action="store_true", help="Restart after rollback."
    )
    rollback.add_argument(
        "--no-open", action="store_true", help=argparse.SUPPRESS
    )
    rollback.add_argument(
        "--port", type=int, default=0, help=argparse.SUPPRESS
    )
    rollback.set_defaults(func=cmd_rollback)

    uninstall = subparsers.add_parser("uninstall", help="Uninstall app files.")
    uninstall.add_argument(
        "--remove-data",
        action="store_true",
        help="Also remove app data/cache/model.",
    )
    uninstall.add_argument(
        "--remove-model",
        action="store_true",
        help="Remove model but keep other app data.",
    )
    uninstall.add_argument("--yes", action="store_true", help="Do not prompt.")
    uninstall.set_defaults(func=cmd_uninstall)

    reset = subparsers.add_parser("reset", help="Reset local app data.")
    reset.add_argument("--yes", action="store_true", help="Do not prompt.")
    reset.set_defaults(func=cmd_reset)

    model = subparsers.add_parser("model", help="Manage local ONNX model.")
    model_subparsers = model.add_subparsers(dest="model_action", required=True)
    for name in ("status", "download", "redownload"):
        child = model_subparsers.add_parser(name)
        child.set_defaults(func=cmd_model)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if bool(args.version):
        print(read_version(resolve_paths().install_root))
        return 0
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    command = cast("Callable[[argparse.Namespace], int]", func)
    try:
        return command(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
