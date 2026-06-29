# VCR (Visualization Color Repair)

A signal-aware recoloring tool for making SVG data visualizations accessible to viewers with Color Vision Deficiency (CVD). Unlike global color filters that treat all colors uniformly, this tool understands what role colors play in encoding data and applies corrections that preserve the intended data signal.

The implementation follows the 6-phase decision tree architecture from the thesis (see `Thesis_Decision_Tree.svg`) and satisfies design requirements DR1-DR7 from Chapter 3.

## Why this exists

The key idea behind this thesis is that visualization accessibility needs to go beyond just making colors look different. Existing tools like Dalton and Colorize apply uniform color filters to web pages. They improve perceptual distinguishability, but they can destroy semantic information. For example, a diverging color scale's white midpoint (representing zero or neutrality) might shift to pink, causing viewers to misinterpret neutral values as slightly positive.

This tool asks "can this user still read the data?" rather than simply "can this user distinguish these colors?"

## What the tool preserves

The tool preserves different invariants depending on what type of data signal the visualization encodes.

For categorical palettes, every pair of category colors must remain perceptually distinguishable (CIEDE2000 ΔE >= 8). The failure mode here is pairwise collapse, where categories become indistinguishable under CVD.

For sequential palettes, L* values must be strictly ordered, equal data intervals should produce equal perceived color differences, and the gradient direction (light-to-dark or dark-to-light) must be maintained. Adjacent colors need to differ by at least ΔL* >= 3. Failure modes include monotonicity violation, uniformity distortion, and direction reversal.

For diverging palettes, the neutral midpoint must remain identifiable as the perceptual extremum, the two arms must remain distinct (endpoint ΔE > 10), and each arm must independently pass sequential tests. Failure modes include midpoint disappearance, midpoint shift, and bidirectional collapse.

## The 6-phase pipeline

Phase 1 parses the SVG and separates data marks (bars, areas, bubbles, map regions) from non-data elements (axes, labels, gridlines, titles). It also detects grid lines and annotation elements by grouping SVG elements by tag and checking for uniform stroke/fill/dimensions. It extracts the color palette used for data encoding and identifies legend elements for later synchronization.

Phase 2 classifies the palette as categorical, sequential, or diverging using heuristics like L* shape analysis, pairwise ΔE patterns, and hue diversity.

Phase 3 counts categories, examines label types (numeric vs. string), analyzes color usage distribution, and for diverging palettes validates midpoint semantics.

Phase 4 checks if the classified palette type matches the extracted data signals. If there's a mismatch, it offers two paths: Path A repairs assuming the designer's palette type is correct, Path B repairs toward the data-appropriate palette type. It defaults to Path A in automated mode.

Phase 5 simulates the palette under the target CVD type using the Machado et al. (2009) model and runs the type-specific tests from the decision tree.

For categorical palettes: if all pairwise ΔE >= 8, it passes. Otherwise, if there are 8 or fewer categories it swaps in a CVD-safe palette (Okabe-Ito, IBM, Wong). For more than 8 categories it maximizes ΔE and flags for supplementary encoding.

For sequential palettes, it runs four tests in order: is L* monotonic, are steps uniform, is direction preserved, and is ΔL* >= 3 between steps.

For diverging palettes, it validates the midpoint semantic, checks midpoint extremum, endpoint separation, per-arm monotonicity, and arm symmetry.

Phase 6 generates a replacement palette. For sequential palettes, colors are ordered using PCA on the full Lab coordinates rather than L* alone, which correctly handles multi-hue ramps where lightness range is narrow but hue carries the ordering information. The repaired palette is re-simulated under CVD and verified against all invariants (iterating up to 3 times if needed). Non-data elements are checked to be unchanged, legend swatches are updated to match data marks, and a legend-data consistency check verifies that each data color's closest legend position is preserved after recoloring.

## Setup

Requires Python 3.8+ with numpy, lxml, and Pillow (Pillow is only needed for SVGs that embed raster-image legends):

```bash
pip install numpy lxml Pillow
```

## Usage

```bash
# run on test SVGs (7 curated test cases)
python3 main.py test_svgs/ output/ --cvd deutan

# run on your own SVGs
python3 main.py input_svgs/ output/ --cvd deutan

# protanopia simulation
python3 main.py input_svgs/ output/ --cvd protan

# tritanopia simulation
python3 main.py input_svgs/ output/ --cvd tritan
```

CVD types: `protan` (protanopia, L-cone/red deficiency), `deutan` (deuteranopia, M-cone/green deficiency, the default), `tritan` (tritanopia, S-cone/blue deficiency).

## Output structure

```
output/
  corrected/      recolored SVGs (only files that needed correction)
  originals/      backup copies of originals that were corrected
  reports/        per-file JSON with all 6 phases logged
  summary.json    overall statistics
```

## Understanding the reports

Each JSON report in `output/reports/` documents all 6 phases. Here's an example:

```json
{
  "file": "election_map.svg",
  "cvd_type": "deutan",
  "status": "recolored",
  "phases": {
    "phase1": {
      "n_data_colors": 9,
      "original_palette": ["#d73027", "#f46d43", "#fdae61", "#fee08b", "#ffffbf"]
    },
    "phase2": {
      "palette_type": "diverging",
      "confidence": 0.92,
      "details": { "is_v_shaped": true, "midpoint_index": 4 }
    },
    "phase5": {
      "tests_run": [
        { "name": "Diverging Test 1: Midpoint Extremum", "passed": true },
        { "name": "Diverging Test 2: Endpoint Separation", "passed": false,
          "value": 5.33, "threshold": 10.0 }
      ],
      "all_tests_passed": false
    }
  }
}
```

Status values: `passed` means already accessible, `recolored` means successfully repaired and verified, `recolored_with_warnings` means repaired but verification was incomplete, `skipped` means fewer than 2 data colors detected, `failed_to_recolor` means no passing palette could be generated, and `error` means a processing error occurred.

## Test cases and evaluation
- `Full Corpus/` — the 63-case evaluation corpus from Chapter 3 of the thesis, sampled from VisAnatomy and Wikimedia Commons across categorical, sequential, and diverging palettes. Pre-computed runs live under `results/full_corpus_deutan/` and `results/full_corpus_protan/`, with aggregates in `results/ch5_results.md`.

Headline numbers from the thesis (Chapter 5):

| Palette type | Invariant                  | Before → After |
|--------------|----------------------------|----------------|
| Categorical  | Pairwise distinguishability | 35% → 100%    |
| Sequential   | Lightness monotonicity     | 25% → 97%      |
| Sequential   | Perceptual uniformity      | 28% → 100%     |
| Diverging    | Bidirectional separability | 3% → 77%       |
| Diverging    | Midpoint integrity         | 90% → 97%      |

### Reproducing the evaluation

The pre-computed pipeline outputs for both CVD types are already committed under `results/full_corpus_deutan/` and `results/full_corpus_protan/`, so you can skip straight to the aggregation step if you just want to regenerate the tables or the figure. If you want to re-run the pipeline itself from scratch:

```bash
python3 main.py "Full Corpus/" results/full_corpus_deutan/ --cvd deutan
python3 main.py "Full Corpus/" results/full_corpus_protan/ --cvd protan
```

Each run writes per-file JSON reports to `reports/` inside that directory, plus a `summary.json`. The aggregation script reads those reports, re-runs all six invariant checks against both the original and corrected palettes for every file, collapses the results to the thesis invariants, and writes `results/ch5_results.md`:

```bash
python3 results/aggregate.py
```

Figure 4 in the paper is produced from that markdown table using Altair:

```bash
pip install altair pandas
python3 results/ch5_barley_plot.py
```

This writes `results/ch5_barley_plot.svg` and `results/ch5_barley_plot.png`.

## File structure

```
main.py                    pipeline orchestration, CLI entry point
color_science.py           sRGB/Lab conversions, CIEDE2000, Machado CVD simulation
svg_parser.py              SVG DOM parsing, element classification, recoloring
classifier.py              Phase 2: palette type classification
data_signal_extractor.py   Phase 3: data characteristic extraction
reconciler.py              Phase 4: palette vs data reconciliation
invariant_tests.py         Phase 5: CVD simulation and invariant testing
recolorer.py               Phase 6: repair strategies per palette type
extension-pyodide/         MV3 Chrome extension (recommended) -- runs the pipeline in-browser via Pyodide/WASM
extension/                 MV3 Chrome extension (alternative) -- talks to server/ over localhost
server/                    Flask wrapper used by extension/
test_svgs/                 7 curated test cases
input_svgs/                your SVGs go here
Full Corpus/               63-case evaluation corpus (VisAnatomy + Wikimedia)
results/                   pre-computed evaluation outputs and ch. 5 figures
figure_ch1/, Geo_examples/ figure assets used in the thesis
output/                    generated when you run main.py
```

## Color science details

CVD simulation uses the Machado, Oliveira, and Fernandes (2009) physiologically-based model, which simulates anomalous trichromacy with variable severity by manipulating spectral absorption functions. This is more accurate than the older Brettel et al. (1997) projection method.

Color distance uses CIEDE2000 (Sharma et al. 2005), the current standard for perceptual color difference. Key thresholds: categorical pairwise minimum ΔE >= 8, diverging endpoint minimum ΔE > 10, sequential adjacent minimum ΔL* >= 3. The categorical threshold of 8 was calibrated against the CVD-safe replacement palettes themselves: Okabe-Ito, Wong, and IBM Design reliably clear ΔE = 8 under simulation but not always 10, so a stricter cutoff would disqualify them as valid replacements (see Chapter 4 of the thesis).

The tool includes embedded CVD-safe palettes for repairs: Okabe-Ito, IBM Design, and Wong for categorical; single-hue blue, purple, and orange ramps for sequential; and blue-orange, purple-green, and blue-red endpoint pairs for diverging.

## Chrome extension

A Chrome extension wraps the pipeline so any SVG chart on a page can be
recolored in place: it scans the page for charts (inline `<svg>`, charts
inside iframes, and charts embedded as `<img src="chart.svg">`), runs the
same 6-phase pipeline as the CLI, and swaps in the corrected SVG, so what
shows up on the page is byte-identical to what `main.py` writes to
`corrected/`. There are two backends; **`extension-pyodide/` is the one to
use** — it's self-contained, with `extension/` kept around as a
Flask-backed alternative (closer to how this was originally built).

### extension-pyodide/ — in-browser, no server (recommended)

Runs the pipeline entirely in-browser via [Pyodide](https://pyodide.org)
(WASM) — no local server, nothing to install beyond the one-time runtime
fetch. Boots Pyodide in an MV3 offscreen document (~3-5s one-time cost,
warmed automatically on install) and produces byte-identical output to the
CLI.

```bash
# 1. Fetch + vendor the Pyodide runtime (one-time)
cd extension-pyodide
./scripts/fetch_pyodide.sh
./scripts/vendor_pyodide.sh

# 2. Load it in Chrome
#    - open  chrome://extensions/
#    - turn on Developer mode (top right)
#    - click  Load unpacked  →  select the  extension-pyodide/  folder
#    - to test on local .svg files: click  Details  →  Allow access to file URLs

# 3. Open any SVG (corpus file or any chart on the web), click the VCR icon,
#    pick a CVD type, flip "Show correction".
open -a "Google Chrome" "input_svgs/BarChart12.svg"
```

Full setup, architecture, and known risks (lxml version drift between the
server's venv and Pyodide's bundled lxml) in `extension-pyodide/README.md`.
Editing a pipeline file at the repo root? Run
`extension-pyodide/scripts/sync_pipeline.sh` afterward — Pyodide runs in a
sandboxed filesystem with its own copies, not a live view of the repo.

### extension/ — local Flask server (alternative)

What this was originally built as: a small Flask wrapper (`server/app.py`)
runs the same pipeline as the CLI, and the extension talks to it over
`localhost`. Functionally narrower than `extension-pyodide/` right now (no
iframe or `<img>`-embedded SVG support yet), but simpler to reason about
since there's no WASM runtime involved.

```bash
# 1. Start the local server (leave running)
pip install -r server/requirements.txt
python3 server/app.py

# 2. Load the extension in Chrome
#    - open  chrome://extensions/
#    - turn on Developer mode (top right)
#    - click  Load unpacked  →  select the  extension/  folder
#    - to test on local .svg files: click  Details  →  Allow access to file URLs

# 3. Open any SVG (corpus file or any chart on the web), click the VCR icon,
#    pick a CVD type, flip "Show correction".
open -a "Google Chrome" "input_svgs/BarChart12.svg"
```

Full notes in `extension/README.md`.

### Shared limitations (both backends)

Only `fill=` / inline `style=` colors are remapped, not stylesheet rules;
D3/Vega charts that re-render on hover will lose the correction and need a
re-toggle; canvas-rendered charts can't be recolored at all (no DOM/markup
to target — a different approach entirely, and arguably the wrong one for
this tool's premise).
