"""Pyodide-side counterpart to server/app.py's Flask glue.

Same contract, same _build_response logic (kept in sync by hand — there's no
import between server/ and here since one runs under CPython+Flask and the
other under Pyodide). offscreen.js calls process_svg(svg_text, cvd_type) and
gets back a JSON string it parses on the JS side, sidestepping PyProxy/JS
object conversion entirely.
"""

import io
import os
import tempfile
import json

from main import process_single_svg


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
    # them without another process_svg() round-trip.
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


def process_svg(svg_text, cvd_type):
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

    return json.dumps(_build_response(report, parsed=parsed))
