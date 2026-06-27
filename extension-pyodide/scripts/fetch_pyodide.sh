#!/usr/bin/env bash
# Download a Pyodide release into ./pyodide/ so the extension can ship a
# self-contained runtime (MV3 forbids loading WASM/JS from a remote origin).
#
# Run once after cloning:
#   ./scripts/fetch_pyodide.sh
#
# Re-run to refresh after bumping PYODIDE_VERSION below.

set -euo pipefail

PYODIDE_VERSION="0.29.3"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST="$EXT_DIR/pyodide"
TARBALL="pyodide-${PYODIDE_VERSION}.tar.bz2"
URL="https://github.com/pyodide/pyodide/releases/download/${PYODIDE_VERSION}/${TARBALL}"

if [[ -d "$DEST" ]]; then
  echo "Pyodide already present at $DEST. Delete it to refetch." >&2
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading $URL ..."
curl -fL "$URL" -o "$TMP/$TARBALL"

echo "Extracting ..."
tar -xjf "$TMP/$TARBALL" -C "$TMP"

# Tarball top-level dir is "pyodide/"
mv "$TMP/pyodide" "$DEST"

echo "Pyodide ${PYODIDE_VERSION} installed at $DEST"
echo "Size: $(du -sh "$DEST" | cut -f1)"
