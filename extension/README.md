# VCR Chrome extension

MV3 extension that talks to the local Flask server at
`http://127.0.0.1:5000` and recolors SVGs on the page.

## Setup

1. Start the server:
   ```bash
   pip install -r ../server/requirements.txt
   python ../server/app.py
   ```
   Verify with `curl http://127.0.0.1:5000/health` → `{"ok": true}`.

2. Load the unpacked extension:
   - Open `chrome://extensions/`
   - Enable **Developer mode** (top right)
   - Click **Load unpacked** → select this `extension/` directory

3. (Only if you want to test on `file://` SVGs) From `chrome://extensions/`,
   click **Details** on VCR and toggle **Allow access to file URLs**.

## Use

Open a page with an SVG chart (e.g. drag any file from `../input_svgs/`
into Chrome). Click the VCR icon. The popup scans the page, sends each
chart to the server, and shows the detected palette and the recolored
replacement. Flip **Show correction** to apply the mapping in place;
flip it off to revert.

## File layout

- `manifest.json` — MV3 manifest, requests `activeTab` + host permission
  for `127.0.0.1:5000`.
- `popup.html` / `popup.css` / `popup.js` — popup UI.
- `content.js` — discovers SVGs ≥60×60 px on the page, serializes them
  for the server, and applies / reverts mappings.

## Known limitations (v1)

- **Server must be running.** No fallback; the popup will surface
  "Server not reachable" if `:5000` is down.
- **CSS-styled fills are ignored.** Only `fill=` / `stroke=` attributes
  and inline `style="fill:…"` are remapped. Stylesheet rules are not.
- **Re-rendering charts wipe corrections.** D3 / Vega charts that
  re-draw on hover or resize will lose the recoloring; toggling off then
  back on re-applies it. A `MutationObserver` is the obvious fix.
- **Single corrected palette per SVG.** The server returns one mapping;
  candidate cycling and Path A/B reconciliation are not exposed yet.
- **No iframe support.** `activeTab` cannot reach embedded iframes
  (Observable, Tableau).
