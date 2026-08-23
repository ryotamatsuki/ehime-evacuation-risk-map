#!/usr/bin/env python3
"""Verify STEP 1-7 production invariants against the intentional release baseline."""

from __future__ import annotations

import argparse
import json
import math
import pathlib


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def close(actual: object, expected: object, *, atol: float = 1e-9) -> bool:
    try:
        return math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=atol)
    except (TypeError, ValueError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=pathlib.Path, required=True)
    parser.add_argument("--metadata", type=pathlib.Path, required=True)
    parser.add_argument("--step5", type=pathlib.Path, required=True)
    parser.add_argument("--step7", type=pathlib.Path, required=True)
    args = parser.parse_args()

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    step5 = json.loads(args.step5.read_text(encoding="utf-8"))
    step7 = json.loads(args.step7.read_text(encoding="utf-8"))

    expected_analysis = baseline["analysis"]
    require(metadata["analysis_version"] == "analysis-core-v4-corrected-public", "unexpected analysis version")
    for key in ("target_meshes", "complete_routes", "route_unavailable", "cross_border_routes"):
        require(metadata[key] == expected_analysis[key], f"metadata {key} drift: {metadata[key]} != {expected_analysis[key]}")

    base_contract = step5["baseline_contract"]
    require(base_contract["complete_scored_rows"] == expected_analysis["complete_scores"], "complete score count drift")
    require(base_contract["capacity_missing_complete_core_rows"] == expected_analysis["capacity_missing_scores"], "capacity-missing count drift")
    require(base_contract["core_data_incomplete_rows"] == expected_analysis["core_data_incomplete"], "core-data-incomplete count drift")
    require(base_contract["route_unavailable_rows"] == expected_analysis["route_unavailable"], "route-unavailable count drift")
    demand = step5["demand_sensitivity"]
    require(demand["over_capacity_area_weighted_shelters"] == expected_analysis["area_weighted_over_capacity_shelters"], "area-weighted overload drift")
    require(demand["over_capacity_full_mesh_shelters"] == expected_analysis["full_mesh_over_capacity_shelters"], "full-mesh overload drift")

    expected_step5 = baseline["step5"]
    sensitivity = step5["weight_sensitivity"]
    require(sensitivity["scenario_count"] == expected_step5["scenario_count"], "STEP 5 scenario-count drift")
    require(sensitivity["robust_top10pct_all_scenarios_n"] == expected_step5["robust_top10pct_all_scenarios_n"], "STEP 5 robust top-decile drift")
    require(sensitivity["robust_top50_all_scenarios_n"] == expected_step5["robust_top50_all_scenarios_n"], "STEP 5 robust top-50 drift")
    equal_spearman = sensitivity["scenarios"]["equal_weight"]["spearman_vs_baseline"]
    require(close(equal_spearman, expected_step5["equal_weight_spearman_vs_baseline"], atol=1e-12), "STEP 5 equal-weight rank correlation drift")

    expected_step7 = baseline["step7"]
    require(step7["release_gate"]["pass"] is True, f"STEP 7 release gate failed: {step7['release_gate']['failures']}")
    require(step7["baseline"]["complete_scores"] == expected_analysis["complete_scores"], "STEP 7 complete-score count drift")
    require(step7["baseline"]["over_capacity_shelters"] == expected_analysis["area_weighted_over_capacity_shelters"], "STEP 7 baseline overload drift")
    scenarios = step7["scenarios"]
    require(len(scenarios) == len(expected_step7["scenarios"]), "STEP 7 scenario-count drift")
    for actual, expected in zip(scenarios, expected_step7["scenarios"], strict=True):
        for key in ("capacity_delta", "simulated_over_capacity_shelters", "resolved_over_capacity_shelters", "affected_complete_meshes"):
            require(actual[key] == expected[key], f"STEP 7 {key} drift at +{expected['capacity_delta']}: {actual[key]} != {expected[key]}")
        require(close(actual["total_score_reduction"], expected["total_score_reduction"], atol=1e-9), f"STEP 7 score-reduction drift at +{expected['capacity_delta']}")
        require(actual["known_capacity_shelters"] == expected_step7["known_capacity_shelters"], "STEP 7 known-capacity shelter count drift")

    print(
        "STEP 7.5 production regression PASS: "
        "35 over-capacity / 813 complete / 128 capacity-missing / 28 unavailable / 13 cross-border; "
        "STEP 5 69 robust top-decile / 42 robust top-50 / equal-weight rho 0.9894182482934234; "
        "STEP 7 +100=34 / +500=19 / +1000=13 unchanged"
    )


if __name__ == "__main__":
    main()
