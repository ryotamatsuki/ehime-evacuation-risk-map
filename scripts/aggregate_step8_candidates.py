#!/usr/bin/env python3
"""STEP 8B aggregate and double-parity release gate for candidate routes.

Rank 1 is checked twice:
1. against STEP 2 rebuilt on the identical GraphML used for the candidate run;
2. against the immutable corrected public Analysis Core v4 artifact.

Shelter municipality / cross-border metadata on rank 1 is then anchored to the
corrected public artifact. This is metadata authority only: shelter identity and
walking distance must already match both route sources before any correction is
applied. Ranks 2+ retain their route-run metadata.
"""
from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np
import pandas as pd

EXPECTED_TARGET_ROWS = 1090
EXPECTED_COMPLETE_ROUTES = 1062
EXPECTED_CANONICAL_CROSS_BORDER = 13
EXPECTED_ANALYSIS_VERSION = "analysis-core-v4-corrected-public"
DISTANCE_TOLERANCE_M = 1e-6


def _read_many(input_dir: pathlib.Path, pattern: str, dtype=None) -> pd.DataFrame:
    paths = sorted(input_dir.rglob(pattern))
    if not paths:
        raise SystemExit(f"no files matched {pattern} under {input_dir}")
    return pd.concat(
        [pd.read_csv(path, encoding="utf-8-sig", dtype=dtype) for path in paths],
        ignore_index=True,
        sort=False,
    )


def load_canonical_routes(public_data_root: pathlib.Path) -> tuple[pd.DataFrame, dict[str, object]]:
    metadata_path = public_data_root / "metadata" / "analysis.json"
    index_path = public_data_root / "routes" / "index.json"
    if not metadata_path.is_file() or not index_path.is_file():
        raise ValueError("canonical public artifact is missing metadata/analysis.json or routes/index.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(index, list) or not index:
        raise ValueError("canonical routes/index.json must be a non-empty list")
    rows: list[dict[str, object]] = []
    for item in index:
        rel = item.get("file")
        if not isinstance(rel, str) or not rel:
            raise ValueError("canonical route index item is missing file")
        payload = json.loads((public_data_root / rel).read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"canonical route payload is not a list: {rel}")
        rows.extend(payload)
    routes = pd.DataFrame(rows)
    required = {
        "mesh_id", "route_status", "selected_shelter_common_id",
        "selected_shelter_name", "shelter_municipality_code", "cross_border",
        "total_walking_distance_m",
    }
    missing = sorted(required - set(routes.columns))
    if missing:
        raise ValueError(f"canonical routes missing fields: {', '.join(missing)}")
    routes["mesh_id"] = routes["mesh_id"].astype(str)
    return routes, metadata


def _as_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series.fillna(False)
    return series.fillna(False).astype(str).str.lower().eq("true")


def _identity_mask(left_id: pd.Series, left_name: pd.Series, right_id: pd.Series, right_name: pd.Series) -> pd.Series:
    return (
        left_id.fillna("").astype(str).eq(right_id.fillna("").astype(str))
        & left_name.fillna("").astype(str).eq(right_name.fillna("").astype(str))
    )


def _distance_delta(left: pd.Series, right: pd.Series) -> tuple[pd.Series, float]:
    delta = (pd.to_numeric(left, errors="coerce") - pd.to_numeric(right, errors="coerce")).abs()
    return delta, float(delta.max()) if len(delta) else 0.0


def aggregate_step8_candidates(
    candidates: pd.DataFrame,
    status: pd.DataFrame,
    baseline: pd.DataFrame,
    canonical_routes: pd.DataFrame,
    canonical_metadata: dict[str, object],
    *,
    expected_rows: int = EXPECTED_TARGET_ROWS,
    expected_complete: int = EXPECTED_COMPLETE_ROUTES,
    candidate_limit: int = 10,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object], list[str]]:
    failures: list[str] = []
    candidates = candidates.copy(); status = status.copy(); baseline = baseline.copy(); canonical_routes = canonical_routes.copy()
    for frame in (candidates, status, baseline, canonical_routes):
        frame["mesh_id"] = frame["mesh_id"].astype(str)

    if len(status) != expected_rows:
        failures.append(f"candidate status rows={len(status)} expected={expected_rows}")
    if status["mesh_id"].nunique() != len(status): failures.append("duplicate mesh_id in candidate status")
    if len(baseline) != expected_rows or baseline["mesh_id"].nunique() != len(baseline):
        failures.append("same-graph STEP 2 baseline must contain one row per target mesh")
    if len(canonical_routes) != expected_rows or canonical_routes["mesh_id"].nunique() != len(canonical_routes):
        failures.append("canonical public routes must contain one row per target mesh")
    mesh_set = set(status["mesh_id"])
    if set(baseline["mesh_id"]) != mesh_set: failures.append("candidate status mesh set differs from same-graph STEP 2 baseline")
    if set(canonical_routes["mesh_id"]) != mesh_set: failures.append("candidate status mesh set differs from canonical public routes")

    canonical_contract = (
        canonical_metadata.get("analysis_version") == EXPECTED_ANALYSIS_VERSION
        and canonical_metadata.get("target_meshes") == expected_rows
        and canonical_metadata.get("complete_routes") == expected_complete
        and canonical_metadata.get("route_unavailable") == expected_rows - expected_complete
        and canonical_metadata.get("cross_border_routes") == EXPECTED_CANONICAL_CROSS_BORDER
    )
    if not canonical_contract: failures.append("canonical Analysis Core v4 metadata contract mismatch")

    complete_status = status["candidate_status"].astype(str).eq("complete")
    if int(complete_status.sum()) != expected_complete:
        failures.append(f"complete candidate meshes={int(complete_status.sum())} expected={expected_complete}")
    if int((~complete_status).sum()) != expected_rows - expected_complete: failures.append("candidate failure count differs from expected")

    same_graph_status = status.merge(baseline[["mesh_id", "route_status"]], on="mesh_id", how="left", validate="one_to_one")
    same_graph_status_mismatch = same_graph_status[same_graph_status["candidate_status"].astype(str) != same_graph_status["route_status"].astype(str)]
    if len(same_graph_status_mismatch): failures.append(f"candidate/same-graph STEP2 route status mismatch rows={len(same_graph_status_mismatch)}")
    canonical_status = status.merge(canonical_routes[["mesh_id", "route_status"]], on="mesh_id", how="left", validate="one_to_one")
    canonical_status_mismatch = canonical_status[canonical_status["candidate_status"].astype(str) != canonical_status["route_status"].astype(str)]
    if len(canonical_status_mismatch): failures.append(f"candidate/canonical route status mismatch rows={len(canonical_status_mismatch)}")

    if len(candidates):
        candidates["candidate_rank"] = pd.to_numeric(candidates["candidate_rank"], errors="coerce")
        candidates["total_walking_distance_m"] = pd.to_numeric(candidates["total_walking_distance_m"], errors="coerce")
        if candidates[["candidate_rank", "total_walking_distance_m"]].isna().any().any(): failures.append("candidate rows contain invalid rank or distance")
        if candidates.duplicated(["mesh_id", "shelter_key"]).any(): failures.append("duplicate mesh_id/shelter_key candidate rows")
        if candidates.duplicated(["mesh_id", "candidate_rank"]).any(): failures.append("duplicate candidate rank within a mesh")
        if candidates["candidate_rank"].lt(1).any() or candidates["candidate_rank"].gt(candidate_limit).any(): failures.append("candidate rank outside configured limit")
        for mesh_id, group in candidates.groupby("mesh_id", sort=False):
            ordered = group.sort_values("candidate_rank"); ranks = [int(v) for v in ordered["candidate_rank"]]
            if ranks != list(range(1, len(ranks) + 1)):
                failures.append(f"non-consecutive candidate ranks for mesh {mesh_id}"); break
            distances = ordered["total_walking_distance_m"].astype(float).to_numpy()
            if len(distances) > 1 and np.any(np.diff(distances) < -1e-7):
                failures.append(f"candidate distance order violation for mesh {mesh_id}"); break

    rank1 = candidates.loc[candidates["candidate_rank"].eq(1)].copy() if len(candidates) else candidates.copy()
    if len(rank1) != expected_complete: failures.append(f"rank1 candidate rows={len(rank1)} expected={expected_complete}")

    same_graph_complete = baseline.loc[baseline["route_status"].astype(str).eq("complete")].copy()
    same_graph_compare = same_graph_complete.merge(
        rank1[["mesh_id", "shelter_common_id", "shelter_name", "total_walking_distance_m", "cross_border"]],
        on="mesh_id", how="left", validate="one_to_one", suffixes=("_step2", "_step8"),
    )
    same_graph_identity = _identity_mask(
        same_graph_compare["selected_shelter_common_id"], same_graph_compare["selected_shelter_name"],
        same_graph_compare["shelter_common_id"], same_graph_compare["shelter_name"],
    )
    if not bool(same_graph_identity.all()): failures.append(f"same-graph rank1 shelter identity mismatch rows={int((~same_graph_identity).sum())}")
    same_graph_delta, same_graph_max_delta = _distance_delta(same_graph_compare["total_walking_distance_m_step2"], same_graph_compare["total_walking_distance_m_step8"])
    if same_graph_delta.isna().any() or (same_graph_delta > DISTANCE_TOLERANCE_M).any():
        failures.append(f"same-graph rank1 distance mismatch; max absolute delta={same_graph_max_delta}")
    same_graph_cross = int(_as_bool(same_graph_compare["cross_border_step8"]).sum())

    canonical_complete = canonical_routes.loc[canonical_routes["route_status"].astype(str).eq("complete")].copy()
    canonical_compare = canonical_complete.merge(
        rank1[["mesh_id", "shelter_common_id", "shelter_name", "total_walking_distance_m", "cross_border"]],
        on="mesh_id", how="left", validate="one_to_one", suffixes=("_canonical", "_step8"),
    )
    canonical_identity = _identity_mask(
        canonical_compare["selected_shelter_common_id"], canonical_compare["selected_shelter_name"],
        canonical_compare["shelter_common_id"], canonical_compare["shelter_name"],
    )
    if not bool(canonical_identity.all()): failures.append(f"canonical rank1 shelter identity mismatch rows={int((~canonical_identity).sum())}")
    canonical_delta, canonical_max_delta = _distance_delta(canonical_compare["total_walking_distance_m_canonical"], canonical_compare["total_walking_distance_m_step8"])
    if canonical_delta.isna().any() or (canonical_delta > DISTANCE_TOLERANCE_M).any():
        failures.append(f"canonical rank1 distance mismatch; max absolute delta={canonical_max_delta}")

    raw_cross = _as_bool(canonical_compare["cross_border_step8"]); canonical_cross_mask = _as_bool(canonical_compare["cross_border_canonical"])
    canonical_cross = int(canonical_cross_mask.sum()); metadata_corrections = int((raw_cross != canonical_cross_mask).sum())
    expected_metadata_corrections = int(canonical_metadata.get("cross_border_metadata_corrections_by_shelter_address", 0) or 0)
    if canonical_cross != EXPECTED_CANONICAL_CROSS_BORDER: failures.append(f"canonical rank1 cross-border routes={canonical_cross} expected={EXPECTED_CANONICAL_CROSS_BORDER}")
    if metadata_corrections != expected_metadata_corrections:
        failures.append(f"rank1 canonical metadata correction count differs from final-export metadata: observed={metadata_corrections} expected={expected_metadata_corrections}")

    route_parity_pass = (
        bool(same_graph_identity.all()) and not same_graph_delta.isna().any() and not (same_graph_delta > DISTANCE_TOLERANCE_M).any()
        and bool(canonical_identity.all()) and not canonical_delta.isna().any() and not (canonical_delta > DISTANCE_TOLERANCE_M).any()
    )
    if route_parity_pass:
        canonical_anchor = canonical_complete.set_index("mesh_id")
        for idx in candidates.index[candidates["candidate_rank"].eq(1)]:
            canonical_row = canonical_anchor.loc[str(candidates.at[idx, "mesh_id"])]
            candidates.at[idx, "shelter_municipality_code"] = canonical_row["shelter_municipality_code"]
            candidates.at[idx, "cross_border"] = bool(canonical_row["cross_border"])

    anchored_rank1 = candidates.loc[candidates["candidate_rank"].eq(1)]
    anchored_cross = int(_as_bool(anchored_rank1["cross_border"]).sum()) if len(anchored_rank1) else 0
    if anchored_cross != EXPECTED_CANONICAL_CROSS_BORDER: failures.append(f"anchored rank1 cross-border routes={anchored_cross} expected={EXPECTED_CANONICAL_CROSS_BORDER}")

    counts = status["candidate_count"].fillna(0).astype(int)
    qa = {
        "step": "STEP 8B - candidate routing double-parity release gate",
        "target_meshes": int(len(status)), "complete_candidate_meshes": int(complete_status.sum()),
        "route_unavailable": int((~complete_status).sum()), "candidate_limit": int(candidate_limit),
        "candidate_route_rows": int(len(candidates)), "rank1_rows": int(len(rank1)),
        "same_graph_rank1_identity_matches": int(same_graph_identity.sum()) if len(same_graph_compare) else 0,
        "same_graph_rank1_max_distance_delta_m": same_graph_max_delta,
        "same_graph_source_cross_border_routes": same_graph_cross,
        "canonical_rank1_identity_matches": int(canonical_identity.sum()) if len(canonical_compare) else 0,
        "canonical_rank1_max_distance_delta_m": canonical_max_delta,
        "canonical_rank1_cross_border_routes": canonical_cross,
        "canonical_cross_border_metadata_corrections": metadata_corrections,
        "canonical_expected_cross_border_metadata_corrections": expected_metadata_corrections,
        "anchored_rank1_cross_border_routes": anchored_cross,
        "canonical_anchor_applied": bool(route_parity_pass),
        "canonical_analysis_version": canonical_metadata.get("analysis_version"),
        "canonical_source_sha": canonical_metadata.get("analysis_source_sha"),
        "canonical_source_workflow_run_id": canonical_metadata.get("source_workflow_run_id"),
        "meshes_with_at_least_2_candidates": int(counts.ge(2).sum()),
        "meshes_with_at_least_5_candidates": int(counts.ge(5).sum()),
        "meshes_reaching_candidate_limit": int(counts.eq(candidate_limit).sum()),
        "mean_candidate_count_complete": float(counts[complete_status].mean()) if complete_status.any() else 0.0,
        "candidate_capacity_filtering": False, "canonical_step2_modified": False,
        "release_gate": "PASS" if not failures else "FAIL", "failures": failures,
    }
    return candidates.sort_values(["mesh_id", "candidate_rank"]).reset_index(drop=True), status.sort_values("mesh_id").reset_index(drop=True), qa, failures


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input-dir", type=pathlib.Path, required=True); p.add_argument("--baseline-routes-csv", type=pathlib.Path, required=True)
    p.add_argument("--canonical-public-data-root", type=pathlib.Path, required=True)
    p.add_argument("--out-candidates-csv", type=pathlib.Path, required=True); p.add_argument("--out-status-csv", type=pathlib.Path, required=True); p.add_argument("--out-qa", type=pathlib.Path, required=True)
    p.add_argument("--expected-rows", type=int, default=EXPECTED_TARGET_ROWS); p.add_argument("--expected-complete", type=int, default=EXPECTED_COMPLETE_ROUTES); p.add_argument("--candidate-limit", type=int, default=10)
    args = p.parse_args()
    candidates = _read_many(args.input_dir, "candidates-*.csv", dtype={"mesh_id": str, "municipality_code": str, "shelter_common_id": str})
    status = _read_many(args.input_dir, "candidate-status-*.csv", dtype={"mesh_id": str, "municipality_code": str, "rank1_shelter_common_id": str})
    baseline = pd.read_csv(args.baseline_routes_csv, encoding="utf-8-sig", dtype={"mesh_id": str, "municipality_code": str, "selected_shelter_common_id": str})
    canonical_routes, canonical_metadata = load_canonical_routes(args.canonical_public_data_root)
    c, s, qa, failures = aggregate_step8_candidates(candidates, status, baseline, canonical_routes, canonical_metadata, expected_rows=args.expected_rows, expected_complete=args.expected_complete, candidate_limit=args.candidate_limit)
    args.out_candidates_csv.parent.mkdir(parents=True, exist_ok=True); args.out_status_csv.parent.mkdir(parents=True, exist_ok=True); args.out_qa.parent.mkdir(parents=True, exist_ok=True)
    c.to_csv(args.out_candidates_csv, index=False, encoding="utf-8-sig"); s.to_csv(args.out_status_csv, index=False, encoding="utf-8-sig")
    args.out_qa.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8"); print(json.dumps(qa, ensure_ascii=False, indent=2))
    if failures: raise SystemExit("STEP 8B release gate failed: " + "; ".join(failures))


if __name__ == "__main__": main()
