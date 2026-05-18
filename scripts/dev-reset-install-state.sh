#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Image Garden"
LEGACY_APP_NAME="Constellation"
DEFAULT_INSTALL_DIR="$HOME/.image-garden"
LEGACY_INSTALL_DIR="$HOME/.constellation"
INSTALL_DIR="${IMAGE_GARDEN_INSTALL_DIR:-${CONSTELLATION_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}}"
KEEP_DATA=0
YES=0
BUNDLE=0
RUN_INSTALL=0
DATA_DIR=""
LEGACY_DATA_DIR=""
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE_DIR="${IMAGE_GARDEN_RELEASE_DIR:-${CONSTELLATION_RELEASE_DIR:-$ROOT/dist-release}}"
PUBLIC_INSTALL_URL="${IMAGE_GARDEN_INSTALL_SCRIPT_URL:-https://github.com/IgaoGuru/image-garden/releases/latest/download/install.sh}"
INSTALL_ARGS=()

usage() {
  cat <<'EOF'
Reset local Image Garden install state for end-to-end installer testing.

Usage:
  scripts/dev-reset-install-state.sh [options] [-- installer args...]

Common full public install test:
  scripts/dev-reset-install-state.sh --yes --install

Deletes:
  - install dir: ~/.image-garden or IMAGE_GARDEN_INSTALL_DIR
  - legacy install dir: ~/.constellation
  - CLI shims: ~/.local/bin/image-garden and ~/.local/bin/constellation
  - app data: ~/Library/Application Support/Image Garden on macOS
              $XDG_DATA_HOME/image-garden or ~/.local/share/image-garden on Linux
  - legacy app data: ~/Library/Application Support/Constellation on macOS
                     $XDG_DATA_HOME/constellation or ~/.local/share/constellation on Linux

Does not delete uv, uv cache, Python cache, Homebrew, photos, or repo files.

Options:
  -y, --yes          do not prompt
  --install          run the public latest installer after reset
  --keep-data        keep app data/database/assets/model; delete install dirs and shims only
  --bundle           rebuild dist-release after reset
  --install-dir DIR  override Image Garden install dir
  --data-dir DIR     override Image Garden app data dir
  --install-url URL  override public install.sh URL used by --install
  -h, --help         show help

Any arguments after -- are forwarded to install.sh when --install is used.
Example:
  scripts/dev-reset-install-state.sh --yes --install -- --recommended --no-launch
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -y|--yes)
      YES=1
      shift
      ;;
    --install)
      RUN_INSTALL=1
      shift
      ;;
    --keep-data)
      KEEP_DATA=1
      shift
      ;;
    --bundle)
      BUNDLE=1
      shift
      ;;
    --no-bundle)
      BUNDLE=0
      shift
      ;;
    --install-dir)
      if [ "$#" -lt 2 ]; then
        printf 'error: --install-dir needs a value\n' >&2
        exit 1
      fi
      INSTALL_DIR="$2"
      shift 2
      ;;
    --data-dir)
      if [ "$#" -lt 2 ]; then
        printf 'error: --data-dir needs a value\n' >&2
        exit 1
      fi
      DATA_DIR="$2"
      shift 2
      ;;
    --install-url)
      if [ "$#" -lt 2 ]; then
        printf 'error: --install-url needs a value\n' >&2
        exit 1
      fi
      PUBLIC_INSTALL_URL="$2"
      shift 2
      ;;
    --)
      shift
      INSTALL_ARGS=("$@")
      break
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'error: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

app_data_dir() {
  name="$1"
  slug="$2"
  case "$(uname -s)" in
    Darwin)
      printf '%s\n' "$HOME/Library/Application Support/$name"
      ;;
    Linux)
      printf '%s\n' "${XDG_DATA_HOME:-$HOME/.local/share}/$slug"
      ;;
    *)
      printf '%s\n' "$HOME/.local/share/$slug"
      ;;
  esac
}

if [ -z "$DATA_DIR" ]; then
  DATA_DIR="$(app_data_dir "$APP_NAME" "image-garden")"
fi
if [ -z "$LEGACY_DATA_DIR" ]; then
  LEGACY_DATA_DIR="$(app_data_dir "$LEGACY_APP_NAME" "constellation")"
fi

IMAGE_GARDEN_SHIM="$HOME/.local/bin/image-garden"
LEGACY_SHIM="$HOME/.local/bin/constellation"

printf '✦ Reset %s install state\n\n' "$APP_NAME"
printf 'Will delete:\n'
printf '  install dir:        %s\n' "$INSTALL_DIR"
printf '  legacy install dir: %s\n' "$LEGACY_INSTALL_DIR"
printf '  CLI shim:           %s\n' "$IMAGE_GARDEN_SHIM"
printf '  legacy CLI shim:    %s\n' "$LEGACY_SHIM"
if [ "$KEEP_DATA" -eq 0 ]; then
  printf '  app data:           %s\n' "$DATA_DIR"
  printf '  legacy app data:    %s\n' "$LEGACY_DATA_DIR"
else
  printf '  app data:           kept\n'
  printf '  legacy app data:    kept\n'
fi
printf '\nWill keep:\n'
printf '  uv binary/cache\n'
printf '  system Python/Homebrew\n'
printf '  photo library\n'
printf '  repo files\n'
if [ "$BUNDLE" -eq 1 ]; then
  printf '\nWill rebuild:\n'
  printf '  %s via pnpm release:bundle\n' "$BUNDLE_DIR"
fi
if [ "$RUN_INSTALL" -eq 1 ]; then
  printf '\nWill run public installer:\n'
  printf '  %s\n' "$PUBLIC_INSTALL_URL"
fi
printf '\n'

if [ "$YES" -ne 1 ]; then
  if [ ! -t 0 ]; then
    printf 'error: refusing to delete without --yes in non-interactive mode\n' >&2
    exit 1
  fi
  printf 'Delete these paths? [y/N] '
  read -r answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) printf 'Aborted.\n'; exit 0 ;;
  esac
fi

stop_if_available() {
  shim="$1"
  if [ -x "$shim" ]; then
    "$shim" stop >/dev/null 2>&1 || true
  fi
}

remove_path() {
  path="$1"
  if [ -e "$path" ] || [ -L "$path" ]; then
    rm -rf "$path"
    printf '✓ removed %s\n' "$path"
  else
    printf '• not found %s\n' "$path"
  fi
}

printf 'Stopping running app instances if known shims exist…\n'
stop_if_available "$IMAGE_GARDEN_SHIM"
stop_if_available "$LEGACY_SHIM"

if pgrep -f '[i]mage-garden|[c]onstellation-app|[c]onstellation_studio|[C]onstellation backend' >/dev/null 2>&1; then
  printf 'warning: an Image Garden/Constellation process may still be running. Stop it if deletion fails.\n'
fi

remove_path "$INSTALL_DIR"
remove_path "$LEGACY_INSTALL_DIR"
remove_path "$IMAGE_GARDEN_SHIM"
remove_path "$LEGACY_SHIM"
if [ "$KEEP_DATA" -eq 0 ]; then
  remove_path "$DATA_DIR"
  remove_path "$LEGACY_DATA_DIR"
fi

if [ "$BUNDLE" -eq 1 ]; then
  printf '\nRebuilding local release bundle…\n'
  (cd "$ROOT" && pnpm release:bundle)
fi

printf '\nReset done.\n'
printf 'Public macOS installer command:\n'
printf '  /bin/bash -c "$(curl -fsSL %s)"\n' "$PUBLIC_INSTALL_URL"
if [ "$BUNDLE" -eq 1 ]; then
  printf 'Local macOS installer command:\n'
  printf '  IMAGE_GARDEN_RELEASE_URL="file://%s/image-garden-macos-arm64.tar.gz" ./scripts/install.sh\n' "$BUNDLE_DIR"
fi

if [ "$RUN_INSTALL" -eq 1 ]; then
  printf '\nRunning public installer…\n'
  /bin/bash -c "$(curl -fsSL "$PUBLIC_INSTALL_URL")" install.sh "${INSTALL_ARGS[@]}"
fi
