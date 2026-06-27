# Recoloring engine. Generates CVD-accessible replacement palettes for each
# palette type while trying to stay perceptually close to the original.

import numpy as np
from color_science import (
    srgb_to_lab, lab_to_srgb, ciede2000, rgb_to_hex, parse_color,
    simulate_cvd, simulate_cvd_lab, pairwise_de_under_cvd,
    get_lightness_under_cvd
)
from invariant_tests import (
    test_categorical_pairwise, CATEGORICAL_MIN_DE,
    adaptive_min_step, adaptive_uniformity_cv
)


# pre-built CVD-safe categorical palettes
SAFE_CATEGORICAL_PALETTES = {
    "okabe_ito": [
        (0, 114, 178),    # blue
        (230, 159, 0),    # orange
        (0, 158, 115),    # bluish green
        (204, 121, 167),  # reddish purple
        (86, 180, 233),   # sky blue
        (213, 94, 0),     # vermillion
        (240, 228, 66),   # yellow
        (0, 0, 0),        # black
    ],
    "ibm": [
        (100, 143, 255),  # ultramarine
        (120, 94, 240),   # indigo
        (220, 38, 127),   # magenta
        (254, 97, 0),     # orange
        (255, 176, 0),    # gold
    ],
    "wong": [
        (0, 0, 0),        # black
        (230, 159, 0),    # orange
        (86, 180, 233),   # sky blue
        (0, 158, 115),    # bluish green
        (240, 228, 66),   # yellow
        (0, 114, 178),    # blue
        (213, 94, 0),     # vermillion
        (204, 121, 167),  # reddish purple
    ],
}

# single-hue sequential ramps that stay monotonic under CVD
SAFE_SEQUENTIAL_ANCHORS = {
    "blue": [(247, 251, 255), (198, 219, 239), (158, 202, 225),
             (107, 174, 214), (66, 146, 198), (33, 113, 181),
             (8, 81, 156), (8, 48, 107)],
    "purple": [(252, 251, 253), (218, 218, 235), (188, 189, 220),
               (158, 154, 200), (128, 125, 186), (106, 81, 163),
               (84, 39, 143), (63, 0, 125)],
    "orange": [(255, 245, 235), (254, 230, 206), (253, 208, 162),
               (253, 174, 107), (253, 141, 60), (241, 105, 19),
               (217, 72, 1), (166, 54, 3)],
}

# safe diverging endpoint pairs. Midpoints use a light gray (L* ~92) rather
# than pure white so the midpoint region stays visible on white page
# backgrounds - a pure-white midpoint blends into the page and makes
# near-zero data points invisible in rendered SVGs.
SAFE_DIVERGING_ENDPOINTS = [
    {"left": (8, 48, 107), "mid": (232, 232, 232), "right": (166, 54, 3)},    # blue / orange
    {"left": (63, 0, 125), "mid": (232, 232, 232), "right": (0, 68, 27)},     # purple / green
    {"left": (5, 48, 97), "mid": (232, 232, 232), "right": (103, 0, 31)},     # blue / red-brown
]


# Find the safe palette perceptually closest to the original.
def _find_closest_safe_palette(original_colors, safe_palettes, cvd_type):
    n = len(original_colors)
    best_palette = None
    best_distance = float('inf')

    for name, palette in safe_palettes.items():
        if len(palette) < n:
            continue

        # greedy match: pair each original color to the nearest unused safe color
        subset = palette[:n]
        orig_labs = [srgb_to_lab(c) for c in original_colors]
        safe_labs = [srgb_to_lab(c) for c in subset]

        used = set()
        total_dist = 0
        mapping = {}

        for i, orig_lab in enumerate(orig_labs):
            best_j = -1
            best_d = float('inf')
            for j, safe_lab in enumerate(safe_labs):
                if j in used:
                    continue
                d = ciede2000(orig_lab, safe_lab)
                if d < best_d:
                    best_d = d
                    best_j = j
            if best_j >= 0:
                used.add(best_j)
                total_dist += best_d
                mapping[i] = best_j

        if total_dist < best_distance:
            best_distance = total_dist
            best_palette = (name, subset, mapping)

    return best_palette


# Generate a CVD-accessible categorical palette. Tries known safe palettes
# first, falls back to perturbation-based optimization on failure.
def recolor_categorical(colors, cvd_type="deutan", attempt=0):
    n = len(colors)

    # rotate palette order on retries so we try different ones first
    palette_names = list(SAFE_CATEGORICAL_PALETTES.keys())
    if attempt > 0:
        palette_names = palette_names[attempt % len(palette_names):] + \
                        palette_names[:attempt % len(palette_names)]

    palettes_to_try = {name: SAFE_CATEGORICAL_PALETTES[name] for name in palette_names}

    # first try: use a known safe palette
    result = _find_closest_safe_palette(colors, palettes_to_try, cvd_type)
    if result:
        _, safe_subset, mapping = result
        ordered_safe = [None] * n
        for orig_idx, safe_idx in mapping.items():
            ordered_safe[orig_idx] = safe_subset[safe_idx]

        # verify it actually passes
        test_check = test_categorical_pairwise(ordered_safe, cvd_type)
        if test_check.passed:
            color_map = {}
            for i, orig in enumerate(colors):
                color_map[rgb_to_hex(orig)] = rgb_to_hex(ordered_safe[i])
            return color_map

    # fallback: perturbation-based optimization in Lab space
    step_scale = 3.0 + attempt * 2.0
    current = [srgb_to_lab(c) for c in colors]
    best_colors = list(colors)

    for _ in range(200):
        rgb_current = [lab_to_srgb(lab) for lab in current]
        de_mat = pairwise_de_under_cvd(rgb_current, cvd_type)

        min_de = float('inf')
        worst_i, worst_j = 0, 1
        for i in range(n):
            for j in range(i + 1, n):
                if de_mat[i, j] < min_de:
                    min_de = de_mat[i, j]
                    worst_i, worst_j = i, j

        if min_de >= CATEGORICAL_MIN_DE:
            best_colors = rgb_current
            break

        # push the two closest colors apart in Lab
        lab_i = current[worst_i]
        lab_j = current[worst_j]

        direction = lab_i - lab_j
        norm = np.linalg.norm(direction[1:])  # a*, b* only
        if norm < 0.01:
            direction = np.array([0, 5, 5])  # arbitrary push
            norm = np.linalg.norm(direction[1:])

        step = step_scale * direction / (norm + 1e-6)
        current[worst_i] = current[worst_i] + step * 0.5
        current[worst_j] = current[worst_j] - step * 0.5

        # clamp L*
        for k in range(n):
            current[k][0] = np.clip(current[k][0], 10, 95)

        best_colors = [lab_to_srgb(lab) for lab in current]

    # build the color mapping
    color_map = {}
    for i, orig in enumerate(colors):
        new = best_colors[i]
        color_map[rgb_to_hex(orig)] = rgb_to_hex(new)
    return color_map


# Group colors into hue neighborhoods on the hue circle.  Returns a list
# of cluster ids (one per input color) or None when the palette has no
# meaningful hue split - meaning a smooth single-hue ramp, a ramp with a
# continuous hue gradient (e.g. viridis-like), or a mostly-achromatic
# palette.  Callers should fall back to PCA sorting in the None case.
#
# This is used for the "multi-hue sequential without a legend" edge case:
# when a palette has distinct hue neighborhoods but no legend to anchor
# ordering, we cluster by hue and sort by L* within each cluster so that
# the within-cluster luminance ordering is preserved through recoloring.
def _detect_hue_neighborhoods(labs, hue_gap_threshold=45.0, min_chroma=8.0):
    n = len(labs)
    if n < 3:
        return None

    hues = []
    chromas = []
    for lab in labs:
        a, b = lab[1], lab[2]
        chromas.append(np.sqrt(a * a + b * b))
        hues.append(np.degrees(np.arctan2(b, a)) % 360)

    # If most colors are achromatic, hue clustering is meaningless.
    chromatic_count = sum(1 for c in chromas if c >= min_chroma)
    if chromatic_count < max(3, n // 2):
        return None

    sorted_idx = sorted(range(n), key=lambda i: hues[i])

    # Walk sorted hues; start a new cluster on each gap > threshold.
    clusters = []
    current = [sorted_idx[0]]
    for k in range(1, n):
        gap = (hues[sorted_idx[k]] - hues[sorted_idx[k - 1]]) % 360
        if gap > hue_gap_threshold:
            clusters.append(current)
            current = [sorted_idx[k]]
        else:
            current.append(sorted_idx[k])
    clusters.append(current)

    # Handle wrap-around: if first and last clusters are close on the
    # hue circle, they belong together.
    if len(clusters) >= 2:
        wrap_gap = (hues[sorted_idx[0]] - hues[sorted_idx[-1]]) % 360
        if wrap_gap <= hue_gap_threshold:
            clusters[0] = clusters[-1] + clusters[0]
            clusters.pop()

    if len(clusters) < 2:
        return None

    cluster_of = [0] * n
    for cid, indices in enumerate(clusters):
        for idx in indices:
            cluster_of[idx] = cid
    return cluster_of


# Produce sort indices by hue-neighborhood ordering: darkest-mean cluster
# first, then L*-ascending within each cluster.  Returns (indices, cluster_of)
# or None when there are no meaningful hue neighborhoods.
def _hue_cluster_sort_indices(labs):
    cluster_of = _detect_hue_neighborhoods(labs)
    if cluster_of is None:
        return None

    n = len(labs)
    # Group indices by cluster
    by_cluster = {}
    for i, cid in enumerate(cluster_of):
        by_cluster.setdefault(cid, []).append(i)

    # Order clusters by mean L* ascending so the darkest neighborhood
    # maps to the darkest end of the new ramp.
    cluster_ids_sorted = sorted(
        by_cluster.keys(),
        key=lambda cid: np.mean([labs[i][0] for i in by_cluster[cid]])
    )

    sorted_indices = []
    for cid in cluster_ids_sorted:
        sorted_indices.extend(sorted(by_cluster[cid], key=lambda i: labs[i][0]))

    if len(sorted_indices) != n:
        return None
    return sorted_indices, cluster_of


# Verify that within each hue cluster, the L* ordering of new colors
# matches the L* ordering of the original colors - i.e. that the
# recoloring did not swap the luminance rank of marks inside a hue
# neighborhood.  Returns True when preserved, False when violated.
def _cluster_L_order_preserved(orig_labs, new_colors_sorted, sorted_indices,
                               cluster_of, tol=0.5):
    if cluster_of is None:
        return True

    new_labs = [srgb_to_lab(c) for c in new_colors_sorted]
    # Map original idx -> new L* via the rank assignment.
    orig_to_new_L = [0.0] * len(orig_labs)
    for rank, orig_idx in enumerate(sorted_indices):
        orig_to_new_L[orig_idx] = new_labs[rank][0]

    by_cluster = {}
    for i, cid in enumerate(cluster_of):
        by_cluster.setdefault(cid, []).append(i)

    for indices in by_cluster.values():
        if len(indices) < 2:
            continue
        ordered = sorted(indices, key=lambda i: orig_labs[i][0])
        prev = orig_to_new_L[ordered[0]]
        for i in ordered[1:]:
            cur = orig_to_new_L[i]
            if cur < prev - tol:
                return False
            prev = cur
    return True


# Interpolate a Lab ramp to n evenly-spaced steps.
# Sort colors by projection onto the first principal component in Lab space.
# For single-hue ramps this reduces to L*-ordering.  For multi-hue ramps
# (e.g. orange->yellow->green) it captures the hue gradient, which carries
# ordering information when the L* range is narrow.
def _pca_sort_indices(labs):
    n = len(labs)
    if n < 2:
        return list(range(n))

    lab_array = np.array([[lab[0], lab[1], lab[2]] for lab in labs],
                         dtype=np.float64)
    mean = lab_array.mean(axis=0)
    centered = lab_array - mean

    try:
        _, _, Vt = np.linalg.svd(centered, full_matrices=False)
        pc1 = Vt[0]
    except np.linalg.LinAlgError:
        # SVD failed; fall back to L*-sorting
        return sorted(range(n), key=lambda i: labs[i][0])

    projections = centered @ pc1

    # Ensure direction: positive projection means higher L* so that
    # sorted_indices[0] is the "darkest" original color.
    L_values = [lab[0] for lab in labs]
    if len(set(L_values)) > 1:
        corr = np.corrcoef(projections, L_values)[0, 1]
        if corr < 0:
            projections = -projections

    return sorted(range(n), key=lambda i: projections[i])


def _interpolate_ramp(ramp_labs, n):
    new_colors = []
    for i in range(n):
        t = i / max(n - 1, 1)  # 0.0 to 1.0
        pos = t * (len(ramp_labs) - 1)
        idx_lo = int(pos)
        idx_hi = min(idx_lo + 1, len(ramp_labs) - 1)
        frac = pos - idx_lo
        interp_lab = ramp_labs[idx_lo] * (1 - frac) + ramp_labs[idx_hi] * frac
        new_colors.append(lab_to_srgb(interp_lab))
    return new_colors


# Iteratively adjust L* until the palette passes all four sequential
# invariants under CVD. Returns adjusted sRGB colors, dark-to-light.
def _enforce_sequential_under_cvd(new_colors_sorted, cvd_type):
    from color_science import simulate_cvd, get_lightness_under_cvd

    n = len(new_colors_sorted)
    if n < 2:
        return new_colors_sorted

    current_labs = np.array([srgb_to_lab(c) for c in new_colors_sorted], dtype=np.float64)

    step_threshold = adaptive_min_step(n)
    cv_threshold = adaptive_uniformity_cv(n)

    for _iteration in range(10):
        current_rgb = [lab_to_srgb(lab) for lab in current_labs]
        cvd_L = np.array(get_lightness_under_cvd(current_rgb, cvd_type))

        # test monotonicity
        diffs = np.diff(cvd_L)
        is_monotonic = np.all(diffs > -0.5)

        # test uniformity
        abs_diffs = np.abs(diffs)
        mean_step = np.mean(abs_diffs)
        cv = np.std(abs_diffs) / (mean_step + 1e-10)
        is_uniform = cv < cv_threshold

        # test direction
        direction_ok = cvd_L[-1] > cvd_L[0]

        # test min step size
        min_step = np.min(abs_diffs) if len(abs_diffs) > 0 else 0
        step_ok = min_step >= step_threshold

        if is_monotonic and is_uniform and direction_ok and step_ok:
            return current_rgb

        # apply targeted repairs for whichever test failed
        if not direction_ok:
            current_labs = current_labs[::-1]
            continue

        if not is_monotonic:
            # scale push to palette size to avoid cascading into the L* ceiling
            push = max(0.5, min(2.0, 80.0 / n))
            for i in range(1, n):
                if cvd_L[i] <= cvd_L[i-1] + 0.5:
                    current_labs[i][0] += (cvd_L[i-1] - cvd_L[i]) + push
                    current_labs[i][0] = min(current_labs[i][0], 98)
            continue

        if not step_ok:
            # for large palettes, redistribute evenly instead of local pushes
            # which cascade and cause clamping collisions
            if n > 30:
                L_lo = current_labs[0][0]
                L_hi = current_labs[-1][0]
                for i in range(n):
                    current_labs[i][0] = L_lo + (L_hi - L_lo) * i / max(n - 1, 1)
            else:
                for i in range(len(abs_diffs)):
                    if abs_diffs[i] < step_threshold:
                        adjust = (step_threshold - abs_diffs[i]) / 2 + 0.5
                        current_labs[i][0] = max(current_labs[i][0] - adjust, 5)
                        current_labs[i+1][0] = min(current_labs[i+1][0] + adjust, 98)
            continue

        if not is_uniform:
            L_lo = current_labs[0][0]
            L_hi = current_labs[-1][0]
            for i in range(n):
                target_L = L_lo + (L_hi - L_lo) * i / max(n - 1, 1)
                current_labs[i][0] = target_L
            continue

    # best effort
    return [lab_to_srgb(lab) for lab in current_labs]


# Generate a CVD-accessible sequential palette. Maps by lightness rank
# so the data ordering is preserved. When the original legend has
# non-monotonic L*, positional_order overrides L*-rank mapping to avoid
# shuffling colors that the viewer interprets by position, not lightness.
def recolor_sequential(colors, cvd_type="deutan", attempt=0, positional_order=None):
    n = len(colors)
    labs = [srgb_to_lab(c) for c in colors]
    L_values = [lab[0] for lab in labs]

    # Choose an ordering strategy.  Three paths, in priority order:
    #   1. positional_order (from a non-monotonic legend) - highest signal.
    #   2. hue-neighborhood sort (multi-hue palette, no legend) - preserves
    #      within-cluster L* ordering.
    #   3. PCA on Lab - single-hue ramps or continuous multi-hue gradients.
    # Path 2 is a new path for the "no legend + multi-hue" edge case; it
    # falls through to path 3 when there are no distinct hue clusters or
    # when the within-cluster L* invariant would be violated.
    cluster_of = None
    if positional_order is not None and len(positional_order) == n:
        pos_Ls = [L_values[i] for i in positional_order]
        first_half_mean = np.mean(pos_Ls[:max(n // 2, 1)])
        second_half_mean = np.mean(pos_Ls[max(n // 2, 1):]) if n > 1 else first_half_mean
        if first_half_mean > second_half_mean:
            sorted_indices = list(reversed(positional_order))
        else:
            sorted_indices = list(positional_order)
    else:
        hue_result = _hue_cluster_sort_indices(labs)
        if hue_result is not None:
            sorted_indices, cluster_of = hue_result
        else:
            sorted_indices = _pca_sort_indices(labs)

    # pick a ramp: infer from original on first try, cycle on retries
    ramp_names = list(SAFE_SEQUENTIAL_ANCHORS.keys())

    if attempt == 0:
        # infer from average chromaticity
        avg_a = np.mean([lab[1] for lab in labs])
        avg_b = np.mean([lab[2] for lab in labs])

        best_ramp_name = "blue"
        if avg_b > 20 and avg_a > 0:
            best_ramp_name = "orange"
        elif avg_a < -10:
            best_ramp_name = "purple"
    else:
        best_ramp_name = ramp_names[attempt % len(ramp_names)]

    safe_ramp = SAFE_SEQUENTIAL_ANCHORS[best_ramp_name]

    # reverse so index 0 = darkest, matching our L*-ascending sort
    ramp_labs = [np.array(srgb_to_lab(c), dtype=np.float64) for c in reversed(safe_ramp)]

    # interpolate to get exactly n steps
    new_colors_sorted = _interpolate_ramp(ramp_labs, n)

    # verify and fix invariants under CVD
    new_colors_sorted = _enforce_sequential_under_cvd(new_colors_sorted, cvd_type)

    # If we used the hue-cluster path, verify within-cluster L* ordering
    # survived CVD enforcement.  On violation, fall back to PCA sort.
    if cluster_of is not None and not _cluster_L_order_preserved(
            labs, new_colors_sorted, sorted_indices, cluster_of):
        sorted_indices = _pca_sort_indices(labs)

    # map by rank: sorted_indices[0] gets darkest new, etc.
    color_map = {}
    for rank, orig_idx in enumerate(sorted_indices):
        color_map[rgb_to_hex(colors[orig_idx])] = rgb_to_hex(new_colors_sorted[rank])
    return color_map


# Generate a CVD-accessible diverging palette by interpolating through
# a neutral midpoint between two safe endpoint hues.
# Preserves the original palette's arm asymmetry: if the original has
# unequal L* ranges on each side, the remapped version matches that ratio.
def recolor_diverging(colors, cvd_type="deutan", attempt=0):
    n = len(colors)
    orig_labs = [srgb_to_lab(c) for c in colors]
    L_values = [lab[0] for lab in orig_labs]
    L_arr = np.array(L_values)

    # --- 1. Choose the safe scheme closest to the original ---
    if attempt == 0:
        orig_left_lab = srgb_to_lab(colors[0])
        orig_right_lab = srgb_to_lab(colors[-1])
        best_scheme = None
        best_dist = float('inf')
        for scheme in SAFE_DIVERGING_ENDPOINTS:
            sl = srgb_to_lab(scheme["left"])
            sr = srgb_to_lab(scheme["right"])
            d1 = ciede2000(orig_left_lab, sl) + ciede2000(orig_right_lab, sr)
            d2 = ciede2000(orig_left_lab, sr) + ciede2000(orig_right_lab, sl)
            if min(d1, d2) < best_dist:
                best_dist = min(d1, d2)
                if d2 < d1:
                    best_scheme = {"left": scheme["right"], "mid": scheme["mid"], "right": scheme["left"]}
                else:
                    best_scheme = scheme
        if best_scheme is None:
            best_scheme = SAFE_DIVERGING_ENDPOINTS[0]
    else:
        best_scheme = SAFE_DIVERGING_ENDPOINTS[attempt % len(SAFE_DIVERGING_ENDPOINTS)]

    left_lab = np.array(srgb_to_lab(best_scheme["left"]), dtype=np.float64)
    mid_lab = np.array(srgb_to_lab(best_scheme["mid"]), dtype=np.float64)
    right_lab = np.array(srgb_to_lab(best_scheme["right"]), dtype=np.float64)

    # --- 2. Split palette into two arms + neutrals by HUE CLUSTER ---
    # Diverging palettes have two chromatic arms with a neutral midpoint,
    # but SVG authoring tools rarely serialize colors in positional order.
    # Splitting at the L* argmax (as earlier versions did) breaks when the
    # palette is interleaved - it would dump most colors into one arm.
    # Instead we split by the biggest gap in hue angle among chromatic
    # colors, which matches the classifier's diverging detection logic.
    chromas = [np.sqrt(lab[1]**2 + lab[2]**2) for lab in orig_labs]
    hues_deg = [np.degrees(np.arctan2(lab[2], lab[1])) % 360 for lab in orig_labs]

    CHROMA_ARM_THRESHOLD = 15  # below this a color is treated as neutral / no-data
    chromatic_idx = [i for i in range(n) if chromas[i] > CHROMA_ARM_THRESHOLD]
    neutral_idx = [i for i in range(n) if chromas[i] <= CHROMA_ARM_THRESHOLD]

    if len(chromatic_idx) >= 2:
        # Find the biggest empty arc in the hue ring; the two arms lie on
        # either side of it.
        sorted_chromatic = sorted(chromatic_idx, key=lambda i: hues_deg[i])
        m_ = len(sorted_chromatic)
        max_gap = 0.0
        split_k = 0
        for k in range(m_):
            h_cur = hues_deg[sorted_chromatic[k]]
            h_nxt = hues_deg[sorted_chromatic[(k + 1) % m_]]
            gap = (h_nxt - h_cur) % 360
            if gap > max_gap:
                max_gap = gap
                split_k = k
        cluster_a_idx = [sorted_chromatic[(split_k + 1 + j) % m_] for j in range(m_)]
        # Now walk the cluster_a list and split it at the SECOND biggest gap
        # so the two arms separate cleanly on the hue ring.
        second_gap = 0.0
        second_k = 0
        for k in range(len(cluster_a_idx) - 1):
            h_cur = hues_deg[cluster_a_idx[k]]
            h_nxt = hues_deg[cluster_a_idx[k + 1]]
            gap = (h_nxt - h_cur) % 360
            if gap > second_gap:
                second_gap = gap
                second_k = k
        cluster_1 = cluster_a_idx[: second_k + 1]
        cluster_2 = cluster_a_idx[second_k + 1:]
    else:
        # Degenerate: too few chromatic colors to split by hue.
        cluster_1 = chromatic_idx
        cluster_2 = []

    # Match each cluster to the correct safe-scheme arm by comparing the
    # mean hue of each cluster to the safe endpoint hues.
    left_ep_hue = np.arctan2(left_lab[2], left_lab[1])
    right_ep_hue = np.arctan2(right_lab[2], right_lab[1])

    def _mean_hue(indices):
        if not indices:
            return 0.0
        sin_sum = sum(np.sin(np.radians(hues_deg[i])) for i in indices)
        cos_sum = sum(np.cos(np.radians(hues_deg[i])) for i in indices)
        return np.arctan2(sin_sum, cos_sum)

    def _circular_dist(a, b):
        d = abs(a - b)
        if d > np.pi:
            d = 2 * np.pi - d
        return d

    hue_1 = _mean_hue(cluster_1)
    # If cluster 1 is closer to the left endpoint hue, it becomes the left arm.
    if _circular_dist(hue_1, left_ep_hue) <= _circular_dist(hue_1, right_ep_hue):
        left_indices = list(cluster_1)
        right_indices = list(cluster_2)
    else:
        left_indices = list(cluster_2)
        right_indices = list(cluster_1)

    # Neutrals ride with whichever arm has the closest L*; this keeps them
    # near the midpoint region of the new palette instead of scrambling the
    # ordered diverging progression.
    for idx in neutral_idx:
        left_Lgap = min((abs(L_values[idx] - L_values[i]) for i in left_indices),
                        default=float('inf'))
        right_Lgap = min((abs(L_values[idx] - L_values[i]) for i in right_indices),
                         default=float('inf'))
        if left_Lgap <= right_Lgap:
            left_indices.append(idx)
        else:
            right_indices.append(idx)

    # --- 3. Set arm sizes from actual assignment ---
    n_left = len(left_indices)   # including midpoint
    n_right = len(right_indices) + 1  # +1 because midpoint is shared

    # Compute arm L* ranges from actual arm members
    left_Ls = sorted([L_values[i] for i in left_indices])
    right_Ls = sorted([L_values[i] for i in right_indices])
    mid_L = max(np.max(L_arr), mid_lab[0])  # midpoint is the lightest

    orig_left_range = abs(left_Ls[0] - mid_L) if left_Ls else 40
    orig_right_range = abs(right_Ls[0] - mid_L) if right_Ls else 40

    # --- 4. Generate new colors with correct arm sizes ---
    safe_left_range = abs(left_lab[0] - mid_lab[0])
    safe_right_range = abs(right_lab[0] - mid_lab[0])
    total_safe = safe_left_range + safe_right_range
    total_orig = orig_left_range + orig_right_range

    if total_orig > 1.0 and total_safe > 1.0:
        left_weight = orig_left_range / total_orig
        right_weight = orig_right_range / total_orig
        target_left_range = total_safe * left_weight
        target_right_range = total_safe * right_weight
    else:
        target_left_range = safe_left_range
        target_right_range = safe_right_range

    left_sign = np.sign(left_lab[0] - mid_lab[0])
    right_sign = np.sign(right_lab[0] - mid_lab[0])
    if left_sign == 0: left_sign = -1
    if right_sign == 0: right_sign = -1

    left_arm_colors = []
    right_arm_colors = []

    # left arm: endpoint to midpoint
    for i in range(n_left):
        t = i / max(n_left - 1, 1)
        ab = left_lab[1:] * (1 - t) + mid_lab[1:] * t
        L = mid_lab[0] + left_sign * target_left_range * (1 - t)
        L = np.clip(L, 5, 98)
        left_arm_colors.append(lab_to_srgb(np.array([L, ab[0], ab[1]])))

    # right arm: midpoint to endpoint (skip midpoint, already in left)
    for i in range(1, n_right):
        t = i / max(n_right - 1, 1)
        ab = mid_lab[1:] * (1 - t) + right_lab[1:] * t
        L = mid_lab[0] + right_sign * target_right_range * t
        L = np.clip(L, 5, 98)
        right_arm_colors.append(lab_to_srgb(np.array([L, ab[0], ab[1]])))

    # enforce sequential invariants on each arm under CVD
    if len(left_arm_colors) >= 2:
        left_arm_colors = _enforce_sequential_under_cvd(left_arm_colors, cvd_type)
    if len(right_arm_colors) >= 2:
        right_arm_colors = _enforce_sequential_under_cvd(right_arm_colors, cvd_type)

    new_colors = left_arm_colors + right_arm_colors

    # --- 5. Map by L*-rank within each arm ---
    left_indices.sort(key=lambda i: L_values[i])
    right_indices.sort(key=lambda i: L_values[i])

    left_new = sorted(range(n_left), key=lambda j: srgb_to_lab(new_colors[j])[0])
    right_new = sorted(range(n_left, len(new_colors)),
                       key=lambda j: srgb_to_lab(new_colors[j])[0])

    color_map = {}
    for rank in range(min(len(left_indices), len(left_new))):
        oi = left_indices[rank]
        ni = left_new[rank]
        color_map[rgb_to_hex(colors[oi])] = rgb_to_hex(new_colors[ni])

    for rank in range(min(len(right_indices), len(right_new))):
        oi = right_indices[rank]
        ni = right_new[rank]
        hex_key = rgb_to_hex(colors[oi])
        if hex_key not in color_map:
            color_map[hex_key] = rgb_to_hex(new_colors[ni])

    return color_map


# Dispatch to the right recoloring strategy for the palette type.
def recolor_palette(colors, palette_type, cvd_type="deutan", test_results=None,
                    attempt=0, positional_order=None):
    if palette_type == "categorical":
        return recolor_categorical(colors, cvd_type, attempt=attempt)
    elif palette_type == "sequential":
        return recolor_sequential(colors, cvd_type, attempt=attempt,
                                  positional_order=positional_order)
    elif palette_type == "diverging":
        return recolor_diverging(colors, cvd_type, attempt=attempt)
    else:
        raise ValueError(f"Unknown palette type: {palette_type}")
