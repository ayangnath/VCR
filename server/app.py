"""Flask wrapper around the VCR pipeline.

Exposes one endpoint:
    POST /detect
        body:   {"svg": "<svg>...</svg>", "cvd_type": "deutan"}
        returns: {
            "status": "passed" | "recolored" | "recolored_with_warnings" | "skipped" | "error",
            "palette_type": "categorical" | "sequential" | "diverging" | null,
            "n_colors": int,
            "original_palette": ["#hex", ...],
            "mapping": {"#origHex": "#newHex", ...} | {},
            "new_palette": ["#hex", ...],
            "mismatch": bool,
            "mismatch_reason": str | null,
            "warnings": [str, ...]
        }

Runs on localhost:5000. Allows any chrome-extension:// origin via CORS.
"""

import hashlib
import io
import os
import sys
import tempfile
from functools import lru_cache

from flask import Flask, request, jsonify

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import process_single_svg  # noqa: E402


app = Flask(__name__)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


# In-memory cache keyed on (svg_hash, cvd_type). The pipeline is deterministic
# for a given SVG + CVD type, so repeat calls can reuse the result.
_result_cache = {}


def _svg_hash(svg_text):
    return hashlib.sha1(svg_text.encode("utf-8")).hexdigest()


def _build_response(report, parsed=None):
    phases = report.get("phases", {})
    p1 = phases.get("phase1", {})
    p2 = phases.get("phase2", {})
    p3 = phases.get("phase3", {})
    p4 = phases.get("phase4", {})
    p6 = phases.get("phase6", {})

    palette_type = p4.get("reconciled_type") or p2.get("palette_type")
    mapping = p6.get("color_mapping", {}) or {}
    new_palette = p6.get("new_palette") or p1.get("original_palette", [])

    # When recoloring was applied, serialize the corrected SVG (identical
    # bytes to what the CLI writes to corrected/) so the extension can
    # swap the entire SVG element rather than re-implementing the apply
    # step (legend sync, raster legend pixel ops, DR6/DR7 logic) in JS.
    corrected_svg = None
    if parsed is not None and report.get("recoloring_applied"):
        try:
            buf = io.BytesIO()
            parsed.tree.write(buf, xml_declaration=True,
                              encoding="utf-8", pretty_print=True)
            corrected_svg = buf.getvalue().decode("utf-8")
        except Exception:
            corrected_svg = None

    # Ranked alternative palettes for the popup's palette navigator. Each
    # candidate already carries its own corrected_svg (built in main.py,
    # one fresh SVG parse per candidate) so the popup can switch between
    # them without another /detect round-trip.
    candidates = [
        {
            "key": c.get("key"),
            "name": c.get("name"),
            "metric_label": c.get("metric_label"),
            "metric_value": c.get("metric_value"),
            "new_palette": c.get("new_palette", []),
            "corrected_svg": c.get("corrected_svg"),
        }
        for c in p6.get("candidates", [])
    ]

    return {
        "status": report.get("status"),
        "palette_type": palette_type,
        "n_colors": p1.get("n_data_colors", 0),
        "original_palette": p1.get("original_palette", []),
        "mapping": mapping,
        "new_palette": new_palette,
        "mismatch": bool(p3.get("possible_mismatch")),
        "mismatch_reason": p4.get("mismatch_explanation"),
        "warnings": report.get("warnings", []),
        "corrected_svg": corrected_svg,
        "candidates": candidates,
    }


@app.route("/detect", methods=["POST", "OPTIONS"])
def detect():
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    svg_text = data.get("svg")
    cvd_type = data.get("cvd_type", "deutan")

    if not svg_text or not isinstance(svg_text, str):
        return jsonify({"error": "missing 'svg' field"}), 400
    if cvd_type not in ("protan", "deutan", "tritan"):
        return jsonify({"error": f"invalid cvd_type: {cvd_type}"}), 400

    cache_key = (_svg_hash(svg_text), cvd_type)
    if cache_key in _result_cache:
        return jsonify(_result_cache[cache_key])

    # parse_svg wants a file path, so round-trip through a tmp file.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".svg", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(svg_text)
        tmp_path = tmp.name

    try:
        parsed, report = process_single_svg(tmp_path, cvd_type=cvd_type)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    response = _build_response(report, parsed=parsed)
    _result_cache[cache_key] = response
    return jsonify(response)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
