#!/usr/bin/env python3
"""STEP 3 production wrapper for total modeled evacuation-route exposure.

The STEP 2 table stores the verified OSM network geometry, while walking
``total_walking_distance_m`` also contains an origin access connector and a
shelter connector.  Some valid routes have zero OSM path length because both
ends attach to the same OSM node.  This wrapper therefore constructs a
comparable modeled route for every complete STEP 2 row:

    500 m mesh centroid -> stored OSM route geometry -> selected shelter

The two off-network end connectors are straight analytical connectors.  They
are not claimed to be verified pedestrian roads.  Edge-based origin access
inside the OSM network follows the stored OSM edge geometry.  The underlying
GSI raster sampler, including explicit absent-tile/unknown handling, lives in
``route_exposure_sampler.py`` and has no separate production CLI.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib

import pandas as pd

from mesh500 import mesh_centroid
from route_exposure_sampler import (
    EXPOSURE_COLUMNS,
    TileStore,
    calculate_route,
    empty_exposure,
    route_coordinates_for_row,
)

MODELED_DISTANCE_FIELD = "route_modeled_geometry_distance_m"
DISTANCE_ABSOLUTE_OUTLIER_M = 100.0
DISTANCE_RELATIVE_OUTLIER = 0.05


def same_point(a: list[float], b: list[float], tolerance: float = 1e-9) -> bool:
    return abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance


def append_point(points: list[list[float]], point: list[float]) -> None:
    if not points or not same_point(points[-1], point):
        points.append([float(point[0]), float(point[1])])


def load_routes(path: pathlib.Path) -> pd.DataFrame:
    """Load STEP 2 routes without destroying locally assigned leading-zero IDs."""
    return pd.read_csv(
        path,
        encoding="utf-8-sig",
        dtype={
            "mesh_id": str,
            "municipality_code": str,
            "selected_shelter_common_id": str,
            "shelter_municipality_code": str,
        },
    )


def shelter_lookup(path: pathlib.Path) -> dict[str, tuple[float, float]]:
    shelters = pd.read_csv(path, encoding="utf-8-sig", dtype={"common_id": str})
    shelters["latitude"] = pd.to_numeric(shelters["latitude"], errors="coerce")
    shelters["longitude"] = pd.to_numeric(shelters["longitude"], errors="coerce")
    result: dict[str, tuple[float, float]] = {}
    for row in shelters.dropna(subset=["latitude", "longitude"]).itertuples(index=False):
        common_id = str(row.common_id or "")
        if common_id:
            result[common_id] = (float(row.latitude), float(row.longitude))
    return result


def build_modeled_route(
    row: pd.Series,
    shelters: dict[str, tuple[float, float]],
) -> list[list[float]]:
    """Return centroid -> OSM route -> shelter with explicit end connectors."""
    lon, lat = mesh_centroid(str(row["mesh_id"]))
    points: list[list[float]] = [[float(lon), float(lat)]]
    for point in route_coordinates_for_row(row):
        append_point(points, point)

    common_id = str(row.get("selected_shelter_common_id") or "")
    if common_id not in shelters:
        raise KeyError(f"selected shelter coordinates missing: {common_id}")
    shelter_lat, shelter_lon = shelters[common_id]
    append_point(points, [shelter_lon, shelter_lat])
    return points


def modeled_fields(exposure: dict[str, object]) -> dict[str, object]:
    """Rename the sampler's geometry-length field to its correct STEP 3 meaning."""
    result = dict(exposure)
    result[MODELED_DISTANCE_FIELD] = result.pop("route_network_geometry_distance_m", None)
    return result


def empty_modeled(status: str) -> dict[str, object]:
    return modeled_fields(empty_exposure(status))


def validate(
    result: pd.DataFrame,
    expected_rows: int,
    expected_complete_routes: int,
) -> tuple[list[str], dict[str, object]]:
    failures: list[str] = []
    if len(result) != expected_rows:
        failures.append(f"row count={len(result)} expected={expected_rows}")
    if result["mesh_id"].astype(str).nunique() != len(result):
        failures.append("duplicate mesh_id")

    source_complete = result["route_status"].astype(str).eq("complete")
    exposure_complete = result["route_exposure_status"].astype(str).eq("complete")
    if int(source_complete.sum()) != expected_complete_routes:
        failures.append(
            f"source complete routes={int(source_complete.sum())} expected={expected_complete_routes}"
        )
    if int(exposure_complete.sum()) != expected_complete_routes:
        failures.append(
            f"complete modeled exposures={int(exposure_complete.sum())} expected={expected_complete_routes}"
        )
    if (source_complete != exposure_complete).any():
        failures.append("STEP 2 complete set differs from STEP 3 modeled exposure set")

    errors = result["route_exposure_status"].astype(str).str.startswith("error:")
    if errors.any():
        failures.append(f"row-level exposure errors={int(errors.sum())}")

    complete = result.loc[exposure_complete].copy()
    numeric_columns = [
        MODELED_DISTANCE_FIELD,
        "route_inundation_distance_m",
        "route_inundation_ratio",
        "route_tsunami_sample_count",
        "route_unknown_ratio",
        "route_classified_coverage_ratio",
    ]
    for column in numeric_columns:
        values = pd.to_numeric(complete[column], errors="coerce")
        if values.isna().any():
            failures.append(f"complete exposure missing numeric field: {column}")

    if len(complete):
        ratios = pd.to_numeric(complete["route_inundation_ratio"], errors="coerce")
        unknown = pd.to_numeric(complete["route_unknown_ratio"], errors="coerce")
        coverage = pd.to_numeric(complete["route_classified_coverage_ratio"], errors="coerce")
        samples = pd.to_numeric(complete["route_tsunami_sample_count"], errors="coerce")
        geometry = pd.to_numeric(complete[MODELED_DISTANCE_FIELD], errors="coerce")
        if (~ratios.between(0, 1)).any():
            failures.append("route_inundation_ratio outside [0,1]")
        if (~unknown.between(0, 1)).any():
            failures.append("route_unknown_ratio outside [0,1]")
        if ((unknown + coverage - 1.0).abs() > 1e-9).any():
            failures.append("unknown ratio + classified coverage != 1")
        if samples.le(0).any():
            failures.append("complete route has zero tsunami samples")
        if geometry.le(0).any():
            failures.append("complete route has zero modeled geometry length")
    else:
        ratios = pd.Series(dtype=float)
        unknown = pd.Series(dtype=float)

    unavailable = result.loc[~source_complete]
    if unavailable["route_exposure_status"].astype(str).ne("route_unavailable").any():
        failures.append("non-complete STEP 2 route not preserved as route_unavailable")
    if pd.to_numeric(unavailable["route_inundation_ratio"], errors="coerce").notna().any():
        failures.append("unavailable route has non-null inundation ratio")

    network_path = pd.to_numeric(complete["network_path_distance_m"], errors="coerce")
    modeled_distance = pd.to_numeric(complete[MODELED_DISTANCE_FIELD], errors="coerce")
    step2_distance = pd.to_numeric(complete["total_walking_distance_m"], errors="coerce")
    residual = (modeled_distance - step2_distance).abs()
    relative = residual / step2_distance.where(step2_distance > 0)
    distance_outlier = residual.gt(DISTANCE_ABSOLUTE_OUTLIER_M) & relative.gt(
        DISTANCE_RELATIVE_OUTLIER
    )
    distance_outlier_ids = complete.loc[distance_outlier, "mesh_id"].astype(str).tolist()
    if distance_outlier_ids:
        failures.append(
            "modeled route geometry materially inconsistent with STEP 2 distance: "
            f"{len(distance_outlier_ids)} routes"
        )

    qa = {
        "step": "STEP 3 - modeled total evacuation-route tsunami exposure",
        "input_route_count": int(len(result)),
        "source_route_status_counts": result["route_status"].value_counts().to_dict(),
        "complete_exposure_routes": int(exposure_complete.sum()),
        "unavailable_routes": int((result["route_exposure_status"] == "route_unavailable").sum()),
        "error_routes": int(errors.sum()),
        "zero_network_path_complete_routes": int(network_path.fillna(math.inf).le(1e-9).sum()),
        "routes_with_inundation": int(ratios.gt(0).sum()) if len(ratios) else 0,
        "routes_with_tile_absence": int(
            pd.to_numeric(complete["route_tile_absent_sample_count"], errors="coerce").gt(0).sum()
        ) if len(complete) else 0,
        "routes_with_unclassified_pixels": int(
            pd.to_numeric(complete["route_unclassified_sample_count"], errors="coerce").gt(0).sum()
        ) if len(complete) else 0,
        "max_unknown_ratio": float(unknown.max()) if len(unknown) else None,
        "inundation_ratio": {
            "p50": float(ratios.quantile(0.50)) if len(ratios) else None,
            "p90": float(ratios.quantile(0.90)) if len(ratios) else None,
            "p95": float(ratios.quantile(0.95)) if len(ratios) else None,
            "max": float(ratios.max()) if len(ratios) else None,
        },
        "modeled_vs_step2_distance": {
            "absolute_residual_p95_m": float(residual.quantile(0.95)) if len(residual) else None,
            "absolute_residual_max_m": float(residual.max()) if len(residual) else None,
            "relative_residual_p95": float(relative.quantile(0.95)) if len(relative.dropna()) else None,
            "relative_residual_max": float(relative.max()) if len(relative.dropna()) else None,
            "material_outlier_definition": (
                f"absolute residual > {DISTANCE_ABSOLUTE_OUTLIER_M:g} m AND "
                f"relative residual > {DISTANCE_RELATIVE_OUTLIER:.0%}"
            ),
            "material_outlier_count": int(distance_outlier.sum()),
            "material_outlier_mesh_ids": distance_outlier_ids,
        },
        "geometry": {
            "primary_denominator": "mesh centroid -> stored OSM route -> selected shelter",
            "offnetwork_origin_connector": "straight analytical connector to OSM projection/node",
            "edge_origin_access": "follows OSM edge geometry from projection to selected endpoint",
            "shelter_connector": "straight analytical connector",
            "connector_warning": "off-network connectors are analytical approximations, not verified pedestrian roads",
        },
        "interpretation": "route_inundation_ratio is modeled tsunami exposure, not road-failure probability",
        "release_gate": {"pass": not failures, "failures": failures},
    }
    return failures, qa


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--routes-csv", type=pathlib.Path, required=True)
    parser.add_argument("--shelters-csv", type=pathlib.Path, required=True)
    parser.add_argument("--cache", type=pathlib.Path, required=True)
    parser.add_argument("--out-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-qa", type=pathlib.Path, required=True)
    parser.add_argument("--sample-spacing-m", type=float, default=25.0)
    parser.add_argument("--expected-rows", type=int, default=1090)
    parser.add_argument("--expected-complete-routes", type=int, default=1062)
    args = parser.parse_args()
    if args.sample_spacing_m <= 0:
        parser.error("--sample-spacing-m must be positive")

    routes = load_routes(args.routes_csv)
    shelters = shelter_lookup(args.shelters_csv)
    tiles = TileStore(args.cache)
    rows: list[dict[str, object]] = []
    for index, row in routes.iterrows():
        output: dict[str, object] = {"mesh_id": str(row["mesh_id"])}
        if str(row.get("route_status")) != "complete":
            output.update(empty_modeled("route_unavailable"))
        else:
            try:
                points = build_modeled_route(row, shelters)
                output.update(modeled_fields(calculate_route(points, tiles, args.sample_spacing_m)))
            except Exception as exc:  # noqa: BLE001
                output.update(empty_modeled(f"error:{type(exc).__name__}"))
        rows.append(output)
        if (index + 1) % 200 == 0:
            print(f"processed {index + 1}/{len(routes)} routes", flush=True)

    exposure = pd.DataFrame(rows)
    drop_columns = EXPOSURE_COLUMNS + [MODELED_DISTANCE_FIELD, "route_geometry_distance_m"]
    result = routes.drop(columns=drop_columns, errors="ignore").merge(
        exposure, on="mesh_id", how="left", validate="one_to_one"
    )
    failures, qa = validate(result, args.expected_rows, args.expected_complete_routes)
    qa["sample_spacing_m"] = args.sample_spacing_m
    qa["unique_tiles_loaded"] = len(tiles.images)
    qa["tile_status_counts"] = {
        status: sum(1 for value in tiles.statuses.values() if value == status)
        for status in sorted(set(tiles.statuses.values()))
    }

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.out_csv, index=False, encoding="utf-8-sig")
    args.out_qa.parent.mkdir(parents=True, exist_ok=True)
    args.out_qa.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit("STEP 3 release gate failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
