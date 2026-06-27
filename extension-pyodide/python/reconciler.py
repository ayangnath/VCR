# Reconcile palette type vs data signals (DR3/DR4). When they disagree, offers
# two paths: Path A respects the designer's choice, Path B follows the data.

from typing import Optional
from data_signal_extractor import DataSignals


# If there's no mismatch, reconciled_type just equals the original.
# If there is one, both paths are described and needs_user_choice is set.
class ReconciliationResult:
    def __init__(
        self,
        needs_user_choice: bool,
        reconciled_type: str,
        path_a_description: Optional[str] = None,
        path_b_type: Optional[str] = None,
        path_b_description: Optional[str] = None,
        mismatch_explanation: Optional[str] = None,
    ):
        self.needs_user_choice = needs_user_choice
        self.reconciled_type = reconciled_type
        self.path_a_description = path_a_description
        self.path_b_type = path_b_type
        self.path_b_description = path_b_description
        self.mismatch_explanation = mismatch_explanation

    def __repr__(self):
        if not self.needs_user_choice:
            return f"ReconciliationResult(type={self.reconciled_type}, no_mismatch)"
        return (
            f"ReconciliationResult(CHOICE_NEEDED, "
            f"A={self.reconciled_type}, B={self.path_b_type})"
        )


# Reconcile palette type with data signals. Pass "A" to respect the
# designer, "B" to follow the data, or None for automatic.
def reconcile_palette_vs_data(
    palette_type: str,
    signals: DataSignals,
    user_preference: Optional[str] = None,
    classification_details: dict = None
) -> ReconciliationResult:
    classification_details = classification_details or {}

    if not signals.possible_mismatch:
        return ReconciliationResult(
            needs_user_choice=False,
            reconciled_type=palette_type,
        )

    # mismatch detected
    suggested_type = _infer_data_appropriate_type(palette_type, signals)

    path_a_desc = _build_path_a_description(palette_type)
    path_b_desc = _build_path_b_description(suggested_type, signals)

    if user_preference == "B":
        return ReconciliationResult(
            needs_user_choice=False,
            reconciled_type=suggested_type,
            path_a_description=path_a_desc,
            path_b_type=suggested_type,
            path_b_description=path_b_desc,
            mismatch_explanation=signals.mismatch_reason,
        )
    elif user_preference == "A":
        return ReconciliationResult(
            needs_user_choice=False,
            reconciled_type=palette_type,
            path_a_description=path_a_desc,
            path_b_type=suggested_type,
            path_b_description=path_b_desc,
            mismatch_explanation=signals.mismatch_reason,
        )
    else:
        auto_choose_b = _should_auto_choose_path_b(palette_type, signals, classification_details)

        if auto_choose_b:
            return ReconciliationResult(
                needs_user_choice=True,
                reconciled_type=suggested_type,
                path_a_description=path_a_desc,
                path_b_type=suggested_type,
                path_b_description=path_b_desc,
                mismatch_explanation=signals.mismatch_reason,
            )
        else:
            return ReconciliationResult(
                needs_user_choice=True,
                reconciled_type=palette_type,
                path_a_description=path_a_desc,
                path_b_type=suggested_type,
                path_b_description=path_b_desc,
                mismatch_explanation=signals.mismatch_reason,
            )


# Infer what palette type best matches the data signals.
def _infer_data_appropriate_type(palette_type: str, signals: DataSignals) -> str:
    if palette_type == "categorical":
        if signals.has_numeric_labels or signals.n_categories > 10:
            return "sequential"

    elif palette_type == "sequential":
        if signals.has_string_labels and signals.n_categories <= 8:
            return "categorical"

    elif palette_type == "diverging":
        if not signals.has_semantic_midpoint:
            return "sequential"

    return palette_type


# Description for Path A (respect the designer's type).
def _build_path_a_description(palette_type: str) -> str:
    descriptions = {
        "categorical": (
            "Path A: Respect designer's categorical palette. "
            "Repair will optimize for pairwise distinguishability (delta E >= 8), "
            "treating each color as a discrete category label."
        ),
        "sequential": (
            "Path A: Respect designer's sequential palette. "
            "Repair will ensure monotonic lightness gradient with uniform steps, "
            "treating data as ordered/continuous."
        ),
        "diverging": (
            "Path A: Respect designer's diverging palette. "
            "Repair will preserve bidirectional structure with distinct midpoint, "
            "assuming data has meaningful reference value."
        ),
    }
    return descriptions.get(palette_type, f"Path A: Optimize for {palette_type} encoding.")


# Description for Path B (follow the data signals).
def _build_path_b_description(suggested_type: str, signals: DataSignals) -> str:
    reasons = {
        "categorical": (
            f"Path B: Treat as categorical data. "
            f"Evidence: {signals.mismatch_reason} "
            f"Repair will use discrete color palette for {signals.n_categories} categories."
        ),
        "sequential": (
            f"Path B: Treat as sequential data. "
            f"Evidence: {signals.mismatch_reason} "
            f"Repair will use ordered lightness gradient to convey magnitude."
        ),
        "diverging": (
            f"Path B: Treat as diverging data. "
            f"Evidence: {signals.mismatch_reason} "
            f"Repair will use bidirectional gradient with neutral midpoint."
        ),
    }
    return reasons.get(suggested_type, f"Path B: Repair as {suggested_type}.")


# Decide whether to automatically pick Path B for high-confidence mismatches.
def _should_auto_choose_path_b(palette_type: str, signals: DataSignals,
                               classification_details: dict = None) -> bool:
    classification_details = classification_details or {}

    # strong hue clusters suggest intentional diverging design
    has_strong_hue_clusters = False
    if classification_details.get("has_hue_clusters"):
        cluster_info = classification_details.get("cluster_info", {})
        hue_separation = cluster_info.get("hue_separation", 0)
        if hue_separation >= 60:
            has_strong_hue_clusters = True

    # diverging without a midpoint, but hue clusters mean it's intentional
    if palette_type == "diverging" and not signals.has_semantic_midpoint:
        if has_strong_hue_clusters:
            return False

        if signals.n_categories > 8:
            return True
        if signals.has_numeric_labels and not signals.has_string_labels:
            return True

    # categorical with only numeric labels is probably continuous data
    if palette_type == "categorical":
        if signals.has_numeric_labels and not signals.has_string_labels:
            return True
        if signals.n_categories > 12:
            return True

    return False
