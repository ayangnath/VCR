# VCR Chrome extension — Pyodide (recommended)

MV3 extension that runs the VCR pipeline entirely in-browser via
[Pyodide](https://pyodide.org) — no localhost server, no Python install on
the user's machine. `../extension/` is a Flask-backed alternative (closer
to how this was originally built); see its README for that path.

## Setup

**Chrome Web Store listing is in progress** — once published, this will be
a normal install from the store. Until then, load it unpacked:

1. Fetch and vendor the Pyodide runtime (one-time, or after bumping the
   version in `scripts/fetch_pyodide.sh`):
   ```bash
   ./scripts/fetch_pyodide.sh    # downloads the full ~450MB release into ./pyodide/ (gitignored)
   ./scripts/vendor_pyodide.sh   # trims it to the ~17MB this extension actually loads, into ./runtime/ (gitignored)
   ```
2. Load the unpacked extension:
   - Open `chrome://extensions/`
   - Enable **Developer mode**
   - Click **Load unpacked** → select this `extension-pyodide/` directory

## Use

Open a page with an SVG chart, click the VCR icon. The first popup open
after install pays a one-time ~3-5s Pyodide boot (background.js warms it
on install, so most opens skip this). Flip **Show correction** to apply
the mapping in place; flip it off to revert.

## Architecture

```
popup.js --message--> background.js --message--> offscreen.js (Pyodide)
```

- **background.js** — MV3 service worker. Owns the offscreen document's
  lifecycle (a service worker's CSP blocks WASM init, hence the offscreen
  document) and routes messages tagged `target: "background"`.
- **offscreen.html/offscreen.js** — boots Pyodide from `runtime/`, loads
  numpy/lxml/pillow from the bundled wheels (no network), writes the
  pipeline modules from `python/` into Pyodide's filesystem, and exposes a
  `process-svg` handler that calls `pyodide_bridge.process_svg`.
- **python/** — the same 8 pipeline modules as the repo root, plus
  `pyodide_bridge.py`, a from-scratch port of `../server/app.py`'s
  `_build_response` (kept in sync by hand; there's no shared import since
  one runs under CPython+Flask and the other under Pyodide). Returns
  `json.dumps(...)` rather than a PyProxy to avoid JS/Python object
  conversion edge cases.
- **popup.js / content.js** — started as copies of `../extension/`'s, with
  `popup.js` swapping the `fetch(localhost)` call for a
  `chrome.runtime.sendMessage` to `background.js`. Both have since
  diverged to add real-world coverage: `content.js` now also detects
  `<img src="chart.svg">`-embedded charts (swapped via a Blob URL rather
  than element replacement) and runs in every frame (manifest sets
  `all_frames: true`), with `popup.js` discovering live frames via
  `chrome.scripting.executeScript` and keying state by `frameId:svgId` so
  same-named SVGs in different frames don't collide.
  `../extension/content.js` does not have these yet.

## Keeping the pipeline in sync

`../server/app.py` imports the pipeline modules straight from the repo
root, so editing e.g. `classifier.py` there takes effect immediately there.
This extension instead bundles **copies** under `python/`, fetched into
Pyodide's sandboxed virtual filesystem at boot — Pyodide has no access to
the real disk, so repo-root edits don't apply here until the copies are
refreshed. After editing any pipeline file at the repo root:

```bash
./scripts/sync_pipeline.sh
```

(`pyodide_bridge.py` is the one exception — it only exists in `python/`,
there's nothing at the repo root to sync it from.)

## Known risks

- **lxml version drift.** `../server/`'s venv pins lxml 5.3.1; Pyodide
  0.29.3 ships lxml 6.0.2. `corrected_svg` serialization may not be
  byte-identical between the two backends even though both call the same
  `tree.write(...)`. Verify against
  `test_pages/fixture_barchart12_deutan.json`; if bytes diverge, compare
  structurally (re-parse + diff elements/attributes) rather than treating
  it as a regression.
- **Raster legend recoloring** (`svg_parser._recolor_raster_legends`) is a
  pixel-by-pixel Python loop — untested for performance under Pyodide on
  large legends.
- **Offscreen document eviction.** `background.js` retries once if a
  message to the offscreen document fails, but this is largely untested
  against real Chrome eviction behavior.
