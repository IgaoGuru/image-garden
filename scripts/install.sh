#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Constellation"
DEFAULT_INSTALL_DIR="$HOME/.constellation"
INSTALL_DIR="${CONSTELLATION_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
RELEASE_URL="${CONSTELLATION_RELEASE_URL:-}"
RELEASE_SHA256="${CONSTELLATION_RELEASE_SHA256:-}"
RELEASE_BASE_URL="${CONSTELLATION_RELEASE_BASE_URL:-https://github.com/constellation/constellation/releases/latest/download}"
UV_INSTALL_URL="${UV_INSTALL_URL:-https://astral.sh/uv/install.sh}"
TMP_DIR=""

cleanup() {
  if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

say() { printf '%s\n' "$*"; }
err() { printf 'error: %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

header() {
  printf '\033[2J\033[H' 2>/dev/null || true
  say "✦ $APP_NAME installer"
  say "Install folder: $INSTALL_DIR"
  say ""
}

platform_asset_name() {
  os_name="$(uname -s)"
  arch_name="$(uname -m)"
  case "$os_name" in
    Darwin)
      case "$arch_name" in
        arm64) say "constellation-macos-arm64.tar.gz" ;;
        *) err "unsupported macOS architecture: $arch_name. Apple Silicon macOS is supported for now."; exit 1 ;;
      esac
      ;;
    *) err "unsupported OS: $os_name. Use scripts/install.ps1 on Windows."; exit 1 ;;
  esac
}

ensure_downloader() {
  if have curl || have wget; then
    return
  fi
  err "need curl or wget to download $APP_NAME"
  exit 1
}

download_to() {
  url="$1"
  output="$2"
  if have curl; then
    curl -fL --retry 3 --connect-timeout 20 "$url" -o "$output"
  else
    wget -O "$output" "$url"
  fi
}

install_uv() {
  if have uv; then
    say "✓ uv found: $(command -v uv)"
    return
  fi
  if [ -x "$HOME/.local/bin/uv" ] || [ -x "$HOME/.cargo/bin/uv" ]; then
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if have uv; then
      say "✓ uv found: $(command -v uv)"
      return
    fi
  fi
  say "Installing uv runtime manager…"
  if have curl; then
    curl -LsSf "$UV_INSTALL_URL" | sh
  else
    wget -qO- "$UV_INSTALL_URL" | sh
  fi
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  if ! have uv; then
    err "uv install finished, but uv is not on PATH. Restart terminal and rerun installer."
    exit 1
  fi
  say "✓ uv installed: $(command -v uv)"
}

load_remote_sha256() {
  checksum_file="$1"
  if [ -n "$RELEASE_SHA256" ]; then
    return
  fi
  if download_to "$RELEASE_URL.sha256" "$checksum_file" >/dev/null 2>&1; then
    RELEASE_SHA256="$(awk '{print $1}' "$checksum_file")"
    say "✓ checksum file found"
  fi
}

verify_sha256() {
  file="$1"
  expected="$2"
  if [ -z "$expected" ]; then
    say "• checksum not provided; skipping verification"
    return
  fi
  if have shasum; then
    actual="$(shasum -a 256 "$file" | awk '{print $1}')"
  elif have sha256sum; then
    actual="$(sha256sum "$file" | awk '{print $1}')"
  else
    err "checksum requested, but no shasum/sha256sum found"
    exit 1
  fi
  if [ "$actual" != "$expected" ]; then
    err "checksum mismatch"
    err "expected: $expected"
    err "actual:   $actual"
    exit 1
  fi
  say "✓ checksum verified"
}

validate_extracted_release() {
  extracted="$1"
  for required in studio viewer-dist playview-dist scripts/install_tui.py; do
    if [ ! -e "$extracted/$required" ]; then
      err "release missing $required"
      exit 1
    fi
  done
}

install_extracted_release() {
  extracted="$1"
  parent="$(dirname "$INSTALL_DIR")"
  backup="$TMP_DIR/previous-install"
  mkdir -p "$parent"
  if [ -e "$INSTALL_DIR" ]; then
    mv "$INSTALL_DIR" "$backup"
  fi
  if ! mv "$extracted" "$INSTALL_DIR"; then
    if [ -e "$backup" ]; then
      mv "$backup" "$INSTALL_DIR"
    fi
    err "failed to install app files"
    exit 1
  fi
}

download_release() {
  asset_name="$(platform_asset_name)"
  if [ -z "$RELEASE_URL" ]; then
    RELEASE_URL="$RELEASE_BASE_URL/$asset_name"
  fi
  TMP_DIR="$(mktemp -d)"
  archive="$TMP_DIR/$asset_name"
  say "Downloading $APP_NAME release…"
  say "$RELEASE_URL"
  download_to "$RELEASE_URL" "$archive"
  load_remote_sha256 "$TMP_DIR/$asset_name.sha256"
  verify_sha256 "$archive" "$RELEASE_SHA256"
  say "Installing app files…"
  extract_dir="$TMP_DIR/extract"
  mkdir -p "$extract_dir"
  tar -xzf "$archive" -C "$extract_dir"
  extracted="$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  if [ -z "$extracted" ]; then
    err "release archive did not contain an app directory"
    exit 1
  fi
  validate_extracted_release "$extracted"
  install_extracted_release "$extracted"
}

run_tui() {
  tui="$INSTALL_DIR/scripts/install_tui.py"
  if [ ! -f "$tui" ]; then
    err "installer TUI missing: $tui"
    exit 1
  fi
  say "Starting installer UI…"
  if [ -t 0 ]; then
    uv run --no-project --python 3.13 "$tui" --app-dir "$INSTALL_DIR" "$@"
  elif ( : </dev/tty ) 2>/dev/null; then
    uv run --no-project --python 3.13 "$tui" --app-dir "$INSTALL_DIR" "$@" </dev/tty
  else
    say "No interactive terminal found; using recommended defaults."
    uv run --no-project --python 3.13 "$tui" --app-dir "$INSTALL_DIR" --recommended "$@"
  fi
}

main() {
  header
  ensure_downloader
  install_uv
  download_release
  run_tui "$@"
}

main "$@"
