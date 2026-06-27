// VCR content script — discovers SVGs on the page, ships their source
// to the popup, and replaces / restores them on demand.
//
// The apply step swaps the SVG element with the corrected SVG returned
// by the pipeline (byte-identical to the CLI output: legend sync, raster
// legend recoloring, and DR7 logic all already applied). The content
// script never re-implements the apply pass in JS, so what the user sees
// on the page matches `corrected/<file>.svg` exactly.
//
// Runs once per frame (manifest declares all_frames: true), so iframes
// get their own independent copy of all the state below — no special
// handling needed here for cross-frame charts, only in popup.js's
// orchestration across frames.
//
// Two kinds of chart elements are handled:
//   - inline <svg> — swapped wholesale via element replacement.
//   - <img src="*.svg"> — a chart embedded as a raster-style reference
//     rather than inline markup. Swapped via a Blob URL on the <img> so
//     the element itself (and its layout/attributes) stays intact.
// Canvas-rendered charts are out of scope: there's no DOM/markup to
// recolor, and remapping pixels directly would be exactly the kind of
// signal-blind global filter this tool is designed not to be.

(() => {
  if (window.__VCR_CONTENT_LOADED__) return;
  window.__VCR_CONTENT_LOADED__ = true;

  const VCR_ID_ATTR = "data-vcr-id";

  // svgId -> { outerHTML } so revert restores byte-exact (inline <svg>)
  const originalsBySvg = new Map();
  // imgId -> original `src` attribute value, so revert restores it
  const originalImgSrcById = new Map();
  // imgId -> original fetched SVG text, cached so re-detection while
  // corrected serves the original bytes rather than nothing (the <img>'s
  // current src is a blob: URL at that point, not re-fetchable as source)
  const imgOriginalSourceById = new Map();

  function isPageStandaloneSvg() {
    return (
      document.documentElement &&
      document.documentElement.tagName &&
      document.documentElement.tagName.toLowerCase() === "svg"
    );
  }

  async function fetchSvgBytes(url) {
    try {
      const resp = await fetch(url);
      if (!resp.ok) return null;
      const ct = (resp.headers.get("content-type") || "").toLowerCase();
      // Only trust the bytes if the response is actually SVG. Some servers
      // route .svg to an HTML viewer / 404 page; in that case fall back.
      // (Cross-origin <img> sources without permissive CORS headers will
      // also land here as a fetch failure — known limitation, the <img>
      // displays fine but we can't read its bytes to recolor it.)
      if (!ct.includes("svg") && !ct.includes("xml") && ct !== "") return null;
      return await resp.text();
    } catch (e) {
      return null;
    }
  }

  // Serialize an SVG element so the pipeline's lxml parser sees something
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

  function isSvgImgSrc(src) {
    if (!src) return false;
    if (src.startsWith("data:image/svg+xml")) return true;
    try {
      const u = new URL(src, window.location.href);
      return /\.svg(?:$|[?#])/i.test(u.pathname);
    } catch (e) {
      return false;
    }
  }

  async function listInlineSvgs(counterRef) {
    const all = document.querySelectorAll("svg");
    const out = [];

    // When the page itself is a single SVG document, fetching the URL
    // gives us the original bytes — no Chrome parse-then-serialize round
    // trip, which would otherwise drift hex case, namespace defaults,
    // attribute ordering, etc. and shift what the pipeline sees.
    let originalBytes = null;
    if (isPageStandaloneSvg() && all.length === 1) {
      originalBytes = await fetchSvgBytes(window.location.href);
    }

    all.forEach((svg) => {
      const r = svg.getBoundingClientRect();
      if (r.width < 60 || r.height < 60) return;
      let id = svg.getAttribute(VCR_ID_ATTR);
      if (!id) {
        id = `vcr-${counterRef.n++}`;
        svg.setAttribute(VCR_ID_ATTR, id);
      }
      // If this SVG is currently in the corrected state (popup was
      // closed and reopened, but the page still shows our replacement),
      // hand the pipeline the *stashed original* — otherwise it would
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
      });
    });
    return out;
  }

  async function listImgSvgs(counterRef) {
    const out = [];
    const imgs = document.querySelectorAll("img");
    for (const img of imgs) {
      if (!isSvgImgSrc(img.currentSrc || img.getAttribute("src"))) continue;
      const r = img.getBoundingClientRect();
      if (r.width < 60 || r.height < 60) continue;

      let id = img.getAttribute(VCR_ID_ATTR);
      if (!id) {
        id = `vcr-${counterRef.n++}`;
        img.setAttribute(VCR_ID_ATTR, id);
      }

      let source = imgOriginalSourceById.get(id);
      if (!source) {
        source = await fetchSvgBytes(img.currentSrc || img.getAttribute("src"));
        if (!source) continue; // CORS-blocked, or not actually SVG bytes
        imgOriginalSourceById.set(id, source);
      }

      out.push({
        id,
        source,
        width: Math.round(r.width),
        height: Math.round(r.height),
        corrected: originalImgSrcById.has(id),
      });
    }
    return out;
  }

  async function listSvgs() {
    const counterRef = { n: 0 };
    const [inline, imgs] = await Promise.all([
      listInlineSvgs(counterRef),
      listImgSvgs(counterRef),
    ]);
    return inline.concat(imgs);
  }

  function applyCorrectedSvgEl(svg, svgId, correctedSvgString) {
    if (!originalsBySvg.has(svgId)) {
      originalsBySvg.set(svgId, { outerHTML: svg.outerHTML });
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
    return { ok: true };
  }

  function applyCorrectedImgEl(img, imgId, correctedSvgString) {
    if (!originalImgSrcById.has(imgId)) {
      originalImgSrcById.set(imgId, img.getAttribute("src"));
    }
    try {
      const blob = new Blob([correctedSvgString], { type: "image/svg+xml" });
      const url = URL.createObjectURL(blob);
      const prevBlobUrl = img.dataset.vcrBlobUrl;
      img.src = url;
      img.dataset.vcrBlobUrl = url;
      if (prevBlobUrl) URL.revokeObjectURL(prevBlobUrl);
      return { ok: true };
    } catch (e) {
      return { ok: false, error: String((e && e.message) || e) };
    }
  }

  function applyCorrectedSvg(id, correctedSvgString) {
    if (!correctedSvgString || typeof correctedSvgString !== "string") {
      return { ok: false, error: "no corrected svg provided" };
    }
    const svgSel = `svg[${VCR_ID_ATTR}="${CSS.escape(id)}"]`;
    const svgEl = document.querySelector(svgSel);
    if (svgEl) return applyCorrectedSvgEl(svgEl, id, correctedSvgString);

    const imgSel = `img[${VCR_ID_ATTR}="${CSS.escape(id)}"]`;
    const imgEl = document.querySelector(imgSel);
    if (imgEl) return applyCorrectedImgEl(imgEl, id, correctedSvgString);

    return { ok: false, error: "element not found" };
  }

  function revert(id) {
    const svgSel = `svg[${VCR_ID_ATTR}="${CSS.escape(id)}"]`;
    const svgEl = document.querySelector(svgSel);
    const orig = originalsBySvg.get(id);
    if (svgEl && orig) {
      const tmp = document.createElement("div");
      tmp.innerHTML = orig.outerHTML;
      const restored = tmp.querySelector("svg");
      if (restored) svgEl.replaceWith(restored);
      originalsBySvg.delete(id);
      return { ok: true };
    }

    const imgSel = `img[${VCR_ID_ATTR}="${CSS.escape(id)}"]`;
    const imgEl = document.querySelector(imgSel);
    const originalSrc = originalImgSrcById.get(id);
    if (imgEl && originalSrc !== undefined) {
      const prevBlobUrl = imgEl.dataset.vcrBlobUrl;
      imgEl.src = originalSrc;
      delete imgEl.dataset.vcrBlobUrl;
      if (prevBlobUrl) URL.revokeObjectURL(prevBlobUrl);
    }
    originalImgSrcById.delete(id);
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
            sendResponse(applyCorrectedSvg(msg.svgId, msg.correctedSvg));
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
