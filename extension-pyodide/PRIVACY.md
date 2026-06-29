# Privacy Policy — VCR: Visualization Color Repair

**Last updated: June 27, 2026**

VCR (Visualization Color Repair) is a browser extension that recolors SVG
data visualizations on web pages so they remain legible to people with
color vision deficiency (CVD). This policy covers the Pyodide-based
extension (no local server required).

## Summary

VCR makes no network requests and sends no data anywhere. Everything it
does happens inside your browser.

## What VCR does

When you click the VCR icon on a page, the extension scans the current
page for SVG charts, runs a color-correction pipeline against them
entirely inside your browser (via [Pyodide](https://pyodide.org), a
Python runtime compiled to WebAssembly that ships bundled with the
extension), and rewrites the SVG's colors in place so the chart stays
distinguishable under common forms of CVD (e.g., deuteranopia,
protanopia).

## Data collection

VCR does not collect, transmit, or sell any data. Specifically:

- **No network calls.** The extension's content security policy
  (`script-src 'self' 'wasm-unsafe-eval'`) does not permit loading code
  from, or sending data to, any remote server. The Pyodide runtime and
  all Python packages it uses (numpy, lxml, Pillow) are bundled inside
  the extension package itself — nothing is fetched at runtime.
- **No analytics or tracking.** There is no telemetry, crash reporting,
  or usage analytics of any kind.
- **No page content leaves your device.** SVG markup found on a page is
  processed locally, in memory, and the only output is the recolored SVG
  written back into that same page.
- **Local storage only.** The extension uses `chrome.storage.local` to
  remember one thing: your last-chosen color palette, so it can be
  reapplied automatically the next time you use VCR. This value is
  stored only on your device, is never transmitted anywhere, and is
  never synced to a Google account or any other device.

## Permissions

VCR requests the following Chrome extension permissions, each used only
for the purpose below:

- **activeTab / scripting** — to inject the recoloring logic into the
  page you're currently viewing, only when you invoke the extension.
- **storage** — to remember your palette preference locally (see above).
- **offscreen** — to run the Pyodide/WebAssembly runtime in a hidden
  document, since Manifest V3 service workers cannot host WASM directly.
- **Host access (`http://*/*`, `https://*/*`, `file:///*`)** — VCR needs
  to find and recolor SVG charts on whatever page you're viewing, and
  chart-bearing pages are not predictable in advance, so the extension
  requests broad host access rather than a fixed list of sites.
  `file:///*` access is **off by default** for all Chrome extensions;
  Chrome requires you to separately and explicitly enable "Allow access
  to file URLs" for VCR if you want it to work on local HTML/SVG files.

## Changes to this policy

If this policy changes, the updated version will be posted at this same
URL with a new "Last updated" date.

## Contact

Questions about this policy or the extension can be sent to
ayangnath@gmail.com.
