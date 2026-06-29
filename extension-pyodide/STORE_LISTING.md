Copy-paste reference for the Chrome Web Store Developer Dashboard listing.
Not loaded by the extension itself.

## Single purpose

VCR finds SVG charts on the current web page and recolors them so they
remain visually distinguishable to users with color vision deficiency.

## Short description (132 char max)

```
Recolors SVG charts on any page so they stay readable for colorblind users. Runs fully in-browser — no server, no network calls.
```
(130 characters)

## Detailed description

```
VCR (Visualization Color Repair) finds SVG data visualizations on the
current web page — bar charts, line charts, maps, and other SVG-based
charts — and recolors them so they remain distinguishable to people with
color vision deficiency (CVD), such as deuteranopia or protanopia.

Click the VCR icon on any page with an SVG chart. VCR analyzes the
chart's colors and existing visual encoding, then computes a replacement
palette that preserves the chart's structure while maximizing contrast
under common CVD types. Toggle the correction on or off at any time; the
original chart is restored when you switch it off.

How it works:
VCR runs its entire color-correction pipeline locally in your browser
using Pyodide, a Python runtime compiled to WebAssembly. There is no
companion server and no network requests of any kind — the runtime and
all of its dependencies are bundled inside the extension package itself.

What VCR does NOT do:
- It does not collect, transmit, or sell any data.
- It does not track your browsing.
- It does not modify anything on the page other than the SVG chart
  elements it recolors.

The only thing VCR remembers is your last-chosen color palette, stored
locally on your device (chrome.storage.local) so it can be reapplied
automatically next time — this value never leaves your browser.

VCR is built as part of an undergraduate thesis on signal-aware,
CVD-accessible SVG recoloring.

Full privacy policy: <PRIVACY_POLICY_URL>
Source code: https://github.com/ayangnath/VCR
```

## Category

**Accessibility**

## Language

English

## Privacy practices tab — permission justifications

**Single purpose description** (same field appears here too):
```
VCR finds SVG charts on the current web page and recolors them so they
remain visually distinguishable to users with color vision deficiency.
```

**activeTab**
```
Used to run the recoloring pipeline against the tab the user is
currently viewing, only when the user explicitly invokes VCR (clicks the
extension icon or toggles the popup control). VCR never acts on a tab
the user hasn't engaged with.
```

**scripting**
```
Used to inject the content script that locates SVG chart elements in the
page DOM and rewrites their fill/stroke colors with the corrected
palette computed by the Pyodide pipeline.
```

**storage**
```
Used to persist exactly one user preference locally — the last color
palette the user selected — via chrome.storage.local, so it can be
reapplied automatically on the next use. No other data is stored, and
nothing is synced off-device.
```

**offscreen**
```
Manifest V3 service workers cannot host a WebAssembly runtime directly.
VCR uses an offscreen document purely as an execution context to run the
bundled Pyodide/WebAssembly runtime that performs the color-correction
computation. No data is rendered or displayed in the offscreen document.
```

**Host permissions (http://*/*, https://*/*, file:///*)**
```
VCR's purpose is to find and recolor SVG charts wherever they appear,
and chart-bearing pages cannot be predicted in advance (they include
news sites, data dashboards, government statistics pages, academic
publications, and more) — so broad host access is required rather than
a fixed site list. file:// access lets the same recoloring work on local
HTML/SVG files (e.g., a researcher's own chart exports); Chrome requires
users to separately enable "Allow access to file URLs" for this
extension after install, so this access is opt-in even after the
extension is installed. VCR only reads/modifies SVG chart markup on the
page; it does not read form fields, credentials, or other page content.
```

**Are you using remote code?**
```
No. All JavaScript, the Pyodide/WebAssembly runtime, and all Python
packages (numpy, lxml, Pillow) used by VCR are bundled inside the
extension package. The extension's content_security_policy
(script-src 'self' 'wasm-unsafe-eval') does not permit loading
executable code from a remote origin.
```

**Data usage disclosures (the checkboxes)**
- Personally identifiable information: **No**
- Health information: **No**
- Financial and payment information: **No**
- Authentication information: **No**
- Personal communications: **No**
- Location: **No**
- Web history: **No**
- User activity: **No**
- Website content: technically yes for the SVG markup VCR reads/rewrites
  in-memory to do its job — but it is never transmitted anywhere, stored,
  or used for any purpose besides the immediate recoloring. If the form
  forces a yes/no here, answer **No data is collected** for "sold to
  third parties," "used for purposes unrelated to the item's core
  functionality," and "used to determine creditworthiness or for lending
  purposes" — all **No**.

Certification checkbox at the bottom of that tab ("I do not sell or
transfer user data...", "I do not use or transfer user data for purposes
unrelated to the item's single purpose...", "I do not use or transfer
user data to determine creditworthiness or for lending purposes"): all
**true** for VCR — check all three.
