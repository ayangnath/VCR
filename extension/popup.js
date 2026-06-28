const SERVER = "http://127.0.0.1:5000";

const state = {
  tabId: null,
  svgs: [],
  results: {},
  currentSvgId: null,
  cvd: "deutan",
  correctionOn: false,
};

const $ = (sel) => document.querySelector(sel);

function sendToContent(tabId, msg) {
  return new Promise((resolve) => {
    chrome.tabs.sendMessage(tabId, msg, (resp) => {
      if (chrome.runtime.lastError) {
        resolve({ ok: false, error: chrome.runtime.lastError.message });
      } else {
        resolve(resp);
      }
    });
  });
}

async function ensureContentScript(tabId) {
  const ping = await sendToContent(tabId, { type: "ping" });
  if (ping && ping.ok) return true;
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
    const ping2 = await sendToContent(tabId, { type: "ping" });
    return !!(ping2 && ping2.ok);
  } catch (e) {
    return false;
  }
}

async function callServer(svgSource, cvdType) {
  const resp = await fetch(`${SERVER}/detect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ svg: svgSource, cvd_type: cvdType }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
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
  const r = state.results[state.currentSvgId];
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

function swatchStripHtml(palette, isGradient) {
  if (!palette || !palette.length) return "";
  if (isGradient) {
    return `<div class="sw" style="flex:1;background:linear-gradient(to right, ${palette.join(",")});"></div>`;
  }
  return palette.map((c) => `<div class="sw" style="background:${c}"></div>`).join("");
}

function renderSvgPicker() {
  const sel = $("#svgPicker");
  sel.innerHTML = "";
  state.svgs.forEach((s, i) => {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = `${i + 1} / ${state.svgs.length} · ${s.width}×${s.height}px`;
    sel.appendChild(opt);
  });
  $("#svgCountBadge").textContent = `${state.svgs.length} SVG${state.svgs.length === 1 ? "" : "s"} found`;
  $("#svgSelector").style.display = state.svgs.length > 1 ? "flex" : "none";
}

function renderResult() {
  const r = state.results[state.currentSvgId];
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
  if (!state.currentSvgId) return;
  const r = state.results[state.currentSvgId];
  if (!r) return;
  const cand = activeCandidate(r);
  const correctedSvg = cand ? cand.corrected_svg : r.corrected_svg;
  if (state.correctionOn && correctedSvg) {
    await sendToContent(state.tabId, {
      type: "apply-corrected",
      svgId: state.currentSvgId,
      correctedSvg: correctedSvg,
      candidateKey: cand ? cand.key : null,
    });
  } else {
    await sendToContent(state.tabId, {
      type: "revert",
      svgId: state.currentSvgId,
    });
  }
}

async function refreshDetections() {
  state.results = {};
  for (let i = 0; i < state.svgs.length; i++) {
    const s = state.svgs[i];
    setStatus(`Analyzing ${i + 1} / ${state.svgs.length}…`);
    try {
      const result = await callServer(s.source, state.cvd);
      result._candidateIdx = await defaultCandidateIdx(result, s.appliedCandidateKey);
      state.results[s.id] = result;
    } catch (e) {
      state.results[s.id] = { status: "error", error: String(e.message || e) };
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

  const ok = await ensureContentScript(tab.id);
  if (!ok) {
    setStatus("Cannot access this page (try reloading the tab).", true);
    return;
  }

  const r = await sendToContent(tab.id, { type: "list-svgs" });
  if (!r || !r.ok) {
    setStatus(r?.error || "Failed to scan page.", true);
    return;
  }
  state.svgs = r.svgs;
  if (!state.svgs.length) {
    setStatus("No SVG charts detected on this page.", true);
    $("#svgCountBadge").textContent = "0 SVGs";
    return;
  }

  state.currentSvgId = state.svgs[0].id;
  // Mirror content.js's "is this SVG already corrected" state into the
  // popup so a close-and-reopen shows the toggle in the right position
  // and re-detects against the original (not the recolored DOM).
  const cur = state.svgs.find((s) => s.id === state.currentSvgId);
  state.correctionOn = !!(cur && cur.corrected);
  renderSvgPicker();

  // Quick health check before bombarding the server
  try {
    await fetch(`${SERVER}/health`, { method: "GET" });
  } catch (e) {
    setStatus("Server not reachable at 127.0.0.1:5000. Start it: python server/app.py", true);
    return;
  }

  await refreshDetections();
}

document.addEventListener("DOMContentLoaded", () => {
  $("#svgPicker").addEventListener("change", async (e) => {
    state.currentSvgId = e.target.value;
    const cur = state.svgs.find((s) => s.id === state.currentSvgId);
    state.correctionOn = !!(cur && cur.corrected);
    renderResult();
  });

  $("#cvdSelect").addEventListener("change", async (e) => {
    state.cvd = e.target.value;
    if (state.correctionOn) {
      for (const s of state.svgs) {
        await sendToContent(state.tabId, { type: "revert", svgId: s.id });
      }
    }
    await refreshDetections();
    if (state.correctionOn) await applyOrRevert();
  });

  $("#toggleTrack").addEventListener("click", async () => {
    if ($("#toggleTrack").classList.contains("disabled")) return;
    state.correctionOn = !state.correctionOn;
    const cur = state.svgs.find((s) => s.id === state.currentSvgId);
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
