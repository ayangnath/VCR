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
    return;
  }

  if (r.status === "error") {
    setStatus(r.error || "Pipeline error", true);
    toggleEl.classList.add("disabled");
    return;
  }

  const t = r.palette_type
    ? r.palette_type.charAt(0).toUpperCase() + r.palette_type.slice(1)
    : "—";
  $("#typeChip").textContent = `${t} · ${r.n_colors}`;

  const isGradient = r.palette_type && r.palette_type !== "categorical";
  $("#origStrip").innerHTML = swatchStripHtml(r.original_palette, isGradient);
  $("#correctedStrip").innerHTML = swatchStripHtml(r.new_palette, isGradient);

  if (r.mismatch) {
    $("#mismatchBanner").style.display = "block";
    $("#mismatchText").textContent =
      r.mismatch_reason || "The classified palette type does not match the data signal.";
  } else {
    $("#mismatchBanner").style.display = "none";
  }

  const hasCorrected = !!r.corrected_svg;

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

  $("#correctedLabel").textContent = state.correctionOn ? "Corrected" : "Corrected (off)";
  $("#correctedStrip").style.opacity = state.correctionOn ? "1" : "0.3";
  toggleEl.classList.toggle("off", !state.correctionOn);
}

async function applyOrRevert() {
  if (!state.currentSvgId) return;
  const r = state.results[state.currentSvgId];
  if (!r) return;
  if (state.correctionOn && r.corrected_svg) {
    await sendToContent(state.tabId, {
      type: "apply-corrected",
      svgId: state.currentSvgId,
      correctedSvg: r.corrected_svg,
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
      state.results[s.id] = await callServer(s.source, state.cvd);
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

  init();
});
