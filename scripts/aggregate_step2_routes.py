#!/usr/bin/env python3
"""Aggregate and release-gate STEP 2 municipality route artifacts."""

from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np
import pandas as pd

from calculate_evacuation_routes_v2 import ALLOWED_STATUSES, MAX_SHELTER_CONNECTOR_M

EXPECTED_TARGET_COUNT = 1090


def read_route_files(input_dir: pathlib.Path) -> pd.DataFrame:
    files = sorted(input_dir.rglob("routes-*.csv"))
    if not files:
        raise RuntimeError(f"no routes-*.csv found under {input_dir}")
    frames: list[pd.DataFrame] = []
    route_dtypes = {
        "mesh_id": str,
        "municipality_code": str,
        "selected_shelter_common_id": str,
        "shelter_municipality_code": str,
    }
    for path in files:
        frame = pd.read_csv(path, encoding="utf-8-sig", dtype=route_dtypes)
        if not frame.empty:
            frame["_source_file"] = str(path)
            frames.append(frame)
    if not frames:
        raise RuntimeError("all route artifacts were empty")
    return pd.concat(frames, ignore_index=True)


def target_meshes(population_csv: pathlib.Path, exposure_csv: pathlib.Path) -> pd.DataFrame:
    population = pd.read_csv(
        population_csv,
        encoding="utf-8-sig",
        dtype={"mesh_id": str, "municipality_code": str},
    )
    exposure = pd.read_csv(exposure_csv, encoding="utf-8-sig", dtype={"mesh_id": str})
    exposure["tsunami_inundation_ratio"] = pd.to_numeric(
        exposure["tsunami_inundation_ratio"], errors="coerce"
    )
    merged = population[["mesh_id", "municipality_code", "municipality"]].merge(
        exposure[["mesh_id", "tsunami_inundation_ratio"]],
        on="mesh_id",
        how="inner",
        validate="one_to_one",
    )
    return merged[merged["tsunami_inundation_ratio"].gt(0)].copy()


def bool_true(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().eq("true")


def parse_expected_ids(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def validate_routes(
    routes: pd.DataFrame,
    targets: pd.DataFrame,
    expected_gap_ids: set[str],
) -> tuple[list[str], dict[str, object]]:
    failures: list[str] = []
    routes["mesh_id"] = routes["mesh_id"].astype(str)
    route_ids = set(routes["mesh_id"])
    target_ids = set(targets["mesh_id"].astype(str))

    if len(targets) != EXPECTED_TARGET_COUNT:
        failures.append(f"source target count is {len(targets)}, expected {EXPECTED_TARGET_COUNT}")
    if len(routes) != EXPECTED_TARGET_COUNT:
        failures.append(f"route row count is {len(routes)}, expected {EXPECTED_TARGET_COUNT}")
    if routes["mesh_id"].nunique() != len(routes):
        failures.append("duplicate mesh_id exists in STEP 2 routes")
    missing = sorted(target_ids - route_ids)
    extra = sorted(route_ids - target_ids)
    if missing:
        failures.append(f"missing target meshes={len(missing)}")
    if extra:
        failures.append(f"unexpected non-target meshes={len(extra)}")

    unknown_statuses = sorted(set(routes["route_status"].dropna()) - ALLOWED_STATUSES)
    if unknown_statuses:
        failures.append(f"unknown route statuses: {unknown_statuses}")

    complete = routes[routes["route_status"].eq("complete")].copy()
    required_complete = [
        "selected_shelter_common_id",
        "selected_shelter_name",
        "origin_access_distance_m",
        "network_path_distance_m",
        "shelter_connector_distance_m",
        "total_walking_distance_m",
        "route_network_coordinates",
    ]
    for column in required_complete:
        if column not in complete.columns or complete[column].isna().any():
            failures.append(f"complete route missing required field: {column}")

    for column in [
        "origin_access_distance_m",
        "network_path_distance_m",
        "shelter_connector_distance_m",
        "total_walking_distance_m",
    ]:
        if column in complete.columns:
            complete[column] = pd.to_numeric(complete[column], errors="coerce")
    if len(complete):
        formula = (
            complete["origin_access_distance_m"]
            + complete["network_path_distance_m"]
            + complete["shelter_connector_distance_m"]
        )
        residual = (formula - complete["total_walking_distance_m"]).abs()
        max_residual = float(residual.max())
        if residual.gt(1e-6).any():
            failures.append(f"distance formula mismatch; max residual={max_residual}")
        if complete["shelter_connector_distance_m"].gt(MAX_SHELTER_CONNECTOR_M + 1e-9).any():
            failures.append("complete route uses shelter connector >500 m")
        invalid_geometry = 0
        for value in complete["route_network_coordinates"]:
            try:
                coords = json.loads(value)
                if not isinstance(coords, list) or not coords:
                    invalid_geometry += 1
            except Exception:  # noqa: BLE001
                invalid_geometry += 1
        if invalid_geometry:
            failures.append(f"invalid complete route geometries={invalid_geometry}")
    else:
        max_residual = None

    actual_gap_ids = set(
        routes.loc[routes["route_status"].eq("network_coverage_gap"), "mesh_id"].astype(str)
    )
    if actual_gap_ids != expected_gap_ids:
        failures.append(
            f"network coverage gap IDs differ: actual={sorted(actual_gap_ids)} expected={sorted(expected_gap_ids)}"
        )

    cross_border_mask = bool_true(complete["cross_border"]) if len(complete) else pd.Series(dtype=bool)
    status_counts = routes["route_status"].value_counts().to_dict()
    distances = pd.to_numeric(complete["total_walking_distance_m"], errors="coerce") if len(complete) else pd.Series(dtype=float)
    quantiles = {}
    if len(distances.dropna()):
        for label, q in (("p50", 0.50), ("p90", 0.90), ("p95", 0.95), ("max", 1.0)):
            quantiles[label] = float(distances.quantile(q))

    qa = {
        "step": "STEP 2 - Cross-border mesh-to-shelter routing",
        "expected_target_count": EXPECTED_TARGET_COUNT,
        "source_target_count": int(len(targets)),
        "route_row_count": int(len(routes)),
        "unique_mesh_count": int(routes["mesh_id"].nunique()),
        "route_status_counts": status_counts,
        "complete_routes": int(len(complete)),
        "cross_border_complete_routes": int(cross_border_mask.sum()) if len(complete) else 0,
        "cross_border_complete_ratio": (
            float(cross_border_mask.mean()) if len(complete) else None
        ),
        "same_municipality_restriction": False,
        "connectors_accounted": True,
        "max_allowed_shelter_connector_m": MAX_SHELTER_CONNECTOR_M,
        "max_complete_shelter_connector_m": (
            float(complete["shelter_connector_distance_m"].max()) if len(complete) else None
        ),
        "distance_formula": "origin_access_distance_m + network_path_distance_m + shelter_connector_distance_m",
        "max_distance_formula_residual_m": max_residual,
        "total_walking_distance_m": quantiles,
        "expected_network_coverage_gap_ids": sorted(expected_gap_ids),
        "actual_network_coverage_gap_ids": sorted(actual_gap_ids),
        "missing_target_mesh_count": len(missing),
        "extra_non_target_mesh_count": len(extra),
        "release_gate": {
            "pass": not failures,
            "failures": failures,
        },
    }
    return failures, qa


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=pathlib.Path, required=True)
    parser.add_argument("--population-csv", type=pathlib.Path, required=True)
    parser.add_argument("--tsunami-exposure-csv", type=pathlib.Path, required=True)
    parser.add_argument("--expected-network-gap-mesh-ids", required=True)
    parser.add_argument("--out-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-qa", type=pathlib.Path, required=True)
    args = parser.parse_args()

    routes = read_route_files(args.input_dir)
    targets = target_meshes(args.population_csv, args.tsunami_exposure_csv)
    expected_gap_ids = parse_expected_ids(args.expected_network_gap_mesh_ids)
    routes = routes.sort_values(["municipality_code", "mesh_id"]).reset_index(drop=True)
    failures, qa = validate_routes(routes, targets, expected_gap_ids)

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    routes.drop(columns=["_source_file"], errors="ignore").to_csv(
        args.out_csv, index=False, encoding="utf-8-sig"
    )
    args.out_qa.parent.mkdir(parents=True, exist_ok=True)
    args.out_qa.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit("STEP 2 release gate failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
