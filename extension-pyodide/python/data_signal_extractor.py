# Extract data signals from SVG visualizations (DR2): category count, label types
# (string vs numeric), color usage distribution, and midpoint semantics.

import numpy as np
import re
from typing import List, Tuple, Dict, Optional
from color_science import srgb_to_lab, rgb_to_hex


# Data characteristics extracted from a visualization. The data might not
# match what the palette type suggests, so we check independently.
class DataSignals:
    def __init__(self):
        self.n_categories = 0
        self.has_numeric_labels = False
        self.has_string_labels = False
        self.label_evidence = []

        self.usage_distribution = "unknown"
        self.color_usage_counts = {}

        self.has_semantic_midpoint = False
        self.midpoint_type = None
        self.midpoint_evidence = []

        self.possible_mismatch = False
        self.mismatch_reason = None


# Check if a label looks numeric (handles currency, percentages, etc).
def _is_numeric_label(text: str) -> bool:
    if not text:
        return False

    # strip currency, percent signs, thousands commas
    cleaned = text.strip()
    cleaned = re.sub(r'^[\$€£¥]', '', cleaned)
    cleaned = re.sub(r'%$', '', cleaned)
    cleaned = re.sub(r',', '', cleaned)
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


# Check if any labels represent zero (strong evidence for a diverging midpoint).
def _detect_zero_labels(labels: List[str]) -> bool:
    for label in labels:
        if not label:
            continue
        cleaned = label.strip().lstrip('±+-')
        if _is_numeric_label(cleaned):
            try:
                value = float(re.sub(r'[^\d.-]', '', cleaned))
                if abs(value) < 0.001:
                    return True
            except ValueError:
                pass
    return False


# Classify color usage as uniform, skewed, clustered, or full_range.
def _analyze_color_usage_distribution(color_usage: Dict[str, int]) -> str:
    if not color_usage:
        return "unknown"

    counts = np.array(list(color_usage.values()))

    if len(counts) == 1:
        return "uniform"

    mean_count = np.mean(counts)
    std_count = np.std(counts)
    cv = std_count / (mean_count + 1e-10)

    if cv < 0.3:
        return "uniform"
    elif cv > 1.0:
        return "full_range"
    else:
        ratio = np.max(counts) / max(np.min(counts), 1)
        if ratio > 5:
            return "skewed"
        else:
            return "clustered"


# Extract data characteristics from a parsed SVG: category count, label
# types, color distribution, and midpoint semantics.
def extract_data_signals(parsed_svg, palette_type: str, classification_details: dict = None) -> DataSignals:
    signals = DataSignals()
    classification_details = classification_details or {}

    signals.n_categories = len(parsed_svg.palette)

    # check labels for numeric vs string content
    labels = getattr(parsed_svg, 'labels', [])

    if labels:
        for label_text in labels:
            is_num = _is_numeric_label(label_text)
            signals.label_evidence.append((label_text, is_num))
            if is_num:
                signals.has_numeric_labels = True
            else:
                signals.has_string_labels = True

        # a "0" label is strong evidence for a diverging midpoint
        if _detect_zero_labels(labels):
            signals.has_semantic_midpoint = True
            signals.midpoint_type = "zero"
            signals.midpoint_evidence.append("Label '0' found")

    # analyze color usage distribution
    try:
        color_counts = {}
        for elem in parsed_svg.data_elements:
            if hasattr(elem, 'get'):
                fill = elem.get('fill', '')
            elif isinstance(elem, dict):
                fill = elem.get('fill', '')
            else:
                continue

            if fill:
                color_counts[fill] = color_counts.get(fill, 0) + 1

        if not color_counts and parsed_svg.palette:
            for color in parsed_svg.palette:
                hex_color = rgb_to_hex(color) if isinstance(color, tuple) else color
                color_counts[hex_color] = 1

        signals.color_usage_counts = color_counts
        signals.usage_distribution = _analyze_color_usage_distribution(color_counts)
    except Exception:
        signals.usage_distribution = "uniform"

    if palette_type == "diverging":
        _validate_diverging_midpoint(signals, parsed_svg.palette, classification_details)

    _detect_mismatches(signals, palette_type, classification_details)

    return signals


# Check if the midpoint is achromatic, or if hue clusters provide enough
# evidence that this is intentionally diverging.
def _validate_diverging_midpoint(signals: DataSignals, palette: List[Tuple[int, int, int]],
                                  classification_details: dict = None):
    classification_details = classification_details or {}

    # strong hue clusters (like reds vs blues) are good evidence for diverging
    if classification_details.get("has_hue_clusters"):
        cluster_info = classification_details.get("cluster_info", {})
        hue_separation = cluster_info.get("hue_separation", 0)

        if hue_separation >= 60:
            signals.has_semantic_midpoint = True
            if not signals.midpoint_type:
                signals.midpoint_type = "hue_cluster"
            signals.midpoint_evidence.append(
                f"Two distinct hue clusters (separation={hue_separation:.1f} deg) "
                f"suggest intentional diverging design."
            )
            return

    # traditional midpoint color check
    if len(palette) < 3:
        return

    mid_idx = len(palette) // 2
    mid_color = palette[mid_idx]
    mid_lab = srgb_to_lab(mid_color)

    chroma = np.sqrt(mid_lab[1]**2 + mid_lab[2]**2)

    if chroma < 20:
        signals.has_semantic_midpoint = True
        if not signals.midpoint_type:
            signals.midpoint_type = "neutral"
        signals.midpoint_evidence.append(f"Achromatic midpoint (chroma={chroma:.1f})")
    else:
        # check if any color in the palette is achromatic
        has_any_achromatic = False
        for color in palette:
            lab = srgb_to_lab(color)
            c = np.sqrt(lab[1]**2 + lab[2]**2)
            if c < 20:
                has_any_achromatic = True
                break

        if has_any_achromatic:
            signals.has_semantic_midpoint = True
            if not signals.midpoint_type:
                signals.midpoint_type = "neutral"
            signals.midpoint_evidence.append("Achromatic color found in palette.")
        else:
            signals.midpoint_evidence.append(
                f"WARNING: Chromatic midpoint (chroma={chroma:.1f}). "
                f"Diverging palettes typically have neutral midpoints."
            )


# Flag cases where the palette type doesn't match the data signals.
def _detect_mismatches(signals: DataSignals, palette_type: str, classification_details: dict = None):
    classification_details = classification_details or {}

    # categorical palette with only numeric labels suggests continuous data
    if palette_type == "categorical":
        if signals.has_numeric_labels and not signals.has_string_labels:
            signals.possible_mismatch = True
            signals.mismatch_reason = (
                "Categorical palette but labels are numeric. "
                "Data may be continuous/ordinal (consider sequential)."
            )

        elif signals.n_categories > 10:
            signals.possible_mismatch = True
            signals.mismatch_reason = (
                f"{signals.n_categories} categories detected. "
                f"Humans struggle to distinguish >8-10 discrete colors. "
                f"Data may be better suited to sequential encoding."
            )

    elif palette_type == "sequential":
        if signals.has_string_labels and not signals.has_numeric_labels:
            signals.possible_mismatch = True
            signals.mismatch_reason = (
                "Sequential palette but labels are strings. "
                "Data may be categorical."
            )

        elif signals.usage_distribution == "skewed" and signals.n_categories <= 8:
            signals.possible_mismatch = True
            signals.mismatch_reason = (
                "Sequential palette but color usage is skewed/sparse. "
                "Data may be categorical with grouped values."
            )

    elif palette_type == "diverging":
        has_strong_hue_clusters = False
        if classification_details.get("has_hue_clusters"):
            cluster_info = classification_details.get("cluster_info", {})
            hue_separation = cluster_info.get("hue_separation", 0)
            if hue_separation >= 60:
                has_strong_hue_clusters = True

        # only flag if there's no midpoint evidence and no strong hue clusters
        if not signals.has_semantic_midpoint and not has_strong_hue_clusters and signals.n_categories >= 5:
            signals.possible_mismatch = True
            signals.mismatch_reason = (
                "Diverging palette but no clear semantic midpoint detected. "
                "Verify that data has meaningful reference value (zero, mean, etc.)."
            )
