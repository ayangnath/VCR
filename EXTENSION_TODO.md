# VCR Chrome Extension — Build TODO

The Python pipeline (`main.py` + 7 modules) is the source of truth. The Chrome
extension runs the same pipeline in-browser via Pyodide so the .crx is fully
self-contained — no localhost server, no Python install, Web Store shippable.

## Architecture

```
[Chrome page]  <-->  [Content script]  <-->  [Popup UI]
                            |                     |
                            +----------+----------+
                                       v
                          [Offscreen document]
                                       |
                                       v
                        [Pyodide: numpy + lxml + Pillow]
                                       |
                                       v
                           [main.py + 6 modules]
```

Why Pyodide over a localhost Flask server:
- Chrome Web Store distribution requires a self-contained .crx. Users won't
  install Python or run a server, and MV3 disallows extensions whose core
  function depends on a localhost process.
- All three third-party deps (numpy, lxml, Pillow) are first-class Pyodide
  packages. lxml usage in `svg_parser.py` is shallow (`etree.QName`,
  `etree.XMLParser(recover=True)`, `etree.parse`) — works as-is.
- Tradeoff: ~15 MB bundle and ~3–5 s cold start on first popup-open.
  Mitigated by warming Pyodide on extension install.

Why an offscreen document (the "MV3 quirk"): MV3 background code runs in a
service worker whose CSP blocks Pyodide's dynamic WASM init. Spawning a
hidden offscreen document via `chrome.offscreen.createDocument()` gives us a
normal page CSP where Pyodide boots fine. Invisible to the user.

## Pyodide host (`extension/offscreen/`)

- [ ] `offscreen.html` — minimal page that loads bundled `pyodide.js`.
- [ ] `offscreen.js`:
  - On load, `loadPyodide()`, then `pyodide.loadPackage(["numpy", "lxml",
    "Pillow"])`.
  - Copy the 7 pipeline `.py` files into Pyodide's virtual FS at startup
    (bundled as static assets under `extension/pipeline/`).
  - Expose two message handlers (via `chrome.runtime.onMessage`):
    - `detect({svg, cvd_type})` → run classifier + data signal extractor +
      reconciler, return `{palette_type, n_colors, mismatch,
      candidates: [{name, meta, colors|stops}], color_mapping}`.
    - `repair({svg, cvd_type, candidate_id})` → run recolorer for the
      chosen candidate, return full `{originalHex → newHex}` map.
- [ ] Cache results per SVG hash so cycling palettes is instant.
- [ ] Warm-up: kick off Pyodide boot in the service worker's `onInstalled`
      handler so the first real user click feels instant.

## Pipeline packaging (`extension/pipeline/`)

- [ ] Copy `color_science.py`, `svg_parser.py`, `classifier.py`,
      `data_signal_extractor.py`, `reconciler.py`, `invariant_tests.py`,
      `recolorer.py` verbatim.
- [ ] Add a thin `bridge.py` that exposes `detect_json(svg_str, cvd_type)`
      and `repair_json(svg_str, cvd_type, candidate_id)` returning JSON-safe
      dicts, so the JS side never touches Python objects directly.
- [ ] Keep `main.py` out of the bundle — it's the CLI entrypoint, not used
      by the extension.

## Chrome extension (`extension/`)

- [ ] `manifest.json` (MV3): `permissions: ["activeTab", "scripting",
      "offscreen", "storage"]`. No `host_permissions` needed (no network).
- [ ] Bundle Pyodide locally under `extension/vendor/pyodide/` — MV3 bans
      remote code, so no CDN. Pull the matching version's `pyodide.js`,
      `pyodide.asm.wasm`, `python_stdlib.zip`, plus the `.whl` files for
      numpy, lxml, Pillow.
- [ ] `popup.html` — start from `extension_mockup.html`; split inline
      `<script>`/`<style>` into `popup.js` / `popup.css` (MV3 forbids inline
      scripts).
- [ ] `popup.js`:
  - On open, message content script → get list of detected SVGs.
  - For each SVG, message offscreen doc → `detect`. Populate dropdown +
    swatches.
  - Toggle / palette nav → message content script with `{svgId, mapping}`.
- [ ] `content.js`:
  - On load, find all `<svg>` nodes containing fill colors (skip tiny icons).
  - Maintain a `Map<svgId, Map<element, originalFill>>` so toggle-off
    restores.
  - Apply mapping by walking each SVG and rewriting `fill=` / `style.fill`
    on elements whose original color is in the mapping.
- [ ] `background.js` (service worker): handles `chrome.offscreen` lifecycle
      — create the offscreen doc on first request, keep it alive, route
      messages between popup/content and offscreen.

## Open questions / risks

- [ ] How to identify "data marks" vs axes from the content script side?
      Two options: (a) server returns a list of CSS selectors / element paths
      to recolor, (b) returns a `{originalHex → newHex}` map and the
      content script swaps every matching fill. Option (b) is simpler but
      could recolor a non-data element that happens to share a color.
      Start with (b), revisit if it causes problems.
- [ ] Multi-SVG pages: do we run pipeline on all of them at popup-open time
      (slow) or lazily on selection (laggy)? Probably batch in the offscreen
      doc with a worker queue.
- [ ] CVD-type switch: re-run `detect` from scratch since simulation
      matrices change.
- [ ] Spot-check flow (DR5): out of scope for v1, can be a static page.
- [ ] D3/Vega charts may re-render on hover/resize and overwrite our fills.
      Mitigation: attach a `MutationObserver` per SVG that re-applies the
      cached mapping whenever a watched node's fill changes. Decide whether
      this is v1 or v2.
- [ ] Pyodide cold start (~3–5 s) on first invocation. Mitigation: warm in
      `onInstalled`. Worst case, show a one-time "Loading Python runtime…"
      spinner in the popup.
- [ ] Bundle size (~15 MB). Acceptable for the Web Store; verify by
      packaging a dry-run .crx before deeper UI work.
- [ ] Raster legend recolor in `svg_parser._recolor_raster_legends` does a
      pixel-by-pixel Python loop. Fine in CPython, may be too slow under
      Pyodide for large legends. If it bites, vectorize with numpy
      (independent of the port — same fix helps the CLI too).

## Out of scope for v1

- Bivariate palette repair.
- Tritanopia (pipeline supports it; UI just needs the option enabled).
- Persisting user choices across sessions.
- Native rewrite of the pipeline in JS/WASM (Pyodide is the bridge for now;
  a future rewrite is mentioned in Ch 6).
