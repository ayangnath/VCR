const state = {
  tabId: null,
  svgs: [], // each entry carries its own frameId for routing back to the right iframe
  results: {},
  currentKey: null,
  cvd: "deutan",
  correctionOn: false,
};

const $ = (sel) => document.querySelector(sel);

// SVG ids are only unique within a single frame; charts in different
// frames of the same tab can reuse "vcr-0" etc. independently, so the
// popup needs a composite key for anything keyed by svg identity.
function svgKey(s) {
  return `${s.frameId}:${s.id}`;
}

function sendToContent(tabId, msg, frameId) {
  return new Promise((resolve) => {
    const cb = (resp) => {
      if (chrome.runtime.lastError) {
        resolve({ ok: false, error: chrome.runtime.lastError.message });
      } else {
        resolve(resp);
      }
    };
    if (frameId === undefined) {
      chrome.tabs.sendMessage(tabId, msg, cb);
    } else {
      chrome.tabs.sendMessage(tabId, msg, { frameId }, cb);
    }
  });
}

function sendToBackground(msg) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ target: "background", ...msg }, (resp) => {
      if (chrome.runtime.lastError) {
        resolve({ ok: false, error: chrome.runtime.lastError.message });
      } else {
        resolve(resp);
      }
    });
  });
}

// Re-injecting content.js (idempotent — it guards itself against double
// registration) doubles as frame discovery: each InjectionResult reports
// the frameId it ran in, including iframes the declarative manifest entry
// already covered (all_frames: true) and ones that loaded before the
// extension did. Falls back to just the main frame if anything goes
// wrong, which matches the old single-frame behavior exactly.
async function discoverFrames(tabId) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      files: ["content.js"],
    });
    // Sort ascending so ordering is at least deterministic run-to-run
    // (frameId 0 is always the top frame). This doesn't guarantee true
    // top-to-bottom page order when charts span both the top frame and a
    // nested iframe -- there's no simple API for cross-frame DOM position.
    const ids = [...new Set(results.map((r) => r.frameId).filter((f) => f !== undefined))];
    ids.sort((a, b) => a - b);
    return ids.length ? ids : [0];
  } catch (e) {
    return [0];
  }
}

async function aliveFrames(tabId) {
  const frameIds = await discoverFrames(tabId);
  const alive = [];
  for (const frameId of frameIds) {
    const ping = await sendToContent(tabId, { type: "ping" }, frameId);
    if (ping && ping.ok) alive.push(frameId);
  }
  return alive;
}

async function callPyodide(svgSource, cvdType) {
  const resp = await sendToBackground({ type: "process-svg", svg: svgSource, cvdType });
  if (!resp || !resp.ok) throw new Error((resp && resp.error) || "Pyodide call failed");
  return resp.result;
}

function setStatus(text, isError = false) {
  const el = $("#status");
  el.textContent = text || "";
  el.classList.toggle("error", !!isError);
}

// Three swatch pairs, each tuned (against color_science.py's own CVD
// simulation) to collapse sharply under exactly one CVD type while staying
// clearly separable under the other two and under normal vision. "Looks
// the same" on a given pair is therefore a real diagnostic signal for that
// type, not a guess - see the plan/PR notes for the verified delta-E table.
const SPOTCHECK_PAIRS = [
  { type: "deutan", colorA: "rgb(220,40,100)", colorB: "rgb(160,120,100)" },
  { type: "protan", colorA: "rgb(220,60,100)", colorB: "rgb(110,100,100)" },
  { type: "tritan", colorA: "rgb(140,180,180)", colorB: "rgb(150,200,80)" },
];

const CVD_PREVALENCE_ORDER = ["deutan", "protan", "tritan"];

const spotCheck = { step: 0, sameTypes: [] };

function startSpotCheck() {
  spotCheck.step = 0;
  spotCheck.sameTypes = [];
  $("#spotCheckLink").style.display = "none";
  $("#spotCheckResult").style.display = "none";
  $("#spotCheckFlow").style.display = "block";
  renderSpotCheckStep();
}

function renderSpotCheckStep() {
  const pair = SPOTCHECK_PAIRS[spotCheck.step];
  $("#spotCheckMeta").textContent = `${spotCheck.step + 1} / ${SPOTCHECK_PAIRS.length}`;
  $("#spotCheckSwatchA").style.background = pair.colorA;
  $("#spotCheckSwatchB").style.background = pair.colorB;
}

function answerSpotCheck(isSame) {
  if (isSame) spotCheck.sameTypes.push(SPOTCHECK_PAIRS[spotCheck.step].type);
  spotCheck.step += 1;
  if (spotCheck.step < SPOTCHECK_PAIRS.length) {
    renderSpotCheckStep();
  } else {
    finishSpotCheck();
  }
}

function finishSpotCheck() {
  $("#spotCheckFlow").style.display = "none";
  $("#spotCheckLink").style.display = "block";

  let inferred = null;
  for (const type of CVD_PREVALENCE_ORDER) {
    if (spotCheck.sameTypes.includes(type)) {
      inferred = type;
      break;
    }
  }

  const resultEl = $("#spotCheckResult");
  resultEl.style.display = "block";
  if (inferred) {
    const label = inferred.charAt(0).toUpperCase() + inferred.slice(1) + "opia";
    resultEl.textContent = `Looks like ${label} — CVD type set above.`;
    $("#cvdSelect").value = inferred;
    $("#cvdSelect").dispatchEvent(new Event("change"));
  } else {
    resultEl.textContent =
      "No strong color-vision signal detected from this quick check — leaving the type as-is.";
  }
}

function cancelSpotCheck() {
  $("#spotCheckFlow").style.display = "none";
  $("#spotCheckLink").style.display = "block";
}

const REMEMBERED_PALETTE_KEY = "vcrCategoricalPalette";

// Currently-selected candidate for a result, or null when the result has
// no candidates (e.g. status "passed"/"skipped"/"error"). Selection is
// tracked per-result (on r._candidateIdx) rather than in global state so
// each SVG in a multi-chart page keeps its own choice when switching via
// the SVG picker.
function activeCandidate(r) {
  if (!r || !r.candidates || !r.candidates.length) return null;
  return r.candidates[r._candidateIdx || 0] || r.candidates[0];
}

// Picks the default candidate index for a freshly-fetched result. Priority:
// 1. appliedKey - what content.js says is *already showing on the page*
//    for this SVG (set in the same round-trip as the apply, so it's
//    immediately authoritative - this is what makes "close popup, reopen"
//    restore the exact palette that was selected, not just that *some*
//    correction is on).
// 2. the remembered categorical palette, for a chart that hasn't been
//    corrected yet this page-session.
// 3. rank 1 (index 0, already ranked best-first by the pipeline).
async function defaultCandidateIdx(r, appliedKey) {
  if (!r || !r.candidates || !r.candidates.length) return 0;

  if (appliedKey) {
    const idx = r.candidates.findIndex((c) => c.key === appliedKey);
    if (idx >= 0) return idx;
  }

  if (r.palette_type === "categorical") {
    try {
      const stored = await chrome.storage.local.get(REMEMBERED_PALETTE_KEY);
      const key = stored && stored[REMEMBERED_PALETTE_KEY];
      if (key) {
        const idx = r.candidates.findIndex((c) => c.key === key);
        if (idx >= 0) return idx;
      }
    } catch (e) {
      // storage unavailable; fall through to rank 1
    }
  }
  return 0;
}

async function navigatePalette(delta) {
  const r = state.results[state.currentKey];
  if (!r || !r.candidates || r.candidates.length < 2) return;
  const n = r.candidates.length;
  r._candidateIdx = ((r._candidateIdx || 0) + delta + n) % n;
  renderResult();

  const cand = r.candidates[r._candidateIdx];
  const tasks = [];
  if (state.correctionOn) tasks.push(applyOrRevert());
  if (r.palette_type === "categorical") {
    tasks.push(
      chrome.storage.local.set({ [REMEMBERED_PALETTE_KEY]: cand.key }).catch(() => {
        // storage unavailable; selection just won't persist across sessions
      })
    );
  }
  await Promise.all(tasks);
}

function renderPaletteNav(r) {
  const nav = $("#paletteNav");
  if (!r || !r.candidates || r.candidates.length < 2) {
    nav.style.display = "none";
    return;
  }
  nav.style.display = "flex";
  const idx = r._candidateIdx || 0;
  const cand = r.candidates[idx];
  $("#paletteName").textContent = cand.name;
  const metaParts = [`${idx + 1} / ${r.candidates.length}`];
  if (cand.metric_label && cand.metric_value != null) {
    metaParts.push(`${cand.metric_label} ${Number(cand.metric_value).toFixed(1)}`);
  }
  $("#paletteMeta").textContent = metaParts.join(" · ");
}

// Rough perceptual luma, good enough to order swatches for display --
// original_palette/new_palette come back in DOM-encounter order (whatever
// order the pipeline first saw each color), not sorted by lightness, so
// feeding them straight into a CSS gradient produces a choppy-looking
// strip even when the underlying ramp is clean. This sort is display-only;
// it never touches the palette data used to actually recolor the chart.
function approxLightness(hex) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || "");
  if (!m) return 0;
  const [r, g, b] = [m[1], m[2], m[3]].map((h) => parseInt(h, 16));
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

function swatchStripHtml(palette, isGradient) {
  if (!palette || !palette.length) return "";
  if (isGradient) {
    const sorted = [...palette].sort((a, b) => approxLightness(a) - approxLightness(b));
    return `<div class="sw" style="flex:1;background:linear-gradient(to right, ${sorted.join(",")});"></div>`;
  }
  return palette.map((c) => `<div class="sw" style="background:${c}"></div>`).join("");
}

function renderSvgPicker() {
  const sel = $("#svgPicker");
  sel.innerHTML = "";
  state.svgs.forEach((s, i) => {
    const opt = document.createElement("option");
    opt.value = svgKey(s);
    const frameTag = s.frameId ? " · frame" : "";
    opt.textContent = `${i + 1} / ${state.svgs.length} · ${s.width}×${s.height}px${frameTag}`;
    sel.appendChild(opt);
  });
  $("#svgCountBadge").textContent = `${state.svgs.length} SVG${state.svgs.length === 1 ? "" : "s"} found`;
  $("#svgSelector").style.display = state.svgs.length > 1 ? "flex" : "none";
}

function renderResult() {
  const r = state.results[state.currentKey];
  const toggleEl = $("#toggleTrack");

  if (!r) {
    $("#typeChip").textContent = "—";
    $("#origStrip").innerHTML = "";
    $("#correctedStrip").innerHTML = "";
    toggleEl.classList.add("disabled");
    $("#paletteNav").style.display = "none";
    return;
  }

  if (r.status === "error") {
    setStatus(r.error || "Pipeline error", true);
    toggleEl.classList.add("disabled");
    $("#paletteNav").style.display = "none";
    return;
  }

  const t = r.palette_type
    ? r.palette_type.charAt(0).toUpperCase() + r.palette_type.slice(1)
    : "—";
  $("#typeChip").textContent = `${t} · ${r.n_colors}`;

  const cand = activeCandidate(r);
  const correctedPalette = cand ? cand.new_palette : r.new_palette;
  const correctedSvg = cand ? cand.corrected_svg : r.corrected_svg;

  const isGradient = r.palette_type && r.palette_type !== "categorical";
  $("#origStrip").innerHTML = swatchStripHtml(r.original_palette, isGradient);
  $("#correctedStrip").innerHTML = swatchStripHtml(correctedPalette, isGradient);

  if (r.mismatch) {
    $("#mismatchBanner").style.display = "block";
    $("#mismatchText").textContent =
      r.mismatch_reason || "The classified palette type does not match the data signal.";
  } else {
    $("#mismatchBanner").style.display = "none";
  }

  const hasCorrected = !!correctedSvg;

  if (r.status === "passed") {
    setStatus("Already accessible — no recoloring needed.");
    toggleEl.classList.add("disabled");
  } else if (r.status === "skipped") {
    setStatus("Skipped: fewer than 2 data colors detected.");
    toggleEl.classList.add("disabled");
  } else if (!hasCorrected) {
    setStatus(`Status: ${r.status} (no corrected SVG available)`);
    toggleEl.classList.add("disabled");
  } else {
    setStatus("");
    toggleEl.classList.remove("disabled");
  }

  renderPaletteNav(r);

  $("#correctedLabel").textContent = state.correctionOn ? "Corrected" : "Corrected (off)";
  $("#correctedStrip").style.opacity = state.correctionOn ? "1" : "0.3";
  toggleEl.classList.toggle("off", !state.correctionOn);
}

async function applyOrRevert() {
  const cur = state.svgs.find((s) => svgKey(s) === state.currentKey);
  if (!cur) return;
  const r = state.results[state.currentKey];
  if (!r) return;
  const cand = activeCandidate(r);
  const correctedSvg = cand ? cand.corrected_svg : r.corrected_svg;
  if (state.correctionOn && correctedSvg) {
    await sendToContent(
      state.tabId,
      {
        type: "apply-corrected",
        svgId: cur.id,
        correctedSvg: correctedSvg,
        candidateKey: cand ? cand.key : null,
      },
      cur.frameId
    );
  } else {
    await sendToContent(state.tabId, { type: "revert", svgId: cur.id }, cur.frameId);
  }
}

async function refreshDetections() {
  state.results = {};
  for (let i = 0; i < state.svgs.length; i++) {
    const s = state.svgs[i];
    const key = svgKey(s);
    setStatus(`Analyzing ${i + 1} / ${state.svgs.length}…`);
    try {
      const result = await callPyodide(s.source, state.cvd);
      result._candidateIdx = await defaultCandidateIdx(result, s.appliedCandidateKey);
      state.results[key] = result;
    } catch (e) {
      state.results[key] = { status: "error", error: String(e.message || e) };
    }
  }
  setStatus("");
  renderResult();
}

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) {
    setStatus("No active tab.", true);
    return;
  }
  state.tabId = tab.id;

  const frameIds = await aliveFrames(tab.id);
  if (!frameIds.length) {
    setStatus("Cannot access this page (try reloading the tab).", true);
    return;
  }

  const svgs = [];
  for (const frameId of frameIds) {
    const r = await sendToContent(tab.id, { type: "list-svgs" }, frameId);
    if (r && r.ok && Array.isArray(r.svgs)) {
      for (const s of r.svgs) svgs.push({ ...s, frameId });
    }
  }
  state.svgs = svgs;
  if (!state.svgs.length) {
    setStatus("No SVG charts detected on this page.", true);
    $("#svgCountBadge").textContent = "0 SVGs";
    return;
  }

  state.currentKey = svgKey(state.svgs[0]);
  // Mirror content.js's "is this SVG already corrected" state into the
  // popup so a close-and-reopen shows the toggle in the right position
  // and re-detects against the original (not the recolored DOM).
  state.correctionOn = !!state.svgs[0].corrected;
  renderSvgPicker();

  // First open after install (or after the offscreen document gets
  // evicted) pays the ~3-5s Pyodide boot cost; subsequent opens resolve
  // immediately since background.js warms it on install.
  setStatus("Booting in-browser Python runtime…");
  const ready = await sendToBackground({ type: "ensure-ready" });
  if (!ready || !ready.ok) {
    setStatus(ready?.error || "Failed to start the in-browser runtime.", true);
    return;
  }

  await refreshDetections();
}

document.addEventListener("DOMContentLoaded", () => {
  $("#svgPicker").addEventListener("change", async (e) => {
    state.currentKey = e.target.value;
    const cur = state.svgs.find((s) => svgKey(s) === state.currentKey);
    state.correctionOn = !!(cur && cur.corrected);
    renderResult();
  });

  $("#cvdSelect").addEventListener("change", async (e) => {
    state.cvd = e.target.value;
    if (state.correctionOn) {
      for (const s of state.svgs) {
        await sendToContent(state.tabId, { type: "revert", svgId: s.id }, s.frameId);
      }
    }
    await refreshDetections();
    if (state.correctionOn) await applyOrRevert();
  });

  $("#toggleTrack").addEventListener("click", async () => {
    if ($("#toggleTrack").classList.contains("disabled")) return;
    state.correctionOn = !state.correctionOn;
    const cur = state.svgs.find((s) => svgKey(s) === state.currentKey);
    if (cur) cur.corrected = state.correctionOn;
    renderResult();
    await applyOrRevert();
  });

  $("#paletteNavPrev").addEventListener("click", () => navigatePalette(-1));
  $("#paletteNavNext").addEventListener("click", () => navigatePalette(1));

  $("#spotCheckLink").addEventListener("click", (e) => {
    e.preventDefault();
    startSpotCheck();
  });
  $("#spotCheckSame").addEventListener("click", () => answerSpotCheck(true));
  $("#spotCheckDifferent").addEventListener("click", () => answerSpotCheck(false));
  $("#spotCheckCancel").addEventListener("click", cancelSpotCheck);

  init();
});
