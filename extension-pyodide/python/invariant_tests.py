# Invariant tests for each palette type (DR3). Categorical gets pairwise delta E,
# sequential gets monotonicity/uniformity/direction/min-step, and diverging
# gets midpoint/endpoint/arm checks.

import numpy as np
from typing import List, Tuple, Optional, Dict
from color_science import (
    srgb_to_lab, ciede2000, simulate_cvd_lab,
    pairwise_de_under_cvd, get_lightness_under_cvd
)


CATEGORICAL_MIN_DE = 8.0           # pairwise distinguishability
DIVERGING_ENDPOINT_MIN_DE = 10.0   # endpoint separation
SEQUENTIAL_MIN_DL = 3.0            # adjacent step size (for small palettes)
MONOTONICITY_TOLERANCE = 0.5       # floating point tolerance


# Adaptive thresholds for large palettes. A single-hue CVD-safe ramp
# spans ~55 L* units under simulation (not 90, because saturation costs
# lightness range). We set min-step to 60% of the theoretical max per
# step. For very large palettes (>150) sRGB quantization makes zero-step
# pairs unavoidable, so the floor drops to 0 and we rely on monotonicity
# and uniformity as the meaningful checks.
def adaptive_min_step(n):
    if n <= 2:
        return SEQUENTIAL_MIN_DL
    if n > 150:
        # sRGB quantization makes zero-step pairs unavoidable;
        # rely on monotonicity and uniformity as the meaningful checks.
        return 0.0
    ideal = 55.0 / (n - 1)
    return max(0.02, min(SEQUENTIAL_MIN_DL, ideal * 0.6))


# Uniformity CV threshold relaxes for large palettes where tiny absolute
# variations in L* produce high CV even when the ramp looks smooth.
def adaptive_uniformity_cv(n):
    if n <= 15:
        return 0.4
    return min(0.8, 0.4 + 0.01 * (n - 15))


# Result of one invariant test (pass/fail with metric details).
class TestResult:
    def __init__(
        self,
        test_name: str,
        passed: bool,
        metric_value: float,
        threshold: float,
        repair_suggestion: Optional[str] = None,
        details: Optional[Dict] = None,
    ):
        self.test_name = test_name
        self.passed = passed
        self.metric_value = metric_value
        self.threshold = threshold
        self.repair_suggestion = repair_suggestion
        self.details = details or {}

    def __repr__(self):
        status = "PASS" if self.passed else "FAIL"
        return (
            f"[{status}] {self.test_name}: "
            f"{self.metric_value:.2f} (threshold: {self.threshold})"
        )


# How a palette can fail under CVD simulation.
class FailureMode:
    PAIRWISE_COLLAPSE = "pairwise_collapse"           # Categorical
    MONOTONICITY_VIOLATION = "monotonicity_violation" # Sequential
    UNIFORMITY_DISTORTION = "uniformity_distortion"   # Sequential
    DIRECTION_REVERSAL = "direction_reversal"         # Sequential
    MIDPOINT_DISAPPEARANCE = "midpoint_disappearance" # Diverging
    MIDPOINT_SHIFT = "midpoint_shift"                  # Diverging
    BIDIRECTIONAL_COLLAPSE = "bidirectional_collapse" # Diverging


# Check that all pairwise delta E values are at least 8 under CVD.
def test_categorical_pairwise(colors: List[Tuple], cvd_type: str = "deutan") -> TestResult:
    if len(colors) < 2:
        return TestResult(
            "Categorical Pairwise ΔE",
            passed=True,
            metric_value=float('inf'),
            threshold=CATEGORICAL_MIN_DE,
            details={"reason": "fewer than 2 colors"},
        )

    de_matrix = pairwise_de_under_cvd(colors, cvd_type)
    n = len(colors)

    # find the worst pair
    min_de = float('inf')
    worst_pair = None

    for i in range(n):
        for j in range(i + 1, n):
            if de_matrix[i, j] < min_de:
                min_de = de_matrix[i, j]
                worst_pair = (i, j)

    passed = min_de >= CATEGORICAL_MIN_DE

    if passed:
        repair = None
    elif n <= 8:
        repair = "Swap in CVD-safe palette (Okabe-Ito, Tol, etc.) - Pick max min pairwise Delta E"
    else:
        repair = "Max Delta E + supplementary encoding (texture, labels) - Flag: hard to distinguish"

    return TestResult(
        "Categorical Pairwise ΔE",
        passed=passed,
        metric_value=round(min_de, 2),
        threshold=CATEGORICAL_MIN_DE,
        repair_suggestion=repair,
        details={
            "worst_pair": worst_pair,
            "n_categories": n,
            "cvd_type": cvd_type,
            "failure_mode": FailureMode.PAIRWISE_COLLAPSE if not passed else None,
        },
    )


# Check that L* values stay monotonic under CVD simulation.
def test_sequential_monotonicity(colors: List[Tuple], cvd_type: str = "deutan") -> TestResult:
    if len(colors) < 2:
        return TestResult(
            "Sequential Test 1: Monotonicity",
            passed=True,
            metric_value=1.0,
            threshold=1.0,
            details={"reason": "fewer than 2 colors"},
        )

    L_values = get_lightness_under_cvd(colors, cvd_type)
    diffs = np.diff(L_values)

    # figure out expected direction
    orig_L = [srgb_to_lab(c)[0] for c in colors]
    expected_increasing = np.mean(np.diff(orig_L)) >= 0

    # For large palettes, sRGB quantization and CVD simulation introduce
    # small L* reversals (~1-2 units) that are imperceptible.  Scale the
    # tolerance so these don't count as monotonicity failures.
    n = len(colors)
    tol = MONOTONICITY_TOLERANCE if n <= 30 else min(2.0, MONOTONICITY_TOLERANCE + 0.03 * (n - 30))

    if expected_increasing:
        is_monotonic = np.all(diffs > -tol)
    else:
        is_monotonic = np.all(diffs < tol)

    return TestResult(
        "Sequential Test 1: Monotonicity",
        passed=is_monotonic,
        metric_value=1.0 if is_monotonic else 0.0,
        threshold=1.0,
        repair_suggestion=None if is_monotonic else "Force monotonic L* (Single-hue ramp or adjust L*)",
        details={
            "L_values_under_cvd": [round(l, 2) for l in L_values],
            "diffs": [round(d, 2) for d in diffs],
            "expected_direction": "increasing" if expected_increasing else "decreasing",
            "failure_mode": FailureMode.MONOTONICITY_VIOLATION if not is_monotonic else None,
        },
    )


# Check that step sizes are roughly uniform (CV < 0.4).
def test_sequential_uniformity(colors: List[Tuple], cvd_type: str = "deutan") -> TestResult:
    if len(colors) < 3:
        return TestResult(
            "Sequential Test 2: Uniformity",
            passed=True,
            metric_value=1.0,
            threshold=1.0,
            details={"reason": "fewer than 3 colors for uniformity check"},
        )

    L_values = get_lightness_under_cvd(colors, cvd_type)
    diffs = np.abs(np.diff(L_values))

    # coefficient of variation of step sizes
    mean_step = np.mean(diffs)
    std_step = np.std(diffs)
    cv = std_step / (mean_step + 1e-10)

    cv_threshold = adaptive_uniformity_cv(len(colors))
    is_uniform = cv < cv_threshold

    return TestResult(
        "Sequential Test 2: Uniformity",
        passed=is_uniform,
        metric_value=round(cv, 2),
        threshold=cv_threshold,
        repair_suggestion=None if is_uniform else "Redistribute L* evenly (Clip range or resample steps)",
        details={
            "step_sizes": [round(d, 2) for d in diffs],
            "mean_step": round(mean_step, 2),
            "std_step": round(std_step, 2),
            "cv": round(cv, 2),
            "failure_mode": FailureMode.UNIFORMITY_DISTORTION if not is_uniform else None,
        },
    )


# Check that the lightness direction is preserved under CVD.
def test_sequential_direction(colors: List[Tuple], cvd_type: str = "deutan") -> TestResult:
    if len(colors) < 2:
        return TestResult(
            "Sequential Test 3: Direction",
            passed=True,
            metric_value=1.0,
            threshold=1.0,
            details={"reason": "fewer than 2 colors"},
        )

    # compare original vs CVD-simulated direction
    orig_L = [srgb_to_lab(c)[0] for c in colors]
    orig_increasing = orig_L[-1] > orig_L[0]

    cvd_L = get_lightness_under_cvd(colors, cvd_type)
    cvd_increasing = cvd_L[-1] > cvd_L[0]

    direction_preserved = (orig_increasing == cvd_increasing)

    return TestResult(
        "Sequential Test 3: Direction",
        passed=direction_preserved,
        metric_value=1.0 if direction_preserved else 0.0,
        threshold=1.0,
        repair_suggestion=None if direction_preserved else "Remap to CVD-safe seq. (viridis, cividis, inferno)",
        details={
            "original_direction": "increasing" if orig_increasing else "decreasing",
            "cvd_direction": "increasing" if cvd_increasing else "decreasing",
            "failure_mode": FailureMode.DIRECTION_REVERSAL if not direction_preserved else None,
        },
    )


# Check that adjacent steps have at least 3 delta L* between them.
def test_sequential_min_step(colors: List[Tuple], cvd_type: str = "deutan") -> TestResult:
    if len(colors) < 2:
        return TestResult(
            "Sequential Test 4: Min Step Size",
            passed=True,
            metric_value=float('inf'),
            threshold=SEQUENTIAL_MIN_DL,
            details={"reason": "fewer than 2 colors"},
        )

    L_values = get_lightness_under_cvd(colors, cvd_type)
    diffs = np.abs(np.diff(L_values))
    min_step = np.min(diffs) if len(diffs) > 0 else 0

    step_threshold = adaptive_min_step(len(colors))
    passed = min_step >= step_threshold

    return TestResult(
        "Sequential Test 4: Min Step Size",
        passed=passed,
        metric_value=round(min_step, 2),
        threshold=step_threshold,
        repair_suggestion=None if passed else "Expand L* range or reduce number of bins",
        details={
            "step_sizes": [round(d, 2) for d in diffs],
            "min_step": round(min_step, 2),
        },
    )


# Extra check for multi-hue sequential palettes: adjacent delta E >= 5.
# Multi-hue ramps can have plateaus where consecutive steps merge under CVD.
def test_sequential_adjacent_de(colors: List[Tuple], cvd_type: str = "deutan") -> TestResult:
    n = len(colors)
    ADJACENT_MIN_DE = max(0.02, min(5.0, 55.0 / max(n - 1, 1) * 0.6)) if n > 15 else 5.0

    if len(colors) < 2:
        return TestResult(
            "Sequential Cross-Check: Adjacent ΔE",
            passed=True,
            metric_value=float('inf'),
            threshold=ADJACENT_MIN_DE,
            details={"reason": "fewer than 2 colors"},
        )

    min_de = float('inf')
    worst_pair = None
    adjacent_des = []

    for i in range(len(colors) - 1):
        lab_i = simulate_cvd_lab(colors[i], cvd_type)
        lab_j = simulate_cvd_lab(colors[i + 1], cvd_type)
        de = ciede2000(lab_i, lab_j)
        adjacent_des.append(round(de, 2))
        if de < min_de:
            min_de = de
            worst_pair = (i, i + 1)

    passed = min_de >= ADJACENT_MIN_DE

    return TestResult(
        "Sequential Cross-Check: Adjacent ΔE",
        passed=passed,
        metric_value=round(min_de, 2),
        threshold=ADJACENT_MIN_DE,
        repair_suggestion=None if passed else (
            "Multi-hue ramp has plateau under CVD. "
            "Remap to single-hue CVD-safe ramp (viridis, cividis) or increase L* separation."
        ),
        details={
            "adjacent_delta_e_values": adjacent_des,
            "worst_pair": worst_pair,
            "failure_mode": FailureMode.PAIRWISE_COLLAPSE if not passed else None,
            "note": "Cross-type check: pairwise separation applied to sequential palette",
        },
    )


# Sort colors by L* ascending. SVG palette order is arbitrary, so we need
# to sort before testing monotonicity and step sizes.
def _sort_by_lightness(colors: List[Tuple]) -> List[Tuple]:
    labs = [srgb_to_lab(c) for c in colors]
    sorted_indices = sorted(range(len(colors)), key=lambda i: labs[i][0])
    return [colors[i] for i in sorted_indices]


# Run all four sequential tests (plus an adjacent delta E check for multi-hue).
def run_sequential_tests(colors: List[Tuple], cvd_type: str = "deutan",
                         classification_details: Optional[Dict] = None) -> List[TestResult]:
    sorted_colors = _sort_by_lightness(colors)

    results = [
        test_sequential_monotonicity(sorted_colors, cvd_type),
        test_sequential_uniformity(sorted_colors, cvd_type),
        test_sequential_direction(sorted_colors, cvd_type),
        test_sequential_min_step(sorted_colors, cvd_type),
    ]

    # multi-hue palettes get an extra adjacent delta E check.
    # Compute hue diversity from the actual colors being tested (not the
    # original classification) so that single-hue replacement ramps skip
    # this check even if the original palette was multi-hue.
    labs = [srgb_to_lab(c) for c in sorted_colors]
    hues = [np.degrees(np.arctan2(lab[2], lab[1])) % 360 for lab in labs
            if np.sqrt(lab[1]**2 + lab[2]**2) > 20]
    if len(hues) >= 2:
        hue_arr = np.array(hues)
        hue_sin = np.mean(np.sin(np.radians(hue_arr)))
        hue_cos = np.mean(np.cos(np.radians(hue_arr)))
        hue_div = np.degrees(np.sqrt(-2 * np.log(max(1e-10, np.sqrt(hue_sin**2 + hue_cos**2)))))
    else:
        hue_div = 0
    if hue_div > 40:
        results.append(test_sequential_adjacent_de(sorted_colors, cvd_type))

    return results


# Find the actual diverging midpoint as the L* extremum in the interior,
# rather than assuming it's at n//2 (palette may not be in gradient order).
def _find_diverging_midpoint(colors: List[Tuple]) -> int:
    n = len(colors)
    L_values = [srgb_to_lab(c)[0] for c in colors]
    L_arr = np.array(L_values)

    min_idx = int(np.argmin(L_arr))
    max_idx = int(np.argmax(L_arr))

    interior = []
    if 0 < max_idx < n - 1:
        interior.append(max_idx)
    if 0 < min_idx < n - 1:
        interior.append(min_idx)

    if interior:
        mean_L = np.mean(L_arr)
        return max(interior, key=lambda i: abs(L_arr[i] - mean_L))
    return n // 2


# Split a diverging palette into two arms by HUE CLUSTER (not by positional
# L* argmax), mirroring the recolorer. Returns two lists of indices, each
# ordered from the lightest color (midpoint-adjacent) to the darkest
# (endpoint). The lightest color in the palette serves as the shared
# midpoint anchor and is prepended to both arms, so each arm reads as a
# sequential ramp starting from the midpoint.
#
# This exists because the earlier positional slice `colors[:mid+1]` /
# `colors[mid:]` assumes the palette is authored in V-shape gradient order.
# Many real-world SVGs (election maps, migration maps, etc.) are authored
# bin-by-bin in arbitrary order, which scrambles the positional split and
# makes sequential checks fail on well-formed diverging palettes.
def _split_diverging_arms_by_hue(colors: List[Tuple]):
    n = len(colors)
    labs = [srgb_to_lab(c) for c in colors]
    L_values = [lab[0] for lab in labs]
    chromas = [np.sqrt(lab[1] ** 2 + lab[2] ** 2) for lab in labs]
    hues_deg = [np.degrees(np.arctan2(lab[2], lab[1])) % 360 for lab in labs]

    # Midpoint anchor = the lightest low-chroma color. Picking plain argmax(L*)
    # fails when the palette's brightest color is a saturated arm endpoint
    # (e.g. #66ff66 in a growth map, #dede00 in a RdYlGn) rather than the
    # semantic neutral. Fall back to overall argmax(L*) if no color is near-
    # neutral (single-hue-ish palettes without a clear midpoint color).
    NEUTRAL_CHROMA_MAX = 25
    near_neutral = [i for i in range(n) if chromas[i] < NEUTRAL_CHROMA_MAX]
    if near_neutral:
        midpoint_idx = max(near_neutral, key=lambda i: L_values[i])
    else:
        midpoint_idx = int(np.argmax(np.array(L_values)))

    # Chromatic threshold is deliberately low so that desaturated-but-hued
    # colors (e.g. a pale gray-green at chroma 8) cluster by their hue rather
    # than getting shunted into the nearest-L* arm, which would land them on
    # the wrong semantic side of the palette.
    chromatic_idx = [i for i in range(n) if chromas[i] > 5 and i != midpoint_idx]
    neutral_idx = [i for i in range(n)
                   if chromas[i] <= 5 and i != midpoint_idx]

    if len(chromatic_idx) < 2:
        # Not enough chromatic colors to split by hue; fall back to L* split.
        arr = sorted(range(n), key=lambda i: L_values[i], reverse=True)
        half = len(arr) // 2
        return arr[:half + 1], [arr[0]] + arr[half + 1:]

    sorted_by_hue = sorted(chromatic_idx, key=lambda i: hues_deg[i])
    m_ = len(sorted_by_hue)
    max_gap = 0.0
    split_k = 0
    for k in range(m_):
        h_cur = hues_deg[sorted_by_hue[k]]
        h_nxt = hues_deg[sorted_by_hue[(k + 1) % m_]]
        gap = (h_nxt - h_cur) % 360
        if gap > max_gap:
            max_gap = gap
            split_k = k
    rotated = [sorted_by_hue[(split_k + 1 + j) % m_] for j in range(m_)]

    second_gap = 0.0
    second_k = 0
    for k in range(len(rotated) - 1):
        h_cur = hues_deg[rotated[k]]
        h_nxt = hues_deg[rotated[k + 1]]
        gap = (h_nxt - h_cur) % 360
        if gap > second_gap:
            second_gap = gap
            second_k = k
    cluster_1 = rotated[: second_k + 1]
    cluster_2 = rotated[second_k + 1:]

    # Neutrals ride with whichever cluster has the closest mean L*.
    if cluster_1 and cluster_2:
        mean_L_1 = float(np.mean([L_values[i] for i in cluster_1]))
        mean_L_2 = float(np.mean([L_values[i] for i in cluster_2]))
        for idx in neutral_idx:
            if abs(L_values[idx] - mean_L_1) <= abs(L_values[idx] - mean_L_2):
                cluster_1.append(idx)
            else:
                cluster_2.append(idx)
    else:
        (cluster_1 if cluster_1 else cluster_2).extend(neutral_idx)

    # Each arm reads lightest-to-darkest as a standalone sequential ramp.
    # The midpoint is included in the arm where its L* naturally fits (i.e.,
    # not force-prepended), so if one of the cluster colors happens to be
    # lighter than the semantic midpoint, the arm's L* sequence stays
    # monotonic. Include the midpoint in both arms for anchoring.
    def _sort_with_midpoint(cluster):
        members = list(cluster)
        if midpoint_idx not in members:
            members.append(midpoint_idx)
        return sorted(members, key=lambda i: L_values[i], reverse=True)

    arm_1 = _sort_with_midpoint(cluster_1)
    arm_2 = _sort_with_midpoint(cluster_2)
    return arm_1, arm_2


# Check that the midpoint sits at a perceptual extremum (lightest or darkest)
# and is distinguishable from its neighbors.
def test_diverging_midpoint_extremum(colors: List[Tuple], cvd_type: str = "deutan") -> TestResult:
    n = len(colors)
    if n < 3:
        return TestResult(
            "Diverging Test 1: Midpoint Extremum",
            passed=False,
            metric_value=0.0,
            threshold=1.0,
            details={"reason": "need at least 3 colors for diverging"},
        )

    mid_idx = _find_diverging_midpoint(colors)
    L_values = get_lightness_under_cvd(colors, cvd_type)

    # is the midpoint at the L* minimum or maximum?
    mid_L = L_values[mid_idx]
    min_L = np.min(L_values)
    max_L = np.max(L_values)

    is_extremum = (abs(mid_L - min_L) < 1.0) or (abs(mid_L - max_L) < 1.0)

    # also make sure it doesn't blend into its neighbors.
    # For large palettes the step size near the midpoint is inherently small,
    # so we scale the threshold the same way as sequential min-step.
    neighbor_threshold = adaptive_min_step(n) if n > 15 else 3.0
    if mid_idx > 0 and mid_idx < n - 1:
        de_left = ciede2000(
            simulate_cvd_lab(colors[mid_idx], cvd_type),
            simulate_cvd_lab(colors[mid_idx - 1], cvd_type),
        )
        de_right = ciede2000(
            simulate_cvd_lab(colors[mid_idx], cvd_type),
            simulate_cvd_lab(colors[mid_idx + 1], cvd_type),
        )
        midpoint_distinct = min(de_left, de_right) >= neighbor_threshold
    else:
        midpoint_distinct = True

    passed = is_extremum and midpoint_distinct

    if not passed:
        if not midpoint_distinct:
            failure = "Disappears (blends in)"
        else:
            failure = "Shifts (wrong step)"
        repair = "Increase L* contrast at midpoint / anchor explicitly"
    else:
        failure = None
        repair = None

    return TestResult(
        "Diverging Test 1: Midpoint Extremum",
        passed=passed,
        metric_value=1.0 if passed else 0.0,
        threshold=1.0,
        repair_suggestion=repair,
        details={
            "midpoint_index": mid_idx,
            "midpoint_L": round(mid_L, 2),
            "is_extremum": is_extremum,
            "is_distinct_from_neighbors": midpoint_distinct,
            "failure_mode": failure,
        },
    )


# Check that the two endpoints remain distinct under CVD (delta E > 10).
def test_diverging_endpoints_distinct(colors: List[Tuple], cvd_type: str = "deutan") -> TestResult:
    if len(colors) < 2:
        return TestResult(
            "Diverging Test 2: Endpoints Distinct",
            passed=False,
            metric_value=0.0,
            threshold=DIVERGING_ENDPOINT_MIN_DE,
            details={"reason": "need at least 2 colors"},
        )

    left_lab = simulate_cvd_lab(colors[0], cvd_type)
    right_lab = simulate_cvd_lab(colors[-1], cvd_type)
    de = ciede2000(left_lab, right_lab)

    passed = de >= DIVERGING_ENDPOINT_MIN_DE

    return TestResult(
        "Diverging Test 2: Endpoints Distinct",
        passed=passed,
        metric_value=round(de, 2),
        threshold=DIVERGING_ENDPOINT_MIN_DE,
        repair_suggestion=None if passed else "Replace arm hue(s) to stay distinct under CVD",
        details={
            "endpoint_de": round(de, 2),
            "failure_mode": FailureMode.BIDIRECTIONAL_COLLAPSE if not passed else None,
        },
    )


# Check that each arm individually passes the sequential tests.
def test_diverging_arms_sequential(colors: List[Tuple], cvd_type: str = "deutan") -> TestResult:
    n = len(colors)
    if n < 3:
        return TestResult(
            "Diverging Test 3: Arms Sequential",
            passed=False,
            metric_value=0.0,
            threshold=1.0,
            details={"reason": "need at least 3 colors"},
        )

    left_idx, right_idx = _split_diverging_arms_by_hue(colors)
    left_arm = [colors[i] for i in left_idx]
    right_arm = [colors[i] for i in right_idx]

    left_results = run_sequential_tests(left_arm, cvd_type)
    right_results = run_sequential_tests(right_arm, cvd_type)

    # For highly asymmetric diverging palettes, the short arm has a
    # compressed L* range and can't meet standalone min-step thresholds.
    # Relax: if a small arm only fails min-step, treat it as passing.
    def _relax_small_arm(results, arm_len):
        if arm_len < n // 4:
            return [r for r in results
                    if not (not r.passed and "Min Step" in r.test_name)]
        return results

    left_results = _relax_small_arm(left_results, len(left_arm))
    right_results = _relax_small_arm(right_results, len(right_arm))

    left_passed = all(r.passed for r in left_results)
    right_passed = all(r.passed for r in right_results)
    both_passed = left_passed and right_passed

    return TestResult(
        "Diverging Test 3: Arms Sequential",
        passed=both_passed,
        metric_value=1.0 if both_passed else 0.0,
        threshold=1.0,
        repair_suggestion=None if both_passed else "Apply Sequential repairs to each failing arm independently",
        details={
            "left_arm_passed": left_passed,
            "right_arm_passed": right_passed,
            "left_arm_results": [str(r) for r in left_results],
            "right_arm_results": [str(r) for r in right_results],
        },
    )


# Check that the L* range ratio between arms is preserved under CVD.
# Instead of forcing symmetry, this preserves asymmetric diverging palettes:
# if the original has a 1.3:1 L* range ratio, the CVD version should approximate that.
def test_diverging_arm_ratio_preservation(colors: List[Tuple], cvd_type: str = "deutan") -> TestResult:
    n = len(colors)
    if n < 3:
        return TestResult(
            "Diverging Test 4: Arm Ratio Preservation",
            passed=False,
            metric_value=0.0,
            threshold=1.0,
            details={"reason": "need at least 3 colors"},
        )

    mid_idx = _find_diverging_midpoint(colors)

    # original L* values
    orig_L = [srgb_to_lab(c)[0] for c in colors]
    # CVD-simulated L* values
    cvd_L = list(get_lightness_under_cvd(colors, cvd_type))

    # L* range of each arm
    orig_left_range = abs(orig_L[0] - orig_L[mid_idx])
    orig_right_range = abs(orig_L[-1] - orig_L[mid_idx])
    cvd_left_range = abs(cvd_L[0] - cvd_L[mid_idx])
    cvd_right_range = abs(cvd_L[-1] - cvd_L[mid_idx])

    # compute ratio (larger / smaller, always >= 1)
    if min(orig_left_range, orig_right_range) < 1.0:
        orig_ratio = 1.0
    else:
        orig_ratio = max(orig_left_range, orig_right_range) / min(orig_left_range, orig_right_range)

    if min(cvd_left_range, cvd_right_range) < 1.0:
        cvd_ratio = float('inf') if max(cvd_left_range, cvd_right_range) > 5.0 else 1.0
    else:
        cvd_ratio = max(cvd_left_range, cvd_right_range) / min(cvd_left_range, cvd_right_range)

    ratio_diff = abs(orig_ratio - cvd_ratio)

    # also check that the longer arm stays the same side
    orig_left_longer = orig_left_range >= orig_right_range
    cvd_left_longer = cvd_left_range >= cvd_right_range
    # skip orientation check if the palette is nearly symmetric (< 3 L* difference)
    nearly_symmetric = abs(orig_left_range - orig_right_range) < 3.0
    same_orientation = nearly_symmetric or (orig_left_longer == cvd_left_longer)

    passed = ratio_diff < 0.3 and same_orientation

    if not passed and not same_orientation:
        repair = "CVD flips which arm has greater L* range; adjust arm hues to preserve asymmetry direction"
    elif not passed:
        repair = "Adjust arm L* ranges to preserve original asymmetry ratio under CVD"
    else:
        repair = None

    return TestResult(
        "Diverging Test 4: Arm Ratio Preservation",
        passed=passed,
        metric_value=round(ratio_diff, 2),
        threshold=0.3,
        repair_suggestion=repair,
        details={
            "orig_left_range": round(orig_left_range, 2),
            "orig_right_range": round(orig_right_range, 2),
            "orig_ratio": round(orig_ratio, 2),
            "cvd_left_range": round(cvd_left_range, 2),
            "cvd_right_range": round(cvd_right_range, 2),
            "cvd_ratio": round(cvd_ratio, 2) if cvd_ratio != float('inf') else "inf",
            "ratio_difference": round(ratio_diff, 2) if ratio_diff != float('inf') else "inf",
            "same_orientation": same_orientation,
        },
    )


# Run all four diverging invariant tests.
def run_diverging_tests(colors: List[Tuple], cvd_type: str = "deutan",
                        classification_details: Optional[Dict] = None) -> List[TestResult]:
    return [
        test_diverging_midpoint_extremum(colors, cvd_type),
        test_diverging_endpoints_distinct(colors, cvd_type),
        test_diverging_arms_sequential(colors, cvd_type),
        test_diverging_arm_ratio_preservation(colors, cvd_type),
    ]


# Run the right invariant tests for the palette type.
def run_invariant_tests(
    colors: List[Tuple],
    palette_type: str,
    cvd_type: str = "deutan",
    classification_details: Optional[Dict] = None,
) -> List[TestResult]:
    if palette_type == "categorical":
        return [test_categorical_pairwise(colors, cvd_type)]
    elif palette_type == "sequential":
        return run_sequential_tests(colors, cvd_type, classification_details)
    elif palette_type == "diverging":
        return run_diverging_tests(colors, cvd_type, classification_details)
    else:
        raise ValueError(f"Unknown palette type: {palette_type}")


# True if every test passed.
def all_tests_passed(test_results: List[TestResult]) -> bool:
    return all(r.passed for r in test_results)
