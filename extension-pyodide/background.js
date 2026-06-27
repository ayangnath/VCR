// MV3 service worker. Owns the offscreen document's lifecycle (a service
// worker's CSP blocks Pyodide's WASM init, so the actual runtime lives in
// offscreen.html/offscreen.js instead) and routes messages between popup.js
// and offscreen.js. Messages carry a `target` field so each listener ignores
// what isn't addressed to it (Chrome's documented offscreen-document
// pattern) — this worker only handles target === "background".

const OFFSCREEN_URL = "offscreen.html";

let creatingOffscreenDocument = null;

async function hasOffscreenDocument() {
  const contexts = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
    documentUrls: [chrome.runtime.getURL(OFFSCREEN_URL)],
  });
  return contexts.length > 0;
}

async function ensureOffscreenDocument() {
  if (await hasOffscreenDocument()) return;

  if (creatingOffscreenDocument) {
    await creatingOffscreenDocument;
    return;
  }

  creatingOffscreenDocument = chrome.offscreen.createDocument({
    url: OFFSCREEN_URL,
    reasons: ["WORKERS"],
    justification: "Run the Pyodide/WASM Python runtime to recolor SVGs.",
  });

  try {
    await creatingOffscreenDocument;
  } finally {
    creatingOffscreenDocument = null;
  }
}

async function sendToOffscreen(msg) {
  await ensureOffscreenDocument();
  try {
    return await chrome.runtime.sendMessage({ ...msg, target: "offscreen" });
  } catch (e) {
    // Chrome can evict the offscreen document; recreate once and retry.
    await ensureOffscreenDocument();
    return await chrome.runtime.sendMessage({ ...msg, target: "offscreen" });
  }
}

chrome.runtime.onInstalled.addListener(() => {
  // Warm Pyodide immediately so the first popup click doesn't pay the
  // ~3-5s WASM boot cost.
  ensureOffscreenDocument().catch(() => {});
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.target !== "background") return false;

  (async () => {
    try {
      const result = await sendToOffscreen(msg);
      sendResponse(result);
    } catch (e) {
      sendResponse({ ok: false, error: String((e && e.message) || e) });
    }
  })();

  return true; // keep channel open for async sendResponse
});
