#!/usr/bin/env python3
"""STEP 10: decompose STEP 8 unserved demand into actionable root causes.

The canonical Analysis Core v4 and STEP 8 K=10 contract are read-only baselines.
STEP 10 extends candidate sensitivity to K=20/K=30, then partitions the K=10
unserved total into mutually exhaustive causes without imputing missing capacity.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from calculate_step4_demand_capacity_risk import prepare_capacities
from capacity_allocation import prepare_candidate_table, solve_capacity_allocation
from capacity_planning_io import load_public_analysis, normalize_capacity_contract

EXPECTED_TARGET_ROWS = 1090
BASELINE_LIMIT = 10
SENSITIVITY_LIMITS = (10, 20, 30)
EPS = 1e-6
CAUSE_ORDER = (
    "route_unavailable",
    "unknown_capacity_only",
    "candidate_limit_recoverable",
    "known_capacity_saturation",
)


def _root_rows(mesh_analysis: pd.DataFrame, allocations: dict[int, object]) -> pd.DataFrame:
    base = mesh_analysis[["mesh_id", "municipality_code", "municipality"]].copy()
    base["mesh_id"] = base["mesh_id"].astype(str)
    merged = base
    for limit in SENSITIVITY_LIMITS:
        frame = allocations[limit].mesh[[
            "mesh_id", "demand", "unserved_demand", "candidate_count",
            "known_capacity_candidate_count", "unknown_capacity_candidate_count",
            "allocation_status",
        ]].copy()
        frame = frame.rename(columns={c: f"{c}_k{limit}" for c in frame.columns if c != "mesh_id"})
        merged = merged.merge(frame, on="mesh_id", how="left", validate="one_to_one")

    def cause(row: pd.Series) -> str:
        u10 = float(row["unserved_demand_k10"])
        u30 = float(row["unserved_demand_k30"])
        if u10 <= EPS:
            return "served_at_k10"
        if int(row["candidate_count_k30"]) == 0:
            return "route_unavailable"
        if int(row["known_capacity_candidate_count_k30"]) == 0:
            return "unknown_capacity_only"
        if u30 > EPS:
            return "known_capacity_saturation"
        return "candidate_limit_recoverable"

    merged["root_cause"] = merged.apply(cause, axis=1)
    merged["net_unserved_reduction_k10_to_k20"] = merged["unserved_demand_k10"] - merged["unserved_demand_k20"]
    merged["net_unserved_reduction_k10_to_k30"] = merged["unserved_demand_k10"] - merged["unserved_demand_k30"]
    return merged.sort_values(["municipality_code", "mesh_id"]).reset_index(drop=True)


def _aggregate_decomposition(root: pd.DataFrame) -> dict[str, float]:
    u10 = float(root["unserved_demand_k10"].sum())
    u30 = float(root["unserved_demand_k30"].sum())
    route = float(root.loc[root["candidate_count_k30"].eq(0), "unserved_demand_k30"].sum())
    unknown = float(root.loc[root["candidate_count_k30"].gt(0) & root["known_capacity_candidate_count_k30"].eq(0), "unserved_demand_k30"].sum())
    known = float(root.loc[root["known_capacity_candidate_count_k30"].gt(0), "unserved_demand_k30"].sum())
    recovered = u10 - u30
    result = {
        "route_unavailable": route,
        "unknown_capacity_only": unknown,
        "candidate_limit_recoverable": recovered,
        "known_capacity_saturation": known,
    }
    result["decomposition_sum"] = sum(result[key] for key in CAUSE_ORDER)
    result["baseline_k10_unserved"] = u10
    result["k30_residual_unserved"] = u30
    result["decomposition_error"] = result["decomposition_sum"] - u10
    return result


def _capacity_gap_ranking(candidates: pd.DataFrame, capacities: pd.DataFrame, root: pd.DataFrame, max_limit: int) -> pd.DataFrame:
    prepared = prepare_candidate_table(candidates, capacities, max_limit)
    residual = root.loc[
        root["candidate_count_k30"].gt(0)
        & root["known_capacity_candidate_count_k30"].eq(0)
        & root["unserved_demand_k30"].gt(EPS),
        ["mesh_id", "municipality_code", "municipality", "unserved_demand_k30"],
    ].copy()
    if residual.empty:
        return pd.DataFrame(columns=[
            "gap_rank", "shelter_key", "shelter_common_id", "shelter_name",
            "shelter_municipality_code", "affected_meshes", "residual_unserved_exposure",
            "nearest_gap_unserved_demand", "min_candidate_rank", "mean_candidate_rank",
        ])
    unknown = prepared.loc[~prepared["capacity_known"]].copy()
    unknown = unknown.merge(residual, on="mesh_id", how="inner", validate="many_to_one")
    if unknown.empty:
        raise ValueError("unknown-capacity residual exists but no unknown candidate rows were found")
    unknown["nearest_gap_unserved_demand"] = unknown["unserved_demand_k30"].where(unknown["candidate_rank"].eq(1), 0.0)
    grouped = unknown.groupby("shelter_key", as_index=False).agg(
        shelter_common_id=("shelter_common_id", "first"),
        shelter_name=("shelter_name", "first"),
        shelter_municipality_code=("shelter_municipality_code", "first"),
        affected_meshes=("mesh_id", "nunique"),
        residual_unserved_exposure=("unserved_demand_k30", "sum"),
        nearest_gap_unserved_demand=("nearest_gap_unserved_demand", "sum"),
        min_candidate_rank=("candidate_rank", "min"),
        mean_candidate_rank=("candidate_rank", "mean"),
    )
    grouped = grouped.sort_values(
        ["residual_unserved_exposure", "nearest_gap_unserved_demand", "affected_meshes", "min_candidate_rank", "shelter_key"],
        ascending=[False, False, False, True, True],
    ).reset_index(drop=True)
    grouped.insert(0, "gap_rank", range(1, len(grouped) + 1))
    return grouped


def _municipality_summary(root: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (code, name), group in root.groupby(["municipality_code", "municipality"], dropna=False):
        residual_route = float(group.loc[group["candidate_count_k30"].eq(0), "unserved_demand_k30"].sum())
        residual_unknown = float(group.loc[group["candidate_count_k30"].gt(0) & group["known_capacity_candidate_count_k30"].eq(0), "unserved_demand_k30"].sum())
        residual_known = float(group.loc[group["known_capacity_candidate_count_k30"].gt(0), "unserved_demand_k30"].sum())
        k10 = float(group["unserved_demand_k10"].sum())
        k20 = float(group["unserved_demand_k20"].sum())
        k30 = float(group["unserved_demand_k30"].sum())
        rows.append({
            "municipality_code": str(code),
            "municipality": str(name),
            "k10_unserved_demand": k10,
            "k20_unserved_demand": k20,
            "k30_unserved_demand": k30,
            "candidate_limit_recoverable_k10_to_k30": k10 - k30,
            "route_unavailable_residual": residual_route,
            "unknown_capacity_only_residual": residual_unknown,
            "known_capacity_saturation_residual": residual_known,
            "route_unavailable_meshes": int(group["candidate_count_k30"].eq(0).sum()),
            "unknown_capacity_only_meshes": int((group["candidate_count_k30"].gt(0) & group["known_capacity_candidate_count_k30"].eq(0) & group["unserved_demand_k30"].gt(EPS)).sum()),
        })
    return pd.DataFrame(rows).sort_values(["k10_unserved_demand", "municipality_code"], ascending=[False, True]).reset_index(drop=True)


def run_step10(
    candidates: pd.DataFrame,
    mesh_analysis: pd.DataFrame,
    capacities: pd.DataFrame,
    *,
    expected_rows: int = EXPECTED_TARGET_ROWS,
    frozen_step8: dict | None = None,
):
    failures: list[str] = []
    if len(mesh_analysis) != expected_rows or mesh_analysis["mesh_id"].astype(str).nunique() != expected_rows:
        failures.append(f"mesh input must contain {expected_rows} unique target meshes")

    allocations: dict[int, object] = {}
    for limit in SENSITIVITY_LIMITS:
        allocations[limit] = solve_capacity_allocation(
            candidates,
            mesh_analysis,
            capacities,
            demand_column="mesh_evacuation_demand_area_weighted",
            candidate_limit=limit,
            scenario_name=f"area_weighted_k{limit}",
        )

    unserved = {limit: float(allocations[limit].summary["unserved_demand"]) for limit in SENSITIVITY_LIMITS}
    if unserved[20] > unserved[10] + EPS or unserved[30] > unserved[20] + EPS:
        failures.append(f"candidate-limit monotonicity failed: {unserved}")
    if frozen_step8 is not None:
        expected = float(frozen_step8["scenarios"]["area_weighted"]["unserved_demand"])
        if abs(unserved[10] - expected) > EPS:
            failures.append(f"K10 baseline drifted: actual={unserved[10]} expected={expected}")

    root = _root_rows(mesh_analysis, allocations)
    decomposition = _aggregate_decomposition(root)
    if abs(float(decomposition["decomposition_error"])) > EPS:
        failures.append(f"root-cause decomposition does not close: {decomposition['decomposition_error']}")
    if decomposition["candidate_limit_recoverable"] < -EPS:
        failures.append("K30 increased total unserved demand")

    gaps = _capacity_gap_ranking(candidates, capacities, root, max(SENSITIVITY_LIMITS))
    municipalities = _municipality_summary(root)
    cause_details = {}
    for key in CAUSE_ORDER:
        amount = float(decomposition[key])
        cause_details[key] = {
            "unserved_demand": amount,
            "share_of_k10_unserved": amount / decomposition["baseline_k10_unserved"] if decomposition["baseline_k10_unserved"] else 0.0,
        }

    sensitivity = {}
    for limit in SENSITIVITY_LIMITS:
        summary = allocations[limit].summary
        sensitivity[str(limit)] = {
            "served_demand": float(summary["served_demand"]),
            "unserved_demand": float(summary["unserved_demand"]),
            "served_share": float(summary["served_share"]),
            "meshes_with_no_candidate": int(summary["meshes_with_no_candidate"]),
            "meshes_with_unknown_capacity_only": int(summary["meshes_with_unknown_capacity_only"]),
            "meshes_partially_unserved": int(summary["meshes_partially_unserved"]),
            "demand_allocated_to_rank_gt1_candidate": float(summary["demand_allocated_to_rank_gt1_candidate"]),
            "mean_additional_walking_distance_m_per_served_demand": float(summary["mean_additional_walking_distance_m_per_served_demand"]),
        }

    qa = {
        "step": "STEP 10 - unserved demand root-cause decomposition",
        "analysis_contract": "K10 frozen baseline + K20/K30 candidate-limit sensitivity",
        "canonical_analysis_core_v4_modified": False,
        "capacity_missing_imputed": False,
        "candidate_limits": list(SENSITIVITY_LIMITS),
        "baseline_k10_unserved_demand": decomposition["baseline_k10_unserved"],
        "k30_residual_unserved_demand": decomposition["k30_residual_unserved"],
        "root_causes": cause_details,
        "decomposition_sum": decomposition["decomposition_sum"],
        "decomposition_error": decomposition["decomposition_error"],
        "candidate_limit_sensitivity": sensitivity,
        "candidate_limit_net_gains": {
            "k10_to_k20": unserved[10] - unserved[20],
            "k20_to_k30": unserved[20] - unserved[30],
            "k10_to_k30": unserved[10] - unserved[30],
        },
        "route_unavailable_meshes_k30": int(allocations[30].mesh["candidate_count"].eq(0).sum()),
        "unknown_capacity_only_meshes_k30": int((allocations[30].mesh["candidate_count"].gt(0) & allocations[30].mesh["known_capacity_candidate_count"].eq(0) & allocations[30].mesh["unserved_demand"].gt(EPS)).sum()),
        "known_capacity_saturation_meshes_k30": int((allocations[30].mesh["known_capacity_candidate_count"].gt(0) & allocations[30].mesh["unserved_demand"].gt(EPS)).sum()),
        "capacity_data_gap_shelters": int(len(gaps)),
        "top_capacity_data_gaps": json.loads(gaps.head(20).to_json(orient="records", force_ascii=False)),
        "interpretation": {
            "route_unavailable": "No reachable shelter candidate exists on the routing graph. Capacity expansion alone cannot fix this cause.",
            "unknown_capacity_only": "Reachable candidates exist, but all candidates through rank 30 have unknown official capacity. Unknown is never treated as zero.",
            "candidate_limit_recoverable": "Net K10 shortage removed by extending the reachable-candidate set to K30. This is sensitivity evidence, not a new-facility recommendation.",
            "known_capacity_saturation": "Residual K30 shortage where at least one known-capacity candidate exists; this is the strict capacity bottleneck component.",
        },
        "release_gate": "PASS" if not failures else "FAIL",
        "failures": failures,
    }
    return root, gaps, municipalities, qa, failures, allocations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates-csv", type=Path, required=True)
    parser.add_argument("--public-data-root", type=Path, required=True)
    parser.add_argument("--shelters-csv", type=Path, required=True)
    parser.add_argument("--step8-qa", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--out-qa", type=Path, required=True)
    parser.add_argument("--expected-rows", type=int, default=EXPECTED_TARGET_ROWS)
    args = parser.parse_args()

    candidates = pd.read_csv(args.candidates_csv, encoding="utf-8-sig", dtype={"mesh_id": str, "shelter_common_id": str})
    mesh, _step4_shelters, metadata = load_public_analysis(args.public_data_root)
    source = pd.read_csv(args.shelters_csv, encoding="utf-8-sig", dtype={"common_id": str})
    capacities, ambiguous = prepare_capacities(source)
    capacities = normalize_capacity_contract(capacities)
    used = set(candidates["shelter_key"].astype(str))
    selected_ambiguous = sorted(used & set(ambiguous))
    if selected_ambiguous:
        raise SystemExit(f"STEP 10 candidate graph contains {len(selected_ambiguous)} ambiguous shelter identities")
    frozen = json.loads(args.step8_qa.read_text(encoding="utf-8"))
    root, gaps, municipalities, qa, failures, allocations = run_step10(
        candidates, mesh, capacities, expected_rows=args.expected_rows, frozen_step8=frozen
    )
    qa["canonical_analysis_source_sha"] = metadata.get("analysis_source_sha")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    root.to_csv(args.out_dir / "unserved_root_causes.csv", index=False, encoding="utf-8-sig")
    gaps.to_csv(args.out_dir / "capacity_data_gaps.csv", index=False, encoding="utf-8-sig")
    municipalities.to_csv(args.out_dir / "municipality_root_cause_summary.csv", index=False, encoding="utf-8-sig")
    for limit in (20, 30):
        allocations[limit].mesh.to_csv(args.out_dir / f"allocation_mesh_k{limit}.csv", index=False, encoding="utf-8-sig")
    args.out_qa.parent.mkdir(parents=True, exist_ok=True)
    args.out_qa.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit("STEP 10 release gate failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
