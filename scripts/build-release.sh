#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${CONSTELLATION_VERSION:-dev}"
OUT_DIR="${CONSTELLATION_RELEASE_DIR:-$ROOT/dist-release}"
STAGE="$OUT_DIR/constellation-$VERSION"
ARCHIVE="$OUT_DIR/constellation-$VERSION.tar.gz"

rm -rf "$STAGE"
mkdir -p "$STAGE"

cd "$ROOT"
printf 'Building viewer assets…\n'
pnpm install --frozen-lockfile
pnpm --filter @constellation/viewer build

if [ ! -f models/clip-image-encoder.onnx ]; then
  printf 'warning: models/clip-image-encoder.onnx not found.\n'
  printf '         Run `pnpm studio:download-onnx` before a consumer release to bundle default semantic embeddings.\n'
fi

printf 'Preparing Python package lock/environment metadata…\n'
uv --directory studio lock

printf 'Staging release files…\n'
mkdir -p "$STAGE/studio" "$STAGE/viewer-dist" "$STAGE/scripts"
if [ -d models ]; then
  mkdir -p "$STAGE/models"
  rsync -a models/ "$STAGE/models/"
fi
rsync -a \
  --exclude '.venv' \
  --exclude '.pytest_cache' \
  --exclude '__pycache__' \
  studio/ "$STAGE/studio/"
rsync -a packages/viewer/dist/ "$STAGE/viewer-dist/"
cp scripts/install.sh scripts/install.ps1 "$STAGE/scripts/"
cp README.md spec.md package.json pnpm-lock.yaml pnpm-workspace.yaml "$STAGE/"

cat > "$STAGE/constellation" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec uv --project "$ROOT/studio" run constellation-app --viewer-dist "$ROOT/viewer-dist" "$@"
SH
chmod +x "$STAGE/constellation"

cat > "$STAGE/constellation.ps1" <<'PS1'
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
uv --project (Join-Path $Root "studio") run constellation-app --viewer-dist (Join-Path $Root "viewer-dist") @args
PS1

printf 'Writing archive %s…\n' "$ARCHIVE"
tar -C "$OUT_DIR" -czf "$ARCHIVE" "constellation-$VERSION"
printf 'Release staged at %s\n' "$STAGE"
printf 'Archive written to %s\n' "$ARCHIVE"
