# Palette classification and signal verification (DR1). Classifies palettes as
# categorical, sequential, or diverging, then runs the right checks (pairwise
# delta E for categorical, L* monotonicity for sequential, etc).

import numpy as np
from color_science import srgb_to_lab

MONOTONICITY_TOLERANCE = 0.5      # floating point tolerance


class PaletteClassification:
    # Result of classifying a palette as categorical, sequential, or diverging.
    def __init__(self, palette_type, confidence, details=None):
        self.palette_type = palette_type
        self.confidence = confidence
        self.details = details or {}

    def __repr__(self):
        return f"PaletteClassification({self.palette_type}, conf={self.confidence:.2f})"

# Get L* values from a list of sRGB colors.
def _lightness_values(colors):
    return np.array([srgb_to_lab(c)[0] for c in colors])

# Check if values are monotonically increasing or decreasing.
def _is_monotonic(values, tolerance=MONOTONICITY_TOLERANCE):
    if len(values) < 2:
        return True, "trivial"
    diffs = np.diff(values)
    if np.all(diffs > -tolerance):
        return True, "increasing"
    if np.all(diffs < tolerance):
        return True, "decreasing"
    return False, "non-monotonic"

# Check if L* values form a V or inverted-V shape (diverging indicator).
# Returns (is_v_shaped, midpoint_index)
def _detect_v_shape(lightness_values):
    n = len(lightness_values)
    if n < 3:
        return False, -1

    # look for a min or max in the interior
    min_idx = np.argmin(lightness_values)
    max_idx = np.argmax(lightness_values)

    # V-shape: decreases to min then increases
    if 0 < min_idx < n - 1:
        left = lightness_values[:min_idx + 1]
        right = lightness_values[min_idx:]
        left_mono, _ = _is_monotonic(left)
        right_mono, _ = _is_monotonic(right)
        if left_mono and right_mono:
            left_decreasing = lightness_values[0] > lightness_values[min_idx]
            right_increasing = lightness_values[-1] > lightness_values[min_idx]
            if left_decreasing and right_increasing:
                return True, min_idx

    # inverted V: increases to max then decreases
    if 0 < max_idx < n - 1:
        left = lightness_values[:max_idx + 1]
        right = lightness_values[max_idx:]
        left_mono, _ = _is_monotonic(left)
        right_mono, _ = _is_monotonic(right)
        if left_mono and right_mono:
            left_increasing = lightness_values[max_idx] > lightness_values[0]
            right_decreasing = lightness_values[max_idx] > lightness_values[-1]
            if left_increasing and right_decreasing:
                return True, max_idx

    return False, -1

# Circular standard deviation of hue angles in Lab a*b* space
def _hue_diversity(colors):
    labs = [srgb_to_lab(c) for c in colors]
    hues = [np.degrees(np.arctan2(lab[2], lab[1])) % 360 for lab in labs]
    if len(hues) < 2:
        return 0
    # circular std dev
    hues_rad = np.radians(hues)
    S = np.mean(np.sin(hues_rad))
    C = np.mean(np.cos(hues_rad))
    R = np.sqrt(S**2 + C**2)
    return np.degrees(np.sqrt(-2 * np.log(max(R, 1e-10))))

# Check if the palette splits into two distinct hue groups (like reds vs blues)
def _detect_hue_clusters(colors, min_cluster_size=2):
    labs = [srgb_to_lab(c) for c in colors]

    # only look at chromatic colors
    chromatic = [(i, lab) for i, lab in enumerate(labs)
                 if np.sqrt(lab[1]**2 + lab[2]**2) > 15]

    if len(chromatic) < min_cluster_size * 2:
        return False, {}

    hues = [np.degrees(np.arctan2(lab[2], lab[1])) % 360 for _, lab in chromatic]
    hues_sorted = sorted(hues)

    # find the biggest gap in the hue circle
    max_gap = 0
    split_idx = 0

    for i in range(len(hues_sorted)):
        if i < len(hues_sorted) - 1:
            gap = hues_sorted[i+1] - hues_sorted[i]
        else:
            # wrap-around
            gap = (hues_sorted[0] + 360) - hues_sorted[i]

        if gap > max_gap:
            max_gap = gap
            split_idx = i

    # if there's a big enough gap, split into two clusters
    if max_gap > 60:
        if split_idx < len(hues_sorted) - 1:
            split_point = (hues_sorted[split_idx] + hues_sorted[split_idx + 1]) / 2
        else:
            # wrap-around case
            split_point = (hues_sorted[split_idx] + hues_sorted[0] + 360) / 2
            if split_point >= 360:
                split_point -= 360

        # assign hues to clusters based on which side of the split they fall on
        cluster1_hues = []
        cluster2_hues = []

        for h in hues:
            dist_forward = (h - split_point) % 360
            if dist_forward < 180:
                cluster2_hues.append(h)
            else:
                cluster1_hues.append(h)

        if len(cluster1_hues) >= min_cluster_size and len(cluster2_hues) >= min_cluster_size:
            def circular_mean(angles):
                angles_rad = np.radians(angles)
                mean_sin = np.mean(np.sin(angles_rad))
                mean_cos = np.mean(np.cos(angles_rad))
                return np.degrees(np.arctan2(mean_sin, mean_cos)) % 360

            c1_mean = circular_mean(cluster1_hues)
            c2_mean = circular_mean(cluster2_hues)
            hue_separation = abs(c2_mean - c1_mean)
            hue_separation = min(hue_separation, 360 - hue_separation)

            return True, {
                "cluster1_size": len(cluster1_hues),
                "cluster2_size": len(cluster2_hues),
                "cluster1_mean_hue": round(c1_mean, 1),
                "cluster2_mean_hue": round(c2_mean, 1),
                "hue_separation": round(hue_separation, 1),
                "max_gap": round(max_gap, 1),
            }

    return False, {}

# Find near-neutral colors in the palette. Returns (indices, count)
def _find_achromatic_colors(colors, chroma_threshold=20):
    labs = [srgb_to_lab(c) for c in colors]
    achromatic_indices = []

    for i, lab in enumerate(labs):
        chroma = np.sqrt(lab[1]**2 + lab[2]**2)
        if chroma < chroma_threshold:
            achromatic_indices.append(i)

    return achromatic_indices, len(achromatic_indices)

# Check if hue steps mostly go in one direction when sorted by L*.
# This helps distinguish multi-hue sequential ramps from diverging.
def _is_unidirectional_hue(labs):
    chromatic = [(lab[0], lab) for lab in labs if np.sqrt(lab[1]**2 + lab[2]**2) > 10]
    if len(chromatic) < 3:
        return False
    chromatic.sort(key=lambda x: x[0])  # sort by L*

    hues = [np.degrees(np.arctan2(lab[2], lab[1])) % 360 for _, lab in chromatic]
    hue_steps = []
    for i in range(len(hues) - 1):
        diff = hues[i+1] - hues[i]
        if diff > 180: diff -= 360
        if diff < -180: diff += 360
        hue_steps.append(diff)

    pos = sum(1 for s in hue_steps if s > 5)
    neg = sum(1 for s in hue_steps if s < -5)
    total = pos + neg
    return total > 0 and min(pos, neg) / max(total, 1) < 0.2

# Figure out if a palette is categorical, sequential, or diverging
def classify_palette(colors):
    n = len(colors)
    if n < 2:
        return PaletteClassification("categorical", 0.5, {"reason": "single color"})

    lightness = _lightness_values(colors)
    mono, direction = _is_monotonic(lightness)
    is_v, v_mid = _detect_v_shape(lightness)
    hue_div = _hue_diversity(colors)

    L_range = np.max(lightness) - np.min(lightness)
    has_clusters, cluster_info = _detect_hue_clusters(colors)
    achromatic_indices, n_achromatic = _find_achromatic_colors(colors)

    details = {
        "n_colors": n,
        "lightness_values": lightness.tolist(),
        "lightness_range": L_range,
        "is_monotonic": mono,
        "monotonic_direction": direction,
        "is_v_shaped": is_v,
        "v_midpoint_index": v_mid,
        "hue_diversity_deg": hue_div,
        "has_hue_clusters": has_clusters,
        "n_achromatic": n_achromatic,
    }
    if has_clusters:
        details["cluster_info"] = cluster_info

    # compute chroma stats
    labs = [srgb_to_lab(c) for c in colors]
    chroma_values = [np.sqrt(lab[1]**2 + lab[2]**2) for lab in labs]
    avg_chroma = np.mean(chroma_values)

    details["avg_chroma"] = avg_chroma

    # single-hue palettes should never be called diverging, even if their
    # desaturated ends look "achromatic" and trigger false cluster signals
    is_single_hue = hue_div < 25

    # two hue clusters plus neutral colors usually means diverging
    # (think election maps, temperature maps)
    if has_clusters and n_achromatic >= 1 and not is_single_hue:
        # Bimodal diverging short-circuit: two clusters with a large empty arc
        # between them (>=200 deg), balanced sizes, and a hue progression that
        # is NOT unidirectional-with-L*. This catches saturated red/blue and
        # green/red diverging palettes whose high chroma would otherwise fall
        # into the sequential override below. A true multi-hue sequential ramp
        # (e.g. viridis, YlGnBu) fills more of the wheel so max_gap stays small.
        max_gap = cluster_info.get("max_gap", 0)
        c1_size = cluster_info.get("cluster1_size", 0)
        c2_size = cluster_info.get("cluster2_size", 0)
        cluster_balance = min(c1_size, c2_size) / max(c1_size, c2_size, 1)
        if (max_gap >= 200
                and cluster_balance >= 0.3
                and hue_div >= 40
                and not _is_unidirectional_hue(labs)):
            details["rule"] = "0: bimodal_diverging (balanced + large_hue_gap)"
            return PaletteClassification("diverging", 0.90, details)

        if avg_chroma > 35 and hue_div > 50:
            # large L* range in a big palette = multi-hue sequential ramp (e.g. YlGnBu)
            # n >= 10 avoids false positives on 8-9 color categorical palettes
            if L_range > 50 and n >= 10:
                details["rule"] = "0_override: large_L_range_sequential"
                return PaletteClassification("sequential", 0.75, details)
            # but if everything is very saturated and spread out, it's categorical
            details["rule"] = "0_override: high_chroma_categorical"
            return PaletteClassification("categorical", 0.85, details)
        achromatic_ratio = n_achromatic / n
        if achromatic_ratio < 0.25 and avg_chroma > 30:
            # few neutrals + high chroma: check if clusters are balanced
            # balanced = diverging, lopsided = probably categorical
            c1 = cluster_info.get("cluster1_size", 0)
            c2 = cluster_info.get("cluster2_size", 0)
            balance = min(c1, c2) / max(c1, c2, 1)
            if balance < 0.3:
                details["rule"] = "0_override: few_achromatic_high_chroma"
                return PaletteClassification("categorical", 0.80, details)
        if _is_unidirectional_hue(labs):
            details["rule"] = "0_override: unidirectional_hue_sequential"
            return PaletteClassification("sequential", 0.80, details)
        # large palette with narrow hue arc: continuous multi-hue gradient
        # (e.g. red->orange->yellow->green), not two distinct diverging arms
        if n > 50 and hue_div < 40:
            details["rule"] = "0_override: large_palette_narrow_hue_sequential"
            return PaletteClassification("sequential", 0.80, details)
        details["rule"] = "0: hue_clusters + achromatic"
        return PaletteClassification("diverging", 0.90, details)

    # two hue clusters in a large palette is usually diverging
    if has_clusters and n >= 8 and not is_single_hue:
        if avg_chroma > 35 and hue_div > 50:
            if L_range > 50 and n >= 10:
                details["rule"] = "0.5_override: large_L_range_sequential"
                return PaletteClassification("sequential", 0.75, details)
            details["rule"] = "0.5_override: high_chroma_categorical"
            return PaletteClassification("categorical", 0.85, details)
        # only if clusters are reasonably balanced
        c1_size = cluster_info.get("cluster1_size", 0)
        c2_size = cluster_info.get("cluster2_size", 0)
        ratio = min(c1_size, c2_size) / max(c1_size, c2_size, 1)
        if ratio > 0.3:
            if _is_unidirectional_hue(labs):
                details["rule"] = "0.5_override: unidirectional_hue_sequential"
                return PaletteClassification("sequential", 0.80, details)
            details["rule"] = "0.5: balanced_hue_clusters"
            return PaletteClassification("diverging", 0.85, details)

    # big palettes without clusters are almost always sequential --
    # nobody can tell 10+ categories apart anyway
    if n > 10 and not has_clusters:
        if L_range > 20:
            details["rule"] = "1: large_palette_sequential"
            return PaletteClassification("sequential", 0.85, details)
        else:
            details["rule"] = "1b: large_palette_low_range"
            return PaletteClassification("categorical", 0.70, details)

    # single hue with varying lightness is sequential (e.g. light blue to dark blue)
    if hue_div < 25 and L_range > 20 and avg_chroma > 10:
        if mono:
            details["rule"] = "2: single_hue_sequential_monotonic"
            return PaletteClassification("sequential", 0.90, details)
        else:
            details["rule"] = "2b: single_hue_sequential_unordered"
            return PaletteClassification("sequential", 0.75, details)

    # V-shape with a neutral midpoint is classic diverging
    if is_v and n >= 5:
        mid_lab = labs[v_mid]
        mid_chroma = np.sqrt(mid_lab[1]**2 + mid_lab[2]**2)
        details["mid_chroma"] = mid_chroma

        # neutral midpoint = diverging
        if mid_chroma < 20:
            details["rule"] = "3: v_shape_neutral_midpoint"
            return PaletteClassification("diverging", 0.85, details)

        # chromatic midpoint: check hue transitions to distinguish from sequential
        chromatic_labs = [(i, lab) for i, lab in enumerate(labs)
                         if np.sqrt(lab[1]**2 + lab[2]**2) > 10]
        if len(chromatic_labs) >= 3:
            hues_ordered = [np.degrees(np.arctan2(lab[2], lab[1])) % 360
                           for _, lab in chromatic_labs]
            hue_steps = []
            for i in range(len(hues_ordered) - 1):
                diff = hues_ordered[i+1] - hues_ordered[i]
                if diff > 180: diff -= 360
                if diff < -180: diff += 360
                hue_steps.append(diff)

            if hue_steps:
                positive_steps = sum(1 for s in hue_steps if s > 5)
                negative_steps = sum(1 for s in hue_steps if s < -5)
                total_directed = positive_steps + negative_steps
                details["hue_steps_direction"] = f"pos={positive_steps} neg={negative_steps}"

                if total_directed > 0 and min(positive_steps, negative_steps) / max(total_directed, 1) < 0.2:
                    details["rule"] = "3a: multi_hue_sequential_v_shape"
                    return PaletteClassification("sequential", 0.75, details)
                else:
                    details["rule"] = "3b: diverging_chromatic_midpoint"
                    return PaletteClassification("diverging", 0.75, details)

        details["rule"] = "3c: v_shape_fallback_sequential"
        return PaletteClassification("sequential", 0.65, details)

    # small V-shape palettes
    if is_v and 3 <= n < 5 and hue_div < 80:
        details["rule"] = "4: small_v_shape"
        return PaletteClassification("diverging", 0.65, details)

    # monotonic lightness is a strong sequential signal
    if mono and L_range > 15:
        if hue_div < 40 and avg_chroma < 60:
            details["rule"] = "5: monotonic_low_hue_div"
            return PaletteClassification("sequential", 0.80, details)
        elif hue_div < 60 and n >= 3:
            hues = [np.degrees(np.arctan2(lab[2], lab[1])) % 360 for lab in labs]
            hue_diffs = [abs(hues[i+1] - hues[i]) for i in range(len(hues)-1)]
            hue_diffs = [min(d, 360-d) for d in hue_diffs]
            max_hue_jump = max(hue_diffs) if hue_diffs else 0
            if max_hue_jump < 60:
                details["rule"] = "5a: monotonic_smooth_hue"
                return PaletteClassification("sequential", 0.65, details)
            else:
                details["rule"] = "5b: monotonic_large_hue_jumps"
                return PaletteClassification("categorical", 0.75, details)
        elif n >= 5:
            hues = [np.degrees(np.arctan2(lab[2], lab[1])) % 360 for lab in labs]
            hue_diffs = [abs(hues[i+1] - hues[i]) for i in range(len(hues)-1)]
            hue_diffs = [min(d, 360-d) for d in hue_diffs]
            max_hue_jump = max(hue_diffs) if hue_diffs else 0
            if max_hue_jump < 90:
                details["rule"] = "5c: monotonic_many_colors_smooth"
                return PaletteClassification("sequential", 0.60, details)
            else:
                details["rule"] = "5d: monotonic_many_colors_jumpy"
                return PaletteClassification("categorical", 0.70, details)
        else:
            details["rule"] = "5e: monotonic_fallback_categorical"
            return PaletteClassification("categorical", 0.70, details)

    # multi-hue sequential ramps with unordered SVG colors:
    # high hue diversity but hue transitions are unidirectional when sorted by L*
    if L_range > 20 and not is_single_hue and _is_unidirectional_hue(labs):
        details["rule"] = "5.5: multi_hue_sequential"
        return PaletteClassification("sequential", 0.75, details)

    # lots of different hues in a small palette = categorical
    if hue_div > 50 and avg_chroma > 20 and n >= 2 and n <= 10:
        details["rule"] = "6: high_hue_diversity_categorical"
        return PaletteClassification("categorical", 0.85, details)

    # weak sequential signal
    if mono and 5 < L_range <= 15 and hue_div < 40:
        details["rule"] = "7: weak_sequential"
        return PaletteClassification("sequential", 0.50, details)

    # fallback: non-monotonic or moderate hue diversity = categorical
    if hue_div > 30 or not mono:
        details["rule"] = "8: categorical_fallback"
        return PaletteClassification("categorical", 0.75, details)

    # nothing else matched, default to categorical
    details["rule"] = "9: default_categorical"
    return PaletteClassification("categorical", 0.60, details)
