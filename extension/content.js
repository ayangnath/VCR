// VCR content script — discovers SVGs on the page, ships their source
// to the popup, and replaces / restores them on demand.
//
// The apply step swaps the SVG element with the corrected SVG returned
// by the server (which is byte-identical to the CLI output: legend sync,
// raster legend recoloring, and DR7 logic all already applied). The
// content script never re-implements the apply pass in JS, so what the
// user sees on the page matches `corrected/<file>.svg` exactly.

(() => {
  if (window.__VCR_CONTENT_LOADED__) return;
  window.__VCR_CONTENT_LOADED__ = true;

  const VCR_ID_ATTR = "data-vcr-id";

  // svgId -> { outerHTML, parent, nextSibling } so revert restores byte-exact
  const originalsBySvg = new Map();
  // svgId -> candidate "key" currently applied (e.g. "wong"), so the popup
  // can restore the right palette-navigator selection on reopen instead of
  // defaulting to rank 1 - this DOM-side state is the source of truth since
  // it's set in the same round-trip as the visible correction, unlike the
  // separate async chrome.storage write for the remembered categorical pick.
  const appliedCandidateKeyById = new Map();

  function isPageStandaloneSvg() {
    return (
      document.documentElement &&
      document.documentElement.tagName &&
      document.documentElement.tagName.toLowerCase() === "svg"
    );
  }

  async function fetchPageBytes() {
    try {
      const resp = await fetch(window.location.href);
      if (!resp.ok) return null;
      const ct = (resp.headers.get("content-type") || "").toLowerCase();
      // Only trust the bytes if the response is actually SVG. Some servers
      // route .svg to an HTML viewer / 404 page; in that case fall back.
      if (!ct.includes("svg") && !ct.includes("xml") && ct !== "") return null;
      return await resp.text();
    } catch (e) {
      return null;
    }
  }

  // Serialize an SVG element so the server's lxml parser sees something
  // well-formed. When SVG is inlined inside HTML, the DOM tracks the SVG
  // namespace internally but the serialized markup can omit `xmlns=...`,
  // which then trips lxml. Cloning and setting the xmlns attrs explicitly
  // makes the output safe for any XML parser.
  function serializeInlineSvg(svg) {
    const clone = svg.cloneNode(true);
    clone.removeAttribute(VCR_ID_ATTR);
    if (!clone.getAttribute("xmlns")) {
      clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    }
    if (!clone.getAttribute("xmlns:xlink")) {
      clone.setAttribute("xmlns:xlink", "http://www.w3.org/1999/xlink");
    }
    return new XMLSerializer().serializeToString(clone);
  }

  async function listSvgs() {
    const all = document.querySelectorAll("svg");
    const out = [];
    let counter = 0;

    // When the page itself is a single SVG document, fetching the URL
    // gives us the original bytes — no Chrome parse-then-serialize round
    // trip, which would otherwise drift hex case, namespace defaults,
    // attribute ordering, etc. and shift what the pipeline sees.
    let originalBytes = null;
    if (isPageStandaloneSvg() && all.length === 1) {
      originalBytes = await fetchPageBytes();
    }

    all.forEach((svg) => {
      const r = svg.getBoundingClientRect();
      if (r.width < 60 || r.height < 60) return;
      let id = svg.getAttribute(VCR_ID_ATTR);
      if (!id) {
        id = `vcr-${counter++}`;
        svg.setAttribute(VCR_ID_ATTR, id);
      }
      // If this SVG is currently in the corrected state (popup was
      // closed and reopened, but the page still shows our replacement),
      // hand the server the *stashed original* — otherwise it would
      // re-detect against the already-CVD-safe palette and report
      // "passed", leaving the user with no way to toggle back.
      const stashed = originalsBySvg.get(id);
      let source;
      if (stashed) {
        source = stashed.outerHTML;
      } else if (originalBytes) {
        source = originalBytes;
      } else {
        try {
          source = serializeInlineSvg(svg);
        } catch (e) {
          return;
        }
      }
      out.push({
        id,
        source,
        width: Math.round(r.width),
        height: Math.round(r.height),
        corrected: !!stashed,
        appliedCandidateKey: appliedCandidateKeyById.get(id) || null,
      });
    });
    return out;
  }

  function applyCorrectedSvg(svgId, correctedSvgString, candidateKey) {
    const sel = `svg[${VCR_ID_ATTR}="${CSS.escape(svgId)}"]`;
    const svg = document.querySelector(sel);
    if (!svg) return { ok: false, error: "svg not found" };
    if (!correctedSvgString || typeof correctedSvgString !== "string") {
      return { ok: false, error: "no corrected svg provided" };
    }

    if (!originalsBySvg.has(svgId)) {
      originalsBySvg.set(svgId, {
        outerHTML: svg.outerHTML,
        parent: svg.parentNode,
        nextSibling: svg.nextSibling,
      });
    }

    // Parse the corrected SVG. Stripping the XML declaration first lets
    // the HTML parser path do the right thing inside an HTML document;
    // the SVG namespace is recognized natively for inline SVG.
    const cleaned = correctedSvgString.replace(/^\s*<\?xml[^?]*\?>\s*/i, "");

    let newSvg = null;
    try {
      const tmp = document.createElement("div");
      tmp.innerHTML = cleaned;
      newSvg = tmp.querySelector("svg");
    } catch (e) {}

    // Fallback: parse as XML and adopt.
    if (!newSvg) {
      try {
        const doc = new DOMParser().parseFromString(cleaned, "image/svg+xml");
        if (doc && doc.documentElement && !doc.querySelector("parsererror")) {
          newSvg = document.adoptNode(doc.documentElement);
        }
      } catch (e) {}
    }
    if (!newSvg) return { ok: false, error: "failed to parse corrected svg" };

    newSvg.setAttribute(VCR_ID_ATTR, svgId);
    svg.replaceWith(newSvg);
    if (candidateKey) {
      appliedCandidateKeyById.set(svgId, candidateKey);
    } else {
      appliedCandidateKeyById.delete(svgId);
    }
    return { ok: true };
  }

  function revert(svgId) {
    const orig = originalsBySvg.get(svgId);
    if (!orig) return { ok: true };
    const cur = document.querySelector(
      `svg[${VCR_ID_ATTR}="${CSS.escape(svgId)}"]`
    );
    if (cur) {
      const tmp = document.createElement("div");
      tmp.innerHTML = orig.outerHTML;
      const restored = tmp.querySelector("svg");
      if (restored) {
        cur.replaceWith(restored);
      }
    }
    originalsBySvg.delete(svgId);
    appliedCandidateKeyById.delete(svgId);
    return { ok: true };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    (async () => {
      try {
        if (!msg || typeof msg !== "object") {
          sendResponse({ ok: false, error: "bad message" });
          return;
        }
        switch (msg.type) {
          case "ping":
            sendResponse({ ok: true });
            break;
          case "list-svgs":
            sendResponse({ ok: true, svgs: await listSvgs() });
            break;
          case "apply-corrected":
            sendResponse(applyCorrectedSvg(msg.svgId, msg.correctedSvg, msg.candidateKey));
            break;
          case "revert":
            sendResponse(revert(msg.svgId));
            break;
          default:
            sendResponse({ ok: false, error: "unknown message type" });
        }
      } catch (e) {
        sendResponse({ ok: false, error: String((e && e.message) || e) });
      }
    })();
    return true; // keep channel open for async sendResponse
  });
})();
