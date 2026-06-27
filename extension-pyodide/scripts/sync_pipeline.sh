#!/usr/bin/env bash
# Re-copy the pipeline modules from the repo root into extension-pyodide/python/.
#
# server/app.py (v1) imports straight from the repo root, so editing e.g.
# classifier.py there takes effect immediately for v1. Pyodide (v2) runs in
# a sandboxed virtual filesystem with no access to the real disk -- it only
# ever sees the copies in extension-pyodide/python/, fetched in at boot. Run
# this after editing any pipeline file at the repo root so v2 picks it up.
#
#   ./scripts/sync_pipeline.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$EXT_DIR/.." && pwd)"
DEST="$EXT_DIR/python"

FILES=(
  classifier.py
  color_science.py
  data_signal_extractor.py
  invariant_tests.py
  main.py
  reconciler.py
  recolorer.py
  svg_parser.py
)

for f in "${FILES[@]}"; do
  cp "$REPO_ROOT/$f" "$DEST/$f"
done

echo "Synced ${#FILES[@]} pipeline files into $DEST"
echo "Note: pyodide_bridge.py is NOT in this list -- it only exists in"
echo "extension-pyodide/python/ and has no repo-root counterpart to sync from."
