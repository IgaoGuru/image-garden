#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Constellation"
INSTALL_DIR="${CONSTELLATION_INSTALL_DIR:-$HOME/.constellation}"
RELEASE_URL="${CONSTELLATION_RELEASE_URL:-}"
GIT_REPO="${CONSTELLATION_GIT_REPO:-https://github.com/constellation/constellation.git}"

printf '✦ %s installer\n\n' "$APP_NAME"
printf 'Install directory: %s\n' "$INSTALL_DIR"

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

install_uv() {
  if need_cmd uv; then
    printf '✓ uv found: %s\n' "$(command -v uv)"
    return
  fi
  printf 'Installing uv…\n'
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  if ! need_cmd uv; then
    printf 'error: uv installation did not put uv on PATH. Restart your shell and rerun this installer.\n' >&2
    exit 1
  fi
  printf '✓ uv installed\n'
}

download_app() {
  mkdir -p "$INSTALL_DIR"
  if [ -n "$RELEASE_URL" ]; then
    tmp="$(mktemp -d)"
    printf 'Downloading Constellation release…\n'
    curl -fL "$RELEASE_URL" -o "$tmp/constellation.tar.gz"
    tar -xzf "$tmp/constellation.tar.gz" -C "$INSTALL_DIR" --strip-components=1
    rm -rf "$tmp"
    return
  fi

  if [ -d "$INSTALL_DIR/.git" ]; then
    printf 'Updating existing checkout…\n'
    git -C "$INSTALL_DIR" pull --ff-only
    return
  fi

  if ! need_cmd git; then
    printf 'error: git is required when CONSTELLATION_RELEASE_URL is not set.\n' >&2
    printf 'Use a release installer URL or install git and rerun.\n' >&2
    exit 1
  fi
  printf 'Cloning Constellation…\n'
  git clone "$GIT_REPO" "$INSTALL_DIR"
}

install_uv
download_app

cd "$INSTALL_DIR"
printf 'Preparing local Python environment…\n'
uv --directory studio sync --inexact --extra onnx

if [ -f package.json ] && need_cmd pnpm; then
  printf 'Building viewer assets…\n'
  pnpm install --frozen-lockfile
  pnpm --filter @constellation/viewer build
else
  printf 'Viewer build skipped; using bundled viewer-dist if present.\n'
fi

printf '\nStarting Constellation…\n'
uv --project studio run constellation-app "$@"
