#!/usr/bin/env python3
"""STEP 3: recalculate tsunami exposure for corrected STEP 2 walking routes.

The input is the 1,090-row STEP 2 route table.  Only rows with
``route_status == complete`` are sampled against the official GSI tsunami
raster.  Routing failures remain explicit rows with null exposure metrics.

``route_inundation_ratio`` is an exposure ratio along the stored OSM route
geometry, not a road-failure probability.  The stored network geometry excludes
the off-network mesh-origin connector and the shelter connector, so those
connectors are not included in this exposure denominator.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import time
from dataclasses import dataclass, field

import pandas as pd
from PIL import Image
from pyproj import Geod

from build_tsunami_exposure import PALETTE, Z, fetch_tile, nearest_depth, tile_xy

GEOD = Geod(ellps="WGS84")
EXPOSURE_COLUMNS = [
    "route_network_geometry_distance_m",
    "route_inundation_distance_m",
    "route_inundation_ratio",
    "route_inundation_ratio_classified",
    "route_tsunami_sample_count",
    "route_inundated_sample_count",
    "route_tile_absent_sample_count",
    "route_unclassified_sample_count",
    "route_unknown_distance_m",
    "route_unknown_ratio",
    "route_classified_coverage_ratio",
    "route_max_depth_class",
    "route_inundated_segments",
    "route_inundated_segment_count",
    "route_exposure_status",
]


def route_distance(points: list[list[float]]) -> float:
    total = 0.0
    for first, second in zip(points, points[1:]):
        total += max(0.0, float(GEOD.inv(first[0], first[1], second[0], second[1])[2]))
    return total


def route_coordinates_for_row(row: pd.Series) -> list[list[float]]:
    """Prefer corrected STEP 2 geometry while retaining legacy compatibility."""
    value = row.get("route_network_coordinates")
    if value is None or (isinstance(value, float) and math.isnan(value)) or value == "":
        value = row.get("route_coordinates")
    if value is None or (isinstance(value, float) and math.isnan(value)) or value == "":
        return []
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        return []
    points: list[list[float]] = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return []
        lon = float(point[0])
        lat = float(point[1])
        if not math.isfinite(lon) or not math.isfinite(lat):
            return []
        points.append([lon, lat])
    return points


def interpolate(first: list[float], azimuth: float, distance_m: float, fraction: float) -> list[float]:
    if fraction <= 0:
        return [float(first[0]), float(first[1])]
    lon, lat, _ = GEOD.fwd(float(first[0]), float(first[1]), azimuth, distance_m * fraction)
    return [float(lon), float(lat)]


def same_point(a: list[float], b: list[float], tolerance: float = 1e-9) -> bool:
    return abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance


@dataclass
class TileStore:
    cache: pathlib.Path
    retries: int = 3
    images: dict[tuple[int, int], Image.Image] = field(default_factory=dict)
    statuses: dict[tuple[int, int], str] = field(default_factory=dict)

    def get(self, key: tuple[int, int]) -> tuple[Image.Image, str]:
        if key in self.images:
            return self.images[key], self.statuses[key]
        x, y = key
        missing = self.cache / f"{Z}_{x}_{y}.missing"
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                image = fetch_tile(key, self.cache)
                status = "absent" if missing.exists() else "present"
                self.images[key] = image
                self.statuses[key] = status
                return image, status
            except Exception as exc:  # noqa: BLE001 - retry transient source errors
                last_error = exc
                if attempt < self.retries:
                    time.sleep(attempt * 2)
        assert last_error is not None
        raise last_error


def append_inundated_segment(
    segments: list[dict[str, object]], start: list[float], end: list[float], depth: int
) -> None:
    if segments:
        previous = segments[-1]
        coordinates = previous["coordinates"]
        if previous["depth_class"] == depth and same_point(coordinates[-1], start):
            coordinates.append(end)
            return
    segments.append({"depth_class": int(depth), "coordinates": [start, end]})


def empty_exposure(status: str) -> dict[str, object]:
    return {
        "route_network_geometry_distance_m": None,
        "route_inundation_distance_m": None,
        "route_inundation_ratio": None,
        "route_inundation_ratio_classified": None,
        "route_tsunami_sample_count": 0,
        "route_inundated_sample_count": 0,
        "route_tile_absent_sample_count": 0,
        "route_unclassified_sample_count": 0,
        "route_unknown_distance_m": None,
        "route_unknown_ratio": None,
        "route_classified_coverage_ratio": None,
        "route_max_depth_class": None,
        "route_inundated_segments": "[]",
        "route_inundated_segment_count": 0,
        "route_exposure_status": status,
    }


def calculate_route(
    points: list[list[float]], tile_store: TileStore, sample_spacing_m: float
) -> dict[str, object]:
    total_distance = route_distance(points)
    if len(points) < 2:
        return empty_exposure("no_geometry") | {"route_network_geometry_distance_m": total_distance}
    if total_distance <= 0:
        return empty_exposure("zero_length") | {"route_network_geometry_distance_m": total_distance}

    inundated_distance = 0.0
    unknown_distance = 0.0
    sample_count = 0
    inundated_samples = 0
    tile_absent_samples = 0
    unclassified_samples = 0
    depths: list[int] = []
    inundated_segments: list[dict[str, object]] = []

    for first, second in zip(points, points[1:]):
        lon1, lat1 = float(first[0]), float(first[1])
        lon2, lat2 = float(second[0]), float(second[1])
        azimuth, _, segment_distance = GEOD.inv(lon1, lat1, lon2, lat2)
        segment_distance = max(0.0, float(segment_distance))
        if segment_distance <= 0:
            continue
        count = max(1, math.ceil(segment_distance / sample_spacing_m))
        sample_length = segment_distance / count
        for index in range(count):
            start = interpolate(first, azimuth, segment_distance, index / count)
            end = interpolate(first, azimuth, segment_distance, (index + 1) / count)
            midpoint = interpolate(first, azimuth, segment_distance, (index + 0.5) / count)
            x, y, px, py = tile_xy(midpoint[0], midpoint[1])
            image, tile_status = tile_store.get((x, y))
            sample_count += 1
            if tile_status == "absent":
                tile_absent_samples += 1
                unknown_distance += sample_length
                continue
            rgba = image.getpixel((px, py))
            if rgba[3] < 128:
                continue
            depth = nearest_depth(rgba[:3])
            if depth is None:
                unclassified_samples += 1
                unknown_distance += sample_length
                continue
            inundated_samples += 1
            inundated_distance += sample_length
            depths.append(int(depth))
            append_inundated_segment(inundated_segments, start, end, int(depth))

    ratio = min(1.0, max(0.0, inundated_distance / total_distance))
    unknown_ratio = min(1.0, max(0.0, unknown_distance / total_distance))
    classified_distance = max(0.0, total_distance - unknown_distance)
    classified_ratio = (
        min(1.0, max(0.0, inundated_distance / classified_distance))
        if classified_distance > 0
        else None
    )
    return {
        "route_network_geometry_distance_m": total_distance,
        "route_inundation_distance_m": inundated_distance,
        "route_inundation_ratio": ratio,
        "route_inundation_ratio_classified": classified_ratio,
        "route_tsunami_sample_count": sample_count,
        "route_inundated_sample_count": inundated_samples,
        "route_tile_absent_sample_count": tile_absent_samples,
        "route_unclassified_sample_count": unclassified_samples,
        "route_unknown_distance_m": unknown_distance,
        "route_unknown_ratio": unknown_ratio,
        "route_classified_coverage_ratio": 1.0 - unknown_ratio,
        "route_max_depth_class": max(depths) if depths else None,
        "route_inundated_segments": json.dumps(inundated_segments, ensure_ascii=False, separators=(",", ":")),
        "route_inundated_segment_count": len(inundated_segments),
        "route_exposure_status": "complete",
    }


def validate_result(
    result: pd.DataFrame, expected_rows: int, expected_complete_routes: int
) -> tuple[list[str], dict[str, object]]:
    failures: list[str] = []
    if len(result) != expected_rows:
        failures.append(f"row count={len(result)} expected={expected_rows}")
    if result["mesh_id"].astype(str).nunique() != len(result):
        failures.append("duplicate mesh_id in STEP 3 output")

    source_complete = result["route_status"].astype(str).eq("complete")
    exposure_complete = result["route_exposure_status"].astype(str).eq("complete")
    if int(source_complete.sum()) != expected_complete_routes:
        failures.append(
            f"source complete routes={int(source_complete.sum())} expected={expected_complete_routes}"
        )
    if int(exposure_complete.sum()) != expected_complete_routes:
        failures.append(
            f"complete exposure routes={int(exposure_complete.sum())} expected={expected_complete_routes}"
        )
    if (source_complete != exposure_complete).any():
        failures.append("route_status complete set differs from route_exposure_status complete set")

    errors = result["route_exposure_status"].astype(str).str.startswith("error:")
    if errors.any():
        failures.append(f"row-level exposure errors={int(errors.sum())}")

    complete = result.loc[exposure_complete].copy()
    for column in [
        "route_network_geometry_distance_m",
        "route_inundation_distance_m",
        "route_inundation_ratio",
        "route_tsunami_sample_count",
        "route_unknown_ratio",
        "route_classified_coverage_ratio",
    ]:
        numeric = pd.to_numeric(complete[column], errors="coerce")
        if numeric.isna().any():
            failures.append(f"complete exposure missing numeric field: {column}")
    if len(complete):
        ratios = pd.to_numeric(complete["route_inundation_ratio"], errors="coerce")
        unknown = pd.to_numeric(complete["route_unknown_ratio"], errors="coerce")
        coverage = pd.to_numeric(complete["route_classified_coverage_ratio"], errors="coerce")
        if (~ratios.between(0, 1)).any():
            failures.append("route_inundation_ratio outside [0,1]")
        if (~unknown.between(0, 1)).any():
            failures.append("route_unknown_ratio outside [0,1]")
        if ((unknown + coverage - 1.0).abs() > 1e-9).any():
            failures.append("unknown ratio + classified coverage != 1")
        if (pd.to_numeric(complete["route_tsunami_sample_count"], errors="coerce") <= 0).any():
            failures.append("complete exposure route has zero samples")

    unavailable = result.loc[~source_complete]
    if unavailable["route_exposure_status"].astype(str).ne("route_unavailable").any():
        failures.append("non-complete STEP 2 route not preserved as route_unavailable")
    if pd.to_numeric(unavailable["route_inundation_ratio"], errors="coerce").notna().any():
        failures.append("unavailable route has non-null route_inundation_ratio")

    ratios = pd.to_numeric(complete["route_inundation_ratio"], errors="coerce") if len(complete) else pd.Series(dtype=float)
    unknown = pd.to_numeric(complete["route_unknown_ratio"], errors="coerce") if len(complete) else pd.Series(dtype=float)
    qa = {
        "step": "STEP 3 - corrected route tsunami exposure",
        "input_route_count": int(len(result)),
        "source_route_status_counts": result["route_status"].value_counts().to_dict(),
        "complete_exposure_routes": int(exposure_complete.sum()),
        "unavailable_routes": int((result["route_exposure_status"] == "route_unavailable").sum()),
        "error_routes": int(errors.sum()),
        "routes_with_inundation": int(ratios.gt(0).sum()) if len(ratios) else 0,
        "routes_with_tile_absence": int(pd.to_numeric(complete["route_tile_absent_sample_count"], errors="coerce").gt(0).sum()) if len(complete) else 0,
        "routes_with_unclassified_pixels": int(pd.to_numeric(complete["route_unclassified_sample_count"], errors="coerce").gt(0).sum()) if len(complete) else 0,
        "max_unknown_ratio": float(unknown.max()) if len(unknown) else None,
        "inundation_ratio": {
            "p50": float(ratios.quantile(0.50)) if len(ratios) else None,
            "p90": float(ratios.quantile(0.90)) if len(ratios) else None,
            "p95": float(ratios.quantile(0.95)) if len(ratios) else None,
            "max": float(ratios.max()) if len(ratios) else None,
        },
        "sampling": {
            "tile_source": "GSI Hazard Map Portal Ehime tsunami raster",
            "tile_zoom": Z,
            "palette_classes": sorted(set(PALETTE.values())),
            "denominator": "stored STEP 2 OSM route network geometry only",
            "offnetwork_origin_connector_included": False,
            "shelter_connector_included": False,
        },
        "interpretation": "route_inundation_ratio is tsunami inundation exposure along the pedestrian route, not a probability of road failure",
        "release_gate": {"pass": not failures, "failures": failures},
    }
    return failures, qa


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--routes-csv", type=pathlib.Path, required=True)
    parser.add_argument("--cache", type=pathlib.Path, required=True)
    parser.add_argument("--out-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-qa", type=pathlib.Path, required=True)
    parser.add_argument("--sample-spacing-m", type=float, default=25.0)
    parser.add_argument("--expected-rows", type=int, default=1090)
    parser.add_argument("--expected-complete-routes", type=int, default=1062)
    args = parser.parse_args()
    if args.sample_spacing_m <= 0:
        parser.error("--sample-spacing-m must be positive")

    routes = pd.read_csv(
        args.routes_csv,
        encoding="utf-8-sig",
        dtype={"mesh_id": str, "municipality_code": str},
    )
    tile_store = TileStore(args.cache)
    exposure_rows: list[dict[str, object]] = []
    for index, row in routes.iterrows():
        base = {"mesh_id": str(row["mesh_id"])}
        if str(row.get("route_status")) != "complete":
            base.update(empty_exposure("route_unavailable"))
        else:
            try:
                points = route_coordinates_for_row(row)
                base.update(calculate_route(points, tile_store, args.sample_spacing_m))
            except Exception as exc:  # noqa: BLE001 - preserve row-level evidence for gate
                base.update(empty_exposure(f"error:{type(exc).__name__}"))
        exposure_rows.append(base)
        if (index + 1) % 200 == 0:
            print(f"processed {index + 1}/{len(routes)} routes", flush=True)

    exposure = pd.DataFrame(exposure_rows)
    result = routes.drop(columns=EXPOSURE_COLUMNS + ["route_geometry_distance_m"], errors="ignore").merge(
        exposure, on="mesh_id", how="left", validate="one_to_one"
    )
    failures, qa = validate_result(result, args.expected_rows, args.expected_complete_routes)
    qa["sample_spacing_m"] = args.sample_spacing_m
    qa["unique_tiles_loaded"] = len(tile_store.images)
    qa["tile_status_counts"] = {
        status: sum(1 for value in tile_store.statuses.values() if value == status)
        for status in sorted(set(tile_store.statuses.values()))
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
