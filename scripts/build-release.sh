#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_VERSION="$(cd "$ROOT" && git describe --tags --always --dirty 2>/dev/null || printf 'dev')"
VERSION="${IMAGE_GARDEN_VERSION:-${CONSTELLATION_VERSION:-$DEFAULT_VERSION}}"
OUT_DIR="${IMAGE_GARDEN_RELEASE_DIR:-${CONSTELLATION_RELEASE_DIR:-$ROOT/dist-release}}"
STAGE="$OUT_DIR/image-garden-$VERSION"
MACOS_ARCHIVE="$OUT_DIR/image-garden-macos-arm64.tar.gz"
WINDOWS_ARCHIVE="$OUT_DIR/image-garden-windows-x64.zip"

rm -rf "$STAGE"
mkdir -p "$STAGE"

cd "$ROOT"
printf 'Building web assets…\n'
pnpm install --frozen-lockfile
pnpm --filter @image-garden/viewer build
pnpm --filter @image-garden/playview build

printf 'ONNX model is not bundled; installer downloads it to app data on first run.\n'

printf 'Preparing Python package lock/environment metadata…\n'
uv --directory packages/studio lock

printf 'Staging release files…\n'
mkdir -p "$STAGE/studio" "$STAGE/viewer-dist" "$STAGE/playview-dist" "$STAGE/scripts"
rsync -a \
  --exclude '.venv' \
  --exclude '.ruff_cache' \
  --exclude '.pytest_cache' \
  --exclude '__pycache__' \
  --exclude 'tests' \
  packages/studio/ "$STAGE/studio/"
rsync -a packages/viewer/dist/ "$STAGE/viewer-dist/"
rsync -a packages/playview/dist/ "$STAGE/playview-dist/"
cp scripts/install.sh scripts/install.ps1 scripts/install_tui.py "$STAGE/scripts/"
cp README.md docs/spec.md package.json pnpm-lock.yaml pnpm-workspace.yaml "$STAGE/"
printf '%s\n' "$VERSION" > "$STAGE/VERSION"

cat > "$STAGE/image-garden" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export IMAGE_GARDEN_INSTALL_DIR="$ROOT"
exec uv --project "$ROOT/studio" run --no-dev image-garden "$@"
SH
chmod +x "$STAGE/image-garden"

cat > "$STAGE/Image Garden.cmd" <<'CMD'
@echo off
set Root=%~dp0
set IMAGE_GARDEN_INSTALL_DIR=%Root%
uv --project "%Root%studio" run --no-dev image-garden %*
CMD

printf 'Writing archives…\n'
tar -C "$OUT_DIR" -czf "$MACOS_ARCHIVE" "image-garden-$VERSION"
if command -v ditto >/dev/null 2>&1; then
  ditto -c -k --sequesterRsrc --keepParent "$STAGE" "$WINDOWS_ARCHIVE"
elif command -v zip >/dev/null 2>&1; then
  (cd "$OUT_DIR" && zip -qr "$WINDOWS_ARCHIVE" "image-garden-$VERSION")
else
  printf 'warning: zip/ditto not found; Windows zip archive skipped.\n'
fi
cp scripts/install.sh "$OUT_DIR/install.sh"
cp scripts/install.ps1 "$OUT_DIR/install.ps1"
sha256_value() {
  file="$1"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  else
    printf 'error: shasum or sha256sum required\n' >&2
    exit 1
  fi
}
write_sha256() {
  file="$1"
  (cd "$OUT_DIR" && printf '%s  %s\n' "$(sha256_value "$file")" "$(basename "$file")" > "$(basename "$file").sha256")
}
write_sha256 "$MACOS_ARCHIVE"
MACOS_SHA256="$(sha256_value "$MACOS_ARCHIVE")"
WINDOWS_SHA256=""
if [ -f "$WINDOWS_ARCHIVE" ]; then
  write_sha256 "$WINDOWS_ARCHIVE"
  WINDOWS_SHA256="$(sha256_value "$WINDOWS_ARCHIVE")"
fi
cat > "$OUT_DIR/release-manifest.json" <<JSON
{
  "version": "$VERSION",
  "assets": {
    "macos-arm64": {
      "file": "$(basename "$MACOS_ARCHIVE")",
      "sha256": "$MACOS_SHA256"
    },
    "windows-x64": {
      "file": "$(basename "$WINDOWS_ARCHIVE")",
      "sha256": "$WINDOWS_SHA256"
    }
  }
}
JSON
printf 'Release staged at %s\n' "$STAGE"
printf 'Archive written to %s\n' "$MACOS_ARCHIVE"
if [ -f "$WINDOWS_ARCHIVE" ]; then
  printf 'Archive written to %s\n' "$WINDOWS_ARCHIVE"
fi
printf 'Bootstrap installers written to %s/install.sh and %s/install.ps1\n' "$OUT_DIR" "$OUT_DIR"
