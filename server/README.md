# VCR — local server + Chrome extension

Thin wrappers around the VCR pipeline. Split into two parts:

- `server/` — Flask app that wraps the Python pipeline in a `/detect` endpoint.
- `extension/` — MV3 Chrome extension; popup UI + content script talk to the
  server and apply the returned color mapping to SVGs on the page.

## Running it

### 1. Start the server

```bash
pip install -r server/requirements.txt
python server/app.py
```

The pipeline itself also needs `numpy`, `lxml`, and `Pillow`, which are listed
in `requirements.txt` for convenience.

The server listens on `http://127.0.0.1:5000`. Verify with:

```bash
curl http://127.0.0.1:5000/health
# → {"ok": true}
```

### 2. Load the extension

1. Open `chrome://extensions/`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** and select the `extension/` directory
4. Pin the extension for convenience

### 3. Use it

Navigate to a page with an SVG chart (try one of the files in `input_svgs/`
opened as `file://`, or an `.svg` page from Wikimedia Commons). Click the VCR
icon. The popup scans the page, sends each chart's SVG to the local server,
and shows the detected palette and the recolored replacement. Flipping the
**Show correction** toggle applies the mapping in-place on the page.

## API

### POST `/detect`

Request:
```json
{ "svg": "<svg>…</svg>", "cvd_type": "deutan" }
```

Response:
```json
{
  "status": "recolored",
  "palette_type": "categorical",
  "n_colors": 4,
  "original_palette": ["#e53935", "#43a047", "#fb8c00", "#8e24aa"],
  "mapping": { "#e53935": "#0072b2", "…": "…" },
  "new_palette": ["#0072b2", "#e69f00", "#56b4e9", "#009e73"],
  "mismatch": false,
  "mismatch_reason": null,
  "warnings": []
}
```

`cvd_type` is one of `protan`, `deutan`, `tritan`. Results are cached in
memory keyed on `(sha1(svg), cvd_type)`, so cycling between CVD types or
re-opening the popup doesn't re-run the pipeline.

### GET `/health`

Returns `{"ok": true}` when the server is up.

## Known limitations

- **Re-rendering charts overwrite the applied colors.** D3 / Vega / Observable
  charts often re-render on hover or resize. The content script does not yet
  install a `MutationObserver`, so corrections can be wiped and the user has
  to re-toggle. This is the first thing to add if the demo page uses an
  interactive chart.
- **CSS fills are not handled.** Only colors set via `fill=` / `stroke=` or
  inline `style="fill:…"` are remapped. Colors defined in `<style>` blocks or
  external stylesheets are ignored.
- **No iframe support.** Many embedded charts (Observable, Tableau) render in
  iframes, which the content script cannot reach with `activeTab`.
- **Single candidate palette.** The server returns one corrected palette per
  SVG — the first that passed verification during the pipeline's three repair
  attempts. The mockup's palette-cycling UI is not wired up.
- **CORS uses `*`.** Fine for local dev; tighten before exposing the server
  to anything other than localhost.
