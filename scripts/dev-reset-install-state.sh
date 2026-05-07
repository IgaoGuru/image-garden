#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Constellation"
INSTALL_DIR="${CONSTELLATION_INSTALL_DIR:-$HOME/.constellation}"
KEEP_DATA=0
YES=0
BUNDLE=1
DATA_DIR=""
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE_DIR="${CONSTELLATION_RELEASE_DIR:-$ROOT/dist-release}"

usage() {
  cat <<'EOF'
Reset local Constellation install state for installer testing.

Usage:
  scripts/dev-reset-install-state.sh [--yes] [--keep-data] [--no-bundle] [--install-dir DIR] [--data-dir DIR]

Deletes:
  - install dir: ~/.constellation or CONSTELLATION_INSTALL_DIR
  - app data:    ~/Library/Application Support/Constellation on macOS
                 $XDG_DATA_HOME/constellation or ~/.local/share/constellation on Linux

Does not delete uv, uv cache, Python cache, Homebrew, photos, or repo files.

Options:
  -y, --yes          do not prompt
  --keep-data        keep app data/database/assets; delete install dir only
  --no-bundle        skip rebuilding dist-release after reset
  --install-dir DIR  override install dir
  --data-dir DIR     override app data dir
  -h, --help         show help
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -y|--yes)
      YES=1
      shift
      ;;
    --keep-data)
      KEEP_DATA=1
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
    --)
      shift
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
  case "$(uname -s)" in
    Darwin)
      printf '%s\n' "$HOME/Library/Application Support/$APP_NAME"
      ;;
    Linux)
      printf '%s\n' "${XDG_DATA_HOME:-$HOME/.local/share}/constellation"
      ;;
    *)
      printf '%s\n' "$HOME/.local/share/constellation"
      ;;
  esac
}

if [ -z "$DATA_DIR" ]; then
  DATA_DIR="$(app_data_dir)"
fi

printf '✦ Reset %s install state\n\n' "$APP_NAME"
printf 'Will delete:\n'
printf '  install dir: %s\n' "$INSTALL_DIR"
if [ "$KEEP_DATA" -eq 0 ]; then
  printf '  app data:    %s\n' "$DATA_DIR"
else
  printf '  app data:    kept\n'
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
printf '\n'

if pgrep -f '[c]onstellation-app|[c]onstellation_studio|[C]onstellation backend' >/dev/null 2>&1; then
  printf 'warning: Constellation may still be running. Stop it before reset if delete fails.\n\n'
fi

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

remove_path() {
  path="$1"
  if [ -e "$path" ]; then
    rm -rf "$path"
    printf '✓ removed %s\n' "$path"
  else
    printf '• not found %s\n' "$path"
  fi
}

remove_path "$INSTALL_DIR"
if [ "$KEEP_DATA" -eq 0 ]; then
  remove_path "$DATA_DIR"
fi

if [ "$BUNDLE" -eq 1 ]; then
  printf '\nRebuilding local release bundle…\n'
  (cd "$ROOT" && pnpm release:bundle)
fi

printf '\nReset done. Run installer again.\n'
if [ "$BUNDLE" -eq 1 ]; then
  printf 'Local macOS installer command:\n'
  printf '  CONSTELLATION_RELEASE_URL="file://%s/constellation-macos-arm64.tar.gz" ./scripts/install.sh\n' "$BUNDLE_DIR"
fi
