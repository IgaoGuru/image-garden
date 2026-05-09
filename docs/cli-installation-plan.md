# Implementation plan: ditch `.app` entirely. Make the CLI installation/app stellar.

## Top-level goal

Stop trying to feel like a desktop `.app` for now. Make Constellation a first-class CLI-managed local app that installs cleanly, can always be started/stopped/reopened, updates safely, explains failures, and leaves the user with one obvious command:

```bash
constellation start
```

The browser UI can remain the primary product UI, but the lifecycle must be owned by a real CLI.

## Product shape

Constellation becomes:

- a CLI command installed as `constellation`
- a local background server managed by that CLI
- a browser UI opened by that CLI
- user-local data in platform app-data folders
- release files under a versioned install directory
- no `.app`, no DMG, no signing/notarization work for now

Primary user commands:

```bash
constellation start       # start backend in background and open browser
constellation stop        # stop background backend
constellation restart     # stop then start
constellation status      # show running/stopped, URL, PID, version
constellation open        # open browser to running app, or explain if stopped
constellation logs        # tail/show logs
constellation doctor      # diagnose install/runtime/model/Python/deps
constellation update      # update release files safely
constellation uninstall   # remove app files; optionally data
constellation reset       # reset app data after confirmation
```

## Current state summary

Today:

1. `install.sh` / `install.ps1` installs `uv`, downloads release archive, swaps it into `~/.constellation`, then launches `scripts/install_tui.py`.
2. `install_tui.py` syncs Python deps, downloads ONNX model, writes a launcher inside `~/.constellation`, then optionally runs foreground `constellation-app`.
3. The user has no obvious stable command on PATH.
4. The app runs in the installer terminal, so start/stop/reopen is unclear.
5. Lifecycle state is not persisted except app data and generated files.

This plan preserves the release archive + uv approach short-term, but replaces the weak lifecycle UX with a real CLI contract.

## Target filesystem layout

### macOS / Linux

Install root:

```text
~/.constellation/
  current -> releases/<version-or-build-id>/
  releases/
    <version>/
      studio/
      viewer-dist/
      playview-dist/
      scripts/
      VERSION
  bin/
    constellation
```

User PATH shim:

```text
~/.local/bin/constellation -> ~/.constellation/bin/constellation
```

App data:

```text
~/Library/Application Support/Constellation/        # macOS
~/.local/share/constellation/                       # Linux fallback
```

Runtime state:

```text
<AppData>/runtime/
  server.pid
  server.json        # url, port, started_at, version, install_root
  server.log
  last-error.log
```

Model:

```text
<AppData>/models/clip-image-encoder.onnx
<AppData>/models/clip-image-encoder.onnx.json
```

### Windows

Install root can remain:

```text
%USERPROFILE%\.constellation\
```

PATH shim target:

```text
%LOCALAPPDATA%\Microsoft\WindowsApps\constellation.cmd
```

or simpler first iteration:

```text
%USERPROFILE%\.constellation\bin\constellation.ps1
```

and installer clearly prints the command/path. Windows PATH polish can be a second pass.

App data:

```text
%LOCALAPPDATA%\Constellation\
```

## Milestone 1: Stable CLI entrypoint

### Goal

After install, user can type:

```bash
constellation --help
```

from a new terminal.

### Work

1. Create a Python CLI module in Studio, e.g.

```text
studio/src/constellation_studio/cli.py
```

2. Add console script in `studio/pyproject.toml`:

```toml
constellation = "constellation_studio.cli:main"
```

3. Keep `constellation-app` as internal implementation command for now.
4. Installer writes a small shell shim to `~/.local/bin/constellation`:

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$HOME/.constellation/current"
exec uv --project "$ROOT/studio" run --no-dev constellation "$@"
```

5. Installer ensures `~/.local/bin` exists.
6. Installer detects whether `~/.local/bin` is on PATH.
   - If yes: success message uses `constellation start`.
   - If no: append shell instructions for zsh/bash, and also print direct fallback:

```bash
~/.local/bin/constellation start
```

### Acceptance criteria

- Fresh macOS install creates `~/.local/bin/constellation`.
- `constellation --help` works in the same terminal.
- Installer final screen says exactly how to start, stop, open, and get logs.

## Milestone 2: Background lifecycle commands

### Goal

The app is not tied to the installer terminal. It runs as a managed background process.

### Commands

#### `constellation start`

Behavior:

1. Run `doctor` preflight subset.
2. If already running, print URL and optionally open browser.
3. Choose a port. Prefer last known port if available; otherwise use ephemeral port.
4. Start backend in background.
5. Write PID/log/state files.
6. Wait until server is reachable.
7. Open browser unless `--no-open`.
8. Print:

```text
Constellation started
URL: http://127.0.0.1:54321/
Stop: constellation stop
Logs: constellation logs
```

Implementation detail:

- Prefer launching `constellation-app --no-open --port <port>` initially, but it currently blocks and prints URL.
- Better: expose backend/app launch function that can run under subprocess with known `--port`.
- Use `subprocess.Popen(..., start_new_session=True)` on POSIX.
- Redirect stdout/stderr to `<AppData>/runtime/server.log`.

#### `constellation stop`

Behavior:

1. Read PID file.
2. Confirm process belongs to Constellation before killing.
3. Send SIGTERM.
4. Wait a few seconds.
5. SIGKILL if needed with warning.
6. Remove stale state.

#### `constellation status`

Print:

```text
Status: running
URL: http://127.0.0.1:54321/
PID: 12345
Version: 0.1.1
Install: ~/.constellation/current
Data: ~/Library/Application Support/Constellation
Model: ready
```

If stale PID, say stale and suggest `constellation doctor --fix` or auto-clean.

#### `constellation open`

- If running, open stored URL in default browser.
- If stopped, say:

```text
Constellation is not running.
Run: constellation start
```

Optionally support `constellation open --start`.

#### `constellation logs`

- Default: print last 100 lines.
- `--follow` tails.
- `--errors` shows last error log if present.

### Acceptance criteria

- User can close installer terminal and later run `constellation open`.
- `constellation stop` reliably stops the backend.
- Repeated `start` is idempotent.
- Stale PID does not block restart.

## Milestone 3: Installer flow simplification

### Goal

Installer installs and configures. It does not foreground-run the app by default.

### New recommended flow

Bootstrap:

1. Install/find `uv`.
2. Download release.
3. Verify checksum.
4. Extract to versioned release dir.
5. Update `current` symlink/pointer.
6. Run dependency sync.
7. Install PATH shim.
8. Optionally download model.
9. Print next commands.

Final screen:

```text
Constellation installed.

Start:
  constellation start

Stop:
  constellation stop

Open later:
  constellation open

Logs:
  constellation logs
```

Do not launch by default unless the user picked `Launch now` or passed `--launch`.

### TUI changes

Current choices should become:

- Install / update recommended
- Install and launch now
- Repair install
- Advanced options
- Uninstall

Advanced options:

- download model now? default yes
- add CLI to PATH? default yes
- launch after install? default no
- reset Python environment? default no

### Acceptance criteria

- Installer no longer leaves app running ambiguously.
- The final screen is actionable and minimal.
- Non-interactive install uses recommended defaults and prints commands.

## Milestone 4: Update and rollback

### Goal

`constellation update` safely updates release files without breaking existing working installs.

### Design

Use versioned release directories:

```text
~/.constellation/releases/0.1.1/
~/.constellation/releases/0.1.2/
~/.constellation/current -> releases/0.1.2
```

Update process:

1. Check current version.
2. Download latest release metadata/archive.
3. Verify SHA256. Make checksum mandatory for public releases.
4. Extract into a new temp release dir.
5. Validate required files.
6. Run `uv sync` against new release.
7. Optionally run a lightweight smoke command:

```bash
uv --project <new>/studio run --no-dev constellation --version
```

8. Stop running server after asking or with `--restart`.
9. Atomically update `current` symlink.
10. Restart if it was running or if `--restart` was passed.
11. Keep previous release for rollback.

Rollback:

```bash
constellation rollback
```

switches `current` to previous release.

### Acceptance criteria

- Failed update leaves old version runnable.
- `update` never deletes app data.
- `status` shows installed/current version.

## Milestone 5: Doctor, repair, uninstall

### `constellation doctor`

Check:

- install root exists
- current release exists
- required release dirs exist
- uv found
- Python env syncable
- console entrypoint works
- app data writable
- model present or downloadable
- onnxruntime importable when embeddings enabled
- port availability / stale PID
- PATH shim exists and points correctly

Output should be friendly:

```text
✓ uv runtime
✓ Python 3.13 environment
✓ app files
✓ CLI shim
✓ app data writable
✓ ONNX Runtime
• model missing — run: constellation doctor --fix
```

`--fix` can:

- recreate shim
- remove stale PID
- run uv sync
- download missing model

### `constellation repair`

Equivalent to:

```bash
constellation stop
constellation doctor --fix --recreate-env
```

### `constellation uninstall`

Default:

- stop server
- remove install root and shim
- keep app data/photos/model

Options:

```bash
constellation uninstall --remove-data
constellation uninstall --remove-model
constellation uninstall --yes
```

Must clearly say photos are not touched.

### Acceptance criteria

- A friend can recover from most broken installs by running `constellation doctor --fix`.
- Uninstall is discoverable from `constellation --help`.

## Milestone 6: Model download hardening

### Current issue

The ONNX model downloads from Hugging Face over HTTPS, but there is no checksum/pinning/resume.

### Improvements

1. Add expected SHA256 for default model in `download_onnx.py`.
2. Verify after download.
3. Keep `.partial` files and support resume if server supports range requests.
4. Write metadata:

```json
{
  "url": "...",
  "sha256": "...",
  "bytes": 123,
  "downloaded_at": "...",
  "app_version": "..."
}
```

5. Add command:

```bash
constellation model status
constellation model download
constellation model redownload
```

Can be a later subcommand if too much for first pass.

### Acceptance criteria

- Corrupt model is detected.
- Failed/interrupted model download does not leave a fake ready state.

## Milestone 7: Release packaging cleanup

### Build-release changes

1. Write `VERSION` file into stage from `CONSTELLATION_VERSION` or git tag/commit.
2. Stage CLI-aware launchers/shims.
3. Stop staging misleading generic launchers that lack model args, or make them delegate to the new CLI.
4. Always generate `.sha256` for archives.
5. Consider generating a small release manifest:

```json
{
  "version": "0.1.2",
  "assets": {
    "macos-arm64": {
      "file": "constellation-macos-arm64.tar.gz",
      "sha256": "..."
    },
    "windows-x64": {
      "file": "constellation-windows-x64.zip",
      "sha256": "..."
    }
  }
}
```

### Bootstrap changes

- Public install should fail if checksum cannot be found, unless `--insecure-no-checksum` or env override for local dev.
- Preserve `CONSTELLATION_RELEASE_URL` local-file workflow.
- Make archive replacement versioned, not destructive.

### Acceptance criteria

- Release archive contains everything needed except Python wheels/model.
- Checksums are mandatory in normal public install.
- Local dev install remains easy.

## Milestone 8: Windows parity

Do macOS first. Then make Windows equivalent:

- `constellation.ps1` or `constellation.cmd` stable command
- process start/stop/status using PID file
- logs in `%LOCALAPPDATA%\Constellation\runtime\server.log`
- PATH instructions or automatic user PATH update
- PowerShell-friendly final screen

Avoid deep Windows service integration for now. A background user process is enough.

## Implementation order

Recommended sequence:

1. Add `constellation_studio.cli` with `start/stop/status/open/logs` for macOS.
2. Make installer write `~/.local/bin/constellation` shim.
3. Change installer final UX to emphasize CLI commands and stop foreground launch by default.
4. Add `doctor`.
5. Add versioned install layout + safer update.
6. Harden checksums/model download.
7. Add Windows parity.
8. Remove/de-emphasize old TUI paths once stable.

## First PR scope

Keep the first PR small enough to land:

- new `constellation` CLI script
- `start`, `stop`, `status`, `open`, `logs`
- runtime state/log files
- installer writes `~/.local/bin/constellation`
- installer final screen updated
- README updated

Defer:

- update/rollback
- model checksum/resume
- Windows PATH perfection
- release manifest

## Testing plan

### Unit tests

- app data path resolution
- runtime state read/write
- stale PID handling
- command parser behavior
- shim text generation
- doctor check results

### Integration tests

Use temp install/data dirs:

```bash
CONSTELLATION_INSTALL_DIR=/tmp/constellation-test \
CONSTELLATION_RELEASE_URL=file://$PWD/dist-release/constellation-macos-arm64.tar.gz \
./scripts/install.sh --recommended --no-launch
```

Then:

```bash
/tmp/home/.local/bin/constellation status
/tmp/home/.local/bin/constellation start --no-open
/tmp/home/.local/bin/constellation status
/tmp/home/.local/bin/constellation open --no-browser # if added for tests
/tmp/home/.local/bin/constellation logs
/tmp/home/.local/bin/constellation stop
```

### Manual friend-test script

Fresh Mac:

1. Paste install command.
2. Confirm installer ends with clear commands.
3. Open new Terminal.
4. Run `constellation start`.
5. Close browser.
6. Run `constellation open`.
7. Run `constellation stop`.
8. Run `constellation status`.
9. Run `constellation uninstall`.

If any step requires repo knowledge or guessing paths, the flow failed.

## UX copy principles

- Always show the next command.
- Never say only “installed successfully”; say how to start.
- Never leave a foreground server running without saying how to stop.
- Prefer “app files”, “photo library”, “local data” over vague technical names.
- Every failure should end with one recovery command, usually `constellation doctor --fix`.

## Non-goals for now

- Signed `.app`
- DMG packaging
- Sparkle updates
- Homebrew tap
- launchd service
- admin privileges
- system-wide install

Those can come later after the CLI lifecycle is excellent.
