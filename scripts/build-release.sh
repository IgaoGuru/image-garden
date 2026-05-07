#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${CONSTELLATION_VERSION:-dev}"
OUT_DIR="${CONSTELLATION_RELEASE_DIR:-$ROOT/dist-release}"
STAGE="$OUT_DIR/constellation-$VERSION"
MACOS_ARCHIVE="$OUT_DIR/constellation-macos-arm64.tar.gz"
WINDOWS_ARCHIVE="$OUT_DIR/constellation-windows-x64.zip"

rm -rf "$STAGE"
mkdir -p "$STAGE"

cd "$ROOT"
printf 'Building web assets…\n'
pnpm install --frozen-lockfile
pnpm --filter @constellation/viewer build
pnpm --filter @constellation/playview build

printf 'ONNX model is not bundled; installer downloads it to app data on first run.\n'

printf 'Preparing Python package lock/environment metadata…\n'
uv --directory studio lock

printf 'Staging release files…\n'
mkdir -p "$STAGE/studio" "$STAGE/viewer-dist" "$STAGE/playview-dist" "$STAGE/scripts"
rsync -a \
  --exclude '.venv' \
  --exclude '.pytest_cache' \
  --exclude '__pycache__' \
  studio/ "$STAGE/studio/"
rsync -a packages/viewer/dist/ "$STAGE/viewer-dist/"
rsync -a playview/dist/ "$STAGE/playview-dist/"
cp scripts/install.sh scripts/install.ps1 scripts/install_tui.py "$STAGE/scripts/"
cp README.md spec.md package.json pnpm-lock.yaml pnpm-workspace.yaml "$STAGE/"

cat > "$STAGE/constellation" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec uv --project "$ROOT/studio" run --no-dev constellation-app --viewer-dist "$ROOT/viewer-dist" --playview-dist "$ROOT/playview-dist" "$@"
SH
chmod +x "$STAGE/constellation"

cat > "$STAGE/constellation.ps1" <<'PS1'
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
uv --project (Join-Path $Root "studio") run --no-dev constellation-app --viewer-dist (Join-Path $Root "viewer-dist") --playview-dist (Join-Path $Root "playview-dist") @args
PS1

printf 'Writing archives…\n'
tar -C "$OUT_DIR" -czf "$MACOS_ARCHIVE" "constellation-$VERSION"
if command -v ditto >/dev/null 2>&1; then
  ditto -c -k --sequesterRsrc --keepParent "$STAGE" "$WINDOWS_ARCHIVE"
elif command -v zip >/dev/null 2>&1; then
  (cd "$OUT_DIR" && zip -qr "$WINDOWS_ARCHIVE" "constellation-$VERSION")
else
  printf 'warning: zip/ditto not found; Windows zip archive skipped.\n'
fi
cp scripts/install.sh "$OUT_DIR/install.sh"
cp scripts/install.ps1 "$OUT_DIR/install.ps1"
write_sha256() {
  file="$1"
  if command -v shasum >/dev/null 2>&1; then
    (cd "$OUT_DIR" && shasum -a 256 "$(basename "$file")" > "$(basename "$file").sha256")
  elif command -v sha256sum >/dev/null 2>&1; then
    (cd "$OUT_DIR" && sha256sum "$(basename "$file")" > "$(basename "$file").sha256")
  fi
}
write_sha256 "$MACOS_ARCHIVE"
if [ -f "$WINDOWS_ARCHIVE" ]; then
  write_sha256 "$WINDOWS_ARCHIVE"
fi
printf 'Release staged at %s\n' "$STAGE"
printf 'Archive written to %s\n' "$MACOS_ARCHIVE"
if [ -f "$WINDOWS_ARCHIVE" ]; then
  printf 'Archive written to %s\n' "$WINDOWS_ARCHIVE"
fi
printf 'Bootstrap installers written to %s/install.sh and %s/install.ps1\n' "$OUT_DIR" "$OUT_DIR"
