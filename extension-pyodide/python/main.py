# Main pipeline for CVD-aware SVG recoloring.
# Usage: python main.py input_folder/ output_folder/ [--cvd protan|deutan|tritan]

import os
import sys
import json
import time
import shutil
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from color_science import rgb_to_hex, parse_color, srgb_to_lab, ciede2000
from svg_parser import parse_svg, apply_recoloring, write_svg
from classifier import classify_palette
from data_signal_extractor import extract_data_signals
from reconciler import reconcile_palette_vs_data
from invariant_tests import run_invariant_tests, all_tests_passed
from recolorer import recolor_palette


# Check whether L* values are monotonic (all increasing or all decreasing).
def _is_L_monotonic(L_values, tol=0.5):
    if len(L_values) < 2:
        return True
    diffs = [L_values[i + 1] - L_values[i] for i in range(len(L_values) - 1)]
    return all(d >= -tol for d in diffs) or all(d <= tol for d in diffs)


# Compute a positional ordering for data palette colors based on the legend's
# spatial arrangement.  Falls back to palette order when the legend has fewer
# than 3 colors.  Returns a list of indices into `colors` sorted from "lowest
# data value" to "highest" (i.e. position in the legend).
def _compute_positional_order(colors, legend_colors_hex):
    n = len(colors)
    palette_hex = [rgb_to_hex(c) for c in colors]

    if len(legend_colors_hex) >= 3:
        # Match each data color to its nearest legend color by ΔE
        legend_labs = [srgb_to_lab(parse_color(h)) for h in legend_colors_hex]
        positions = []
        for i, c in enumerate(colors):
            c_lab = srgb_to_lab(c)
            best_pos = 0
            best_d = float('inf')
            for j, l_lab in enumerate(legend_labs):
                d = ciede2000(c_lab, l_lab)
                if d < best_d:
                    best_d = d
                    best_pos = j
            positions.append((best_pos, i))
        positions.sort(key=lambda x: x[0])
        return [idx for _, idx in positions]
    else:
        # No usable legend; use palette order (order of first appearance)
        return list(range(n))


# After recoloring, verify that each data color still maps to the same
# legend position it did before.  Returns a list of mismatch dicts.
def _check_legend_data_consistency(colors, color_mapping, legend_colors_hex):
    if len(legend_colors_hex) < 2:
        return []

    legend_labs = [srgb_to_lab(parse_color(h)) for h in legend_colors_hex]

    def closest_legend_pos(hex_c):
        rgb = parse_color(hex_c)
        if not rgb:
            return -1
        lab = srgb_to_lab(rgb)
        best_pos, best_d = 0, float('inf')
        for j, l_lab in enumerate(legend_labs):
            d = ciede2000(lab, l_lab)
            if d < best_d:
                best_d = d
                best_pos = j
        return best_pos

    # Build new legend colors after mapping
    new_legend = [color_mapping.get(h, h) for h in legend_colors_hex]
    new_legend_labs = [srgb_to_lab(parse_color(h)) for h in new_legend]

    def closest_new_legend_pos(hex_c):
        rgb = parse_color(hex_c)
        if not rgb:
            return -1
        lab = srgb_to_lab(rgb)
        best_pos, best_d = 0, float('inf')
        for j, l_lab in enumerate(new_legend_labs):
            d = ciede2000(lab, l_lab)
            if d < best_d:
                best_d = d
                best_pos = j
        return best_pos

    mismatches = []
    for c in colors:
        old_hex = rgb_to_hex(c)
        new_hex = color_mapping.get(old_hex, old_hex)
        pos_before = closest_legend_pos(old_hex)
        pos_after = closest_new_legend_pos(new_hex)
        if pos_before != pos_after:
            mismatches.append({
                "color_before": old_hex,
                "color_after": new_hex,
                "legend_pos_before": pos_before,
                "legend_pos_after": pos_after,
            })
    return mismatches


# Make numpy types JSON-serializable.
def _json_default(obj):
    import numpy as np
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return str(obj)


# Run one SVG through the full pipeline. Returns (parsed_svg, report).
def process_single_svg(svg_path, cvd_type="deutan", user_choice=None):
    report = {
        "file": os.path.basename(svg_path),
        "cvd_type": cvd_type,
        "status": "pending",
        "phases": {},
        "warnings": [],
    }

    try:
        # phase 1: parse and extract palette
        parsed = parse_svg(svg_path)
        colors = parsed.palette

        report["phases"]["phase1"] = {
            "n_data_elements": len(parsed.data_elements),
            "n_legend_elements": len(parsed.legend_elements),
            "n_nondata_elements": len(parsed.nondata_elements),
            "n_data_colors": len(colors),
            "original_palette": [rgb_to_hex(c) for c in colors],
        }

        if len(colors) < 2:
            report["status"] = "skipped"
            report["warnings"].append("Fewer than 2 data colors detected; nothing to check.")
            return parsed, report

        # Detect non-monotonic luminance in the legend encoding.
        # When L* diffs on the legend change sign, L*-rank remapping may
        # shuffle colors.  We only check when we have ≥3 discrete legend
        # swatches in spatial order — gradient legends and DOM walk order
        # are not reliable signals for this test.
        legend_hex = parsed.ordered_legend_colors
        legend_L_monotonic = True  # assume monotonic unless proven otherwise

        if len(legend_hex) >= 3:
            legend_Ls = [srgb_to_lab(parse_color(h))[0] for h in legend_hex]
            legend_L_monotonic = _is_L_monotonic(legend_Ls)

        report["phases"]["phase1"]["legend_luminance_monotonic"] = legend_L_monotonic
        report["phases"]["phase1"]["n_legend_swatches_ordered"] = len(legend_hex)
        if not legend_L_monotonic:
            report["warnings"].append(
                "Non-monotonic luminance detected in legend swatches. "
                "L*-rank remapping may re-order values; using legend-based "
                "positional mapping instead."
            )

        # phase 2: classify palette type (DR1)
        classification = classify_palette(colors)

        report["phases"]["phase2"] = {
            "palette_type": classification.palette_type,
            "confidence": round(classification.confidence, 2),
            "details": {
                k: v for k, v in classification.details.items()
                if k not in ("lightness_values",)
            },
        }

        # phase 3: extract data signals (DR2)
        signals = extract_data_signals(parsed, classification.palette_type, classification.details)

        report["phases"]["phase3"] = {
            "n_categories": signals.n_categories,
            "has_numeric_labels": signals.has_numeric_labels,
            "has_string_labels": signals.has_string_labels,
            "usage_distribution": signals.usage_distribution,
            "has_semantic_midpoint": signals.has_semantic_midpoint,
            "midpoint_type": signals.midpoint_type,
            "possible_mismatch": signals.possible_mismatch,
        }

        if signals.possible_mismatch:
            report["warnings"].append(f"PALETTE-DATA MISMATCH: {signals.mismatch_reason}")

        # phase 4: reconcile palette type vs data signals (DR3/DR4)
        reconciliation = reconcile_palette_vs_data(
            classification.palette_type,
            signals,
            user_preference=user_choice,
            classification_details=classification.details
        )

        report["phases"]["phase4"] = {
            "needs_user_choice": reconciliation.needs_user_choice,
            "reconciled_type": reconciliation.reconciled_type,
            "mismatch_explanation": reconciliation.mismatch_explanation,
        }

        if reconciliation.needs_user_choice:
            report["warnings"].append(
                f"Path A (default): {reconciliation.path_a_description}"
            )
            report["warnings"].append(
                f"Path B (alternative): {reconciliation.path_b_description}"
            )

        final_type = reconciliation.reconciled_type

        # phase 5: test invariants under CVD simulation (DR3)
        test_results = run_invariant_tests(
            colors, final_type, cvd_type,
            classification_details=classification.details
        )
        all_passed = all_tests_passed(test_results)

        report["phases"]["phase5"] = {
            "tests_run": [
                {
                    "name": t.test_name,
                    "passed": t.passed,
                    "value": t.metric_value,
                    "threshold": t.threshold,
                    "repair_suggestion": t.repair_suggestion,
                }
                for t in test_results
            ],
            "all_tests_passed": all_passed,
        }

        if all_passed:
            report["status"] = "passed"
            report["recoloring_applied"] = False
            return parsed, report

        # phase 6: recolor and verify
        MAX_REPAIR_ITERATIONS = 3
        iteration_log = []
        best_mapping = None
        best_verify_results = None
        best_verify_passed = False

        # Compute positional order only when we have ≥3 real legend swatches
        # whose L* is non-monotonic.  Without a usable spatial legend,
        # L*-rank mapping is the best available heuristic (DOM walk order
        # is meaningless for scattered layouts like calendars / heatmaps).
        positional_order = None
        if not legend_L_monotonic and len(legend_hex) >= 3 \
                and final_type in ("sequential",):
            positional_order = _compute_positional_order(colors, legend_hex)

        for iteration in range(MAX_REPAIR_ITERATIONS):
            color_mapping = recolor_palette(
                colors, final_type, cvd_type, test_results,
                attempt=iteration, positional_order=positional_order
            )

            if not color_mapping:
                iteration_log.append({
                    "iteration": iteration + 1,
                    "result": "no_mapping_generated",
                })
                continue

            new_colors_rgb = []
            for c in colors:
                hex_c = rgb_to_hex(c)
                if hex_c in color_mapping:
                    new_rgb = parse_color(color_mapping[hex_c])
                    new_colors_rgb.append(new_rgb)
                else:
                    new_colors_rgb.append(c)

            verify_results = run_invariant_tests(
                new_colors_rgb, final_type, cvd_type,
                classification_details=classification.details
            )
            verify_passed = all_tests_passed(verify_results)

            iteration_log.append({
                "iteration": iteration + 1,
                "color_mapping": color_mapping,
                "verification_passed": verify_passed,
                "tests": [
                    {"name": t.test_name, "passed": t.passed, "value": t.metric_value}
                    for t in verify_results
                ],
            })

            if verify_passed:
                best_mapping = color_mapping
                best_verify_results = verify_results
                best_verify_passed = True
                break
            elif best_mapping is None or sum(t.passed for t in verify_results) > sum(
                t.passed for t in (best_verify_results or [])
            ):
                best_mapping = color_mapping
                best_verify_results = verify_results

        if best_mapping is None:
            report["status"] = "failed_to_recolor"
            report["warnings"].append("Could not generate a passing palette after all attempts.")
            report["phases"]["phase6"] = {"iteration_log": iteration_log}
            return parsed, report

        apply_recoloring(parsed, best_mapping)

        # DR6: make sure non-data elements weren't touched
        dr6_violations = []
        for elem in parsed.nondata_elements:
            if elem.fill and elem.fill != elem.original_fill:
                dr6_violations.append(rgb_to_hex(elem.original_fill))
            if elem.stroke and elem.stroke != elem.original_stroke:
                dr6_violations.append(rgb_to_hex(elem.original_stroke))
        if dr6_violations:
            report["warnings"].append(
                f"{len(dr6_violations)} non-data element(s) were modified unexpectedly."
            )

        # DR7: check that legend swatches match the recolored data
        dr7_ok = True
        for elem in parsed.legend_elements:
            ec = elem.effective_color
            if ec:
                hex_ec = rgb_to_hex(ec)
                expected_new = best_mapping.get(rgb_to_hex(elem.original_fill or elem.original_stroke))
                if expected_new and hex_ec != expected_new:
                    dr7_ok = False
        if not dr7_ok:
            report["warnings"].append(
                "Some legend swatches may not match the recolored data palette."
            )

        # DR7b: verify that data→legend position mapping is preserved.
        # If a data color's closest legend position changed, the recoloring
        # introduced a discrepancy between what the legend shows and what
        # the visualization displays.
        legend_mismatches = []
        if legend_hex:
            legend_mismatches = _check_legend_data_consistency(
                colors, best_mapping, legend_hex
            )
            if legend_mismatches:
                report["warnings"].append(
                    f"{len(legend_mismatches)} data color(s) now map to a "
                    f"different legend position after recoloring."
                )

        final_new_colors = []
        for c in colors:
            hex_c = rgb_to_hex(c)
            if hex_c in best_mapping:
                final_new_colors.append(parse_color(best_mapping[hex_c]))
            else:
                final_new_colors.append(c)

        phase6_report = {
            "color_mapping": best_mapping,
            "new_palette": [rgb_to_hex(c) for c in final_new_colors],
            "verification_tests": [
                {
                    "name": t.test_name,
                    "passed": t.passed,
                    "value": t.metric_value,
                }
                for t in best_verify_results
            ],
            "verification_passed": best_verify_passed,
            "iterations_used": len(iteration_log),
            "iteration_log": iteration_log,
        }
        if legend_mismatches:
            phase6_report["legend_position_shifts"] = legend_mismatches
        report["phases"]["phase6"] = phase6_report

        if best_verify_passed:
            report["status"] = "recolored"
            report["recoloring_applied"] = True
        else:
            report["status"] = "recolored_with_warnings"
            report["recoloring_applied"] = True
            report["warnings"].append(
                f"Post-recoloring verification did not fully pass after "
                f"{len(iteration_log)} attempt(s). Best result applied. Manual review recommended."
            )

        return parsed, report

    except Exception as e:
        report["status"] = "error"
        report["warnings"].append(f"Error processing file: {str(e)}")
        import traceback
        report["traceback"] = traceback.format_exc()
        return None, report


# Batch-process all SVGs in a folder.
def process_folder(input_dir, output_dir, cvd_type="deutan"):
    input_path = Path(input_dir)
    output_path = Path(output_dir)

    if output_path.exists():
        shutil.rmtree(output_path)

    corrected_dir = output_path / "corrected"
    original_dir = output_path / "originals"
    reports_dir = output_path / "reports"
    for d in (corrected_dir, original_dir, reports_dir):
        d.mkdir(parents=True, exist_ok=True)

    svg_files = sorted(input_path.glob("*.svg"))
    if not svg_files:
        print(f"No SVG files found in {input_dir}")
        return

    summary = {
        "total_files": len(svg_files),
        "cvd_type": cvd_type,
        "results": {
            "passed": 0,
            "recolored": 0,
            "recolored_with_warnings": 0,
            "skipped": 0,
            "error": 0,
            "failed_to_recolor": 0,
        },
        "files": [],
    }

    print(f"\n{'='*70}")
    print(f"  CVD-Aware SVG Recoloring Pipeline")
    print(f"  CVD Type: {cvd_type} | Files: {len(svg_files)}")
    print(f"{'='*70}\n")

    for svg_file in svg_files:
        filename = svg_file.name
        print(f"  Processing: {filename}...", end=" ", flush=True)

        t0 = time.time()
        parsed, report = process_single_svg(str(svg_file), cvd_type)
        elapsed = time.time() - t0

        report["processing_time_sec"] = round(elapsed, 3)
        status = report["status"]

        status_icons = {
            "passed": "✓ PASS",
            "recolored": "↻ RECOLORED",
            "recolored_with_warnings": "? RECOLORED*",
            "skipped": "– SKIP",
            "error": "✗ ERROR",
            "failed_to_recolor": "! FAIL",
        }
        icon = status_icons.get(status, "? UNKNOWN")

        phase1 = report.get("phases", {}).get("phase1", {})
        phase2 = report.get("phases", {}).get("phase2", {})
        phase4 = report.get("phases", {}).get("phase4", {})
        ptype = phase4.get("reconciled_type") or phase2.get("palette_type", "?")
        n_colors = phase1.get("n_data_colors", 0)
        print(f"[{icon}] type={ptype} colors={n_colors} ({elapsed:.2f}s)")

        if report.get("warnings"):
            for w in report["warnings"]:
                print(f"      WARNING: {w}")

        if status in ("recolored", "recolored_with_warnings") and parsed is not None:
            write_svg(parsed, str(corrected_dir / filename))
            shutil.copy2(str(svg_file), str(original_dir / filename))

        report_file = reports_dir / f"{svg_file.stem}_report.json"
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2, default=_json_default)

        summary["results"][status] = summary["results"].get(status, 0) + 1
        phase5 = report.get("phases", {}).get("phase5", {})
        signal_passed = phase5.get("all_tests_passed", None)

        summary["files"].append({
            "file": filename,
            "status": status,
            "palette_type": ptype,
            "n_colors": n_colors,
            "signal_passed": signal_passed,
            "recolored": report.get("recoloring_applied", False),
        })

    with open(output_path / "summary.json", 'w') as f:
        json.dump(summary, f, indent=2, default=_json_default)

    r = summary["results"]
    print(f"\n{'='*70}")
    print(f"  Summary")
    print(f"{'='*70}")
    print(f"  Total:     {summary['total_files']}")
    print(f"  Passed:    {r['passed']} (already accessible)")
    print(f"  Recolored: {r['recolored']}")
    if r.get('recolored_with_warnings', 0):
        print(f"  Recolored*: {r['recolored_with_warnings']} (with warnings)")
    print(f"  Skipped:   {r['skipped']}")
    print(f"  Errors:    {r['error']}")
    if r.get('failed_to_recolor', 0):
        print(f"  Failed:    {r['failed_to_recolor']}")
    print(f"\n  Output: {output_path}/")
    print(f"    corrected/  - accessible SVGs")
    print(f"    originals/  - pre-correction copies")
    print(f"    reports/    - per-file analysis (JSON)")
    print(f"    summary.json")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python main.py INPUT_DIR OUTPUT_DIR [--cvd protan|deutan|tritan]")
        print()
        print("Processes SVG visualizations for colorblind accessibility.")
        print("Each SVG is analyzed, classified, checked against color difference")
        print("thresholds (CIEDE2000), and recolored if needed.")
        print()
        print("Options:")
        print("  --cvd TYPE   CVD type to simulate (default: deutan)")
        print("               protan = protanopia (L-cone deficiency)")
        print("               deutan = deuteranopia (M-cone deficiency)")
        print("               tritan = tritanopia (S-cone deficiency)")
        sys.exit(1)

    input_dir = sys.argv[1]
    output_dir = sys.argv[2]

    cvd_type = "deutan"
    if "--cvd" in sys.argv:
        idx = sys.argv.index("--cvd")
        if idx + 1 < len(sys.argv):
            cvd_type = sys.argv[idx + 1]
            if cvd_type not in ("protan", "deutan", "tritan"):
                print(f"Unknown CVD type: {cvd_type}. Use protan, deutan, or tritan.")
                sys.exit(1)

    if not os.path.isdir(input_dir):
        print(f"Input directory not found: {input_dir}")
        sys.exit(1)

    process_folder(input_dir, output_dir, cvd_type)
