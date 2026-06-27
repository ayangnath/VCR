#!/usr/bin/env bash
# Trim the full Pyodide release (downloaded by fetch_pyodide.sh into
# ./pyodide/, ~450MB of every package Pyodide ships) down to just what this
# extension loads at runtime: the core engine (pyodide.js + the
# pyodide.asm.js glue it dynamically imports to instantiate the .wasm
# binary -- not optional, despite the name) plus numpy/lxml/pillow wheels.
# MV3 forbids loading WASM/JS from a remote origin, so the trimmed set is
# what actually gets bundled into the extension under ./runtime/.
#
# Run after fetch_pyodide.sh (or after bumping its PYODIDE_VERSION):
#   ./scripts/vendor_pyodide.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC="$EXT_DIR/pyodide"
DEST="$EXT_DIR/runtime"

if [[ ! -d "$SRC" ]]; then
  echo "No $SRC found. Run ./scripts/fetch_pyodide.sh first." >&2
  exit 1
fi

rm -rf "$DEST"
mkdir -p "$DEST"

cp "$SRC/pyodide.js" "$DEST/"
cp "$SRC/pyodide.asm.js" "$DEST/"
cp "$SRC/pyodide.asm.wasm" "$DEST/"
cp "$SRC/python_stdlib.zip" "$DEST/"
cp "$SRC/pyodide-lock.json" "$DEST/"

# Resolve wheel filenames from pyodide-lock.json so a Pyodide version bump
# doesn't require editing this script.
PACKAGES=(numpy lxml pillow)
for pkg in "${PACKAGES[@]}"; do
  file_name=$(python3 -c "
import json
d = json.load(open('$SRC/pyodide-lock.json'))
print(d['packages']['$pkg']['file_name'])
")
  cp "$SRC/$file_name" "$DEST/"
done

echo "Vendored runtime at $DEST"
echo "Size: $(du -sh "$DEST" | cut -f1)"
