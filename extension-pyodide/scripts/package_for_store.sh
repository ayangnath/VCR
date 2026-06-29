#!/usr/bin/env bash
# Build a clean, reviewable ZIP of extension-pyodide/ for Chrome Web Store
# upload. Ships only what the extension loads at runtime:
#   manifest.json background.js content.js offscreen.html offscreen.js
#   popup.html popup.js popup.css icons/ python/ runtime/
# Excludes dev-only material: scripts/, test_pages/, README.md,
# PRIVACY.md, STORE_LISTING.md, and the raw ~452MB pyodide/ download.
#
# Usage:
#   ./scripts/package_for_store.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$EXT_DIR"

if [[ ! -d runtime ]]; then
  echo "runtime/ missing — fetching and vendoring Pyodide first..."
  ./scripts/fetch_pyodide.sh
  ./scripts/vendor_pyodide.sh
fi

VERSION=$(python3 -c "import json; print(json.load(open('manifest.json'))['version'])")
DIST_DIR="$EXT_DIR/dist"
STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT

PACKAGE_ITEMS=(
  manifest.json
  background.js
  content.js
  offscreen.html
  offscreen.js
  popup.html
  popup.js
  popup.css
  icons
  python
  runtime
)

PKG_NAME="vcr-pyodide-${VERSION}"
mkdir -p "$STAGE_DIR/$PKG_NAME"
for item in "${PACKAGE_ITEMS[@]}"; do
  if [[ ! -e "$item" ]]; then
    echo "error: expected $item not found in $EXT_DIR" >&2
    exit 1
  fi
  cp -R "$item" "$STAGE_DIR/$PKG_NAME/"
done

# Strip filesystem cruft that cp -R may have carried over.
find "$STAGE_DIR/$PKG_NAME" -name ".DS_Store" -delete

mkdir -p "$DIST_DIR"
ZIP_PATH="$DIST_DIR/${PKG_NAME}.zip"
rm -f "$ZIP_PATH"

(cd "$STAGE_DIR/$PKG_NAME" && zip -rq "$ZIP_PATH" .)

echo "Packaged $ZIP_PATH"
echo "Size: $(du -sh "$ZIP_PATH" | cut -f1)"
echo "Contents:"
unzip -l "$ZIP_PATH" | tail -n +4 | grep -v '^ *-\+$\|files$'
