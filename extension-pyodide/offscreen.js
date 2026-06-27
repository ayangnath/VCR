// Hosts the actual Pyodide/WASM runtime. Lives in an offscreen document
// because a service worker's CSP blocks WASM compilation; this page gets a
// normal page CSP instead. Boots as soon as the document is created
// (background.js creates it on install) so the first popup click doesn't
// pay the cold-start cost.

const PIPELINE_FILES = [
  "color_science.py",
  "svg_parser.py",
  "classifier.py",
  "data_signal_extractor.py",
  "reconciler.py",
  "invariant_tests.py",
  "recolorer.py",
  "main.py",
  "pyodide_bridge.py",
];

const PIPELINE_DIR = "/lib/vcr_pipeline";

let bridge = null;

async function boot() {
  const pyodide = await loadPyodide({
    indexURL: chrome.runtime.getURL("runtime/"),
  });
  await pyodide.loadPackage(["numpy", "lxml", "pillow"]);

  pyodide.FS.mkdirTree(PIPELINE_DIR);
  for (const name of PIPELINE_FILES) {
    const text = await (await fetch(chrome.runtime.getURL(`python/${name}`))).text();
    pyodide.FS.writeFile(`${PIPELINE_DIR}/${name}`, text);
  }
  pyodide.runPython(`
import sys
if "${PIPELINE_DIR}" not in sys.path:
    sys.path.insert(0, "${PIPELINE_DIR}")
`);

  bridge = pyodide.pyimport("pyodide_bridge");
  return pyodide;
}

const pyodideReady = boot();

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.target !== "offscreen") return false;

  (async () => {
    try {
      await pyodideReady;
      if (msg.type === "ensure-ready") {
        sendResponse({ ok: true });
        return;
      }
      if (msg.type === "process-svg") {
        const json = bridge.process_svg(msg.svg, msg.cvdType);
        sendResponse({ ok: true, result: JSON.parse(json) });
        return;
      }
      sendResponse({ ok: false, error: "unknown message type" });
    } catch (e) {
      sendResponse({ ok: false, error: String((e && e.message) || e) });
    }
  })();

  return true;
});
