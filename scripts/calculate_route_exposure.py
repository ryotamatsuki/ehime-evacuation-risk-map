#!/usr/bin/env python3
"""Estimate tsunami exposure along each precomputed pedestrian route.

The route geometry is sampled against the official GSI tsunami raster palette.
The result is an exposure measure (road distance in the inundation raster /
route distance), not a probability that a road will be destroyed.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib

import pandas as pd
from pyproj import Geod

from build_tsunami_exposure import fetch_tile, nearest_depth, tile_xy


GEOD = Geod(ellps="WGS84")


def route_distance(points: list[list[float]]) -> float:
    total = 0.0
    for first, second in zip(points, points[1:]):
        total += float(GEOD.inv(first[0], first[1], second[0], second[1])[2])
    return total


def calculate_route(
    points: list[list[float]],
    tiles: dict[tuple[int, int], object],
    cache: pathlib.Path,
    sample_spacing_m: float,
) -> dict[str, object]:
    total_distance = route_distance(points)
    if len(points) < 2:
        return {
            "route_geometry_distance_m": total_distance,
            "route_inundation_distance_m": None,
            "route_inundation_ratio": None,
            "route_tsunami_sample_count": 0,
            "route_inundated_sample_count": 0,
            "route_max_depth_class": None,
            "route_exposure_status": "no_geometry",
        }
    if total_distance <= 0:
        return {
            "route_geometry_distance_m": total_distance,
            "route_inundation_distance_m": None,
            "route_inundation_ratio": None,
            "route_tsunami_sample_count": 0,
            "route_inundated_sample_count": 0,
            "route_max_depth_class": None,
            "route_exposure_status": "zero_length",
        }

    inundated_distance = 0.0
    sample_count = 0
    inundated_samples = 0
    depths: list[int] = []

    for first, second in zip(points, points[1:]):
        lon1, lat1 = float(first[0]), float(first[1])
        lon2, lat2 = float(second[0]), float(second[1])
        azimuth, _, segment_distance = GEOD.inv(lon1, lat1, lon2, lat2)
        segment_distance = float(segment_distance)
        if segment_distance <= 0:
            continue
        count = max(1, math.ceil(segment_distance / sample_spacing_m))
        sample_length = segment_distance / count
        for index in range(count):
            fraction = (index + 0.5) / count
            lon, lat, _ = GEOD.fwd(lon1, lat1, azimuth, segment_distance * fraction)
            x, y, px, py = tile_xy(float(lon), float(lat))
            key = (x, y)
            if key not in tiles:
                tiles[key] = fetch_tile(key, cache)
            rgba = tiles[key].getpixel((px, py))
            sample_count += 1
            if rgba[3] < 128:
                continue
            depth = nearest_depth(rgba[:3])
            if depth is None:
                continue
            inundated_samples += 1
            inundated_distance += sample_length
            depths.append(depth)

    ratio = min(1.0, max(0.0, inundated_distance / total_distance))
    return {
        "route_geometry_distance_m": total_distance,
        "route_inundation_distance_m": inundated_distance,
        "route_inundation_ratio": ratio,
        "route_tsunami_sample_count": sample_count,
        "route_inundated_sample_count": inundated_samples,
        "route_max_depth_class": max(depths) if depths else None,
        "route_exposure_status": "complete",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--routes-csv", type=pathlib.Path, required=True)
    parser.add_argument("--cache", type=pathlib.Path, required=True)
    parser.add_argument("--out-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-qa", type=pathlib.Path, required=True)
    parser.add_argument("--sample-spacing-m", type=float, default=25.0)
    args = parser.parse_args()
    if args.sample_spacing_m <= 0:
        parser.error("--sample-spacing-m must be positive")

    routes = pd.read_csv(args.routes_csv, encoding="utf-8-sig", dtype={"mesh_id": str})
    tiles: dict[tuple[int, int], object] = {}
    rows: list[dict[str, object]] = []
    for index, row in routes.iterrows():
        base = {"mesh_id": row["mesh_id"]}
        if row.get("route_status") != "complete" or pd.isna(row.get("route_coordinates")):
            base.update({
                "route_geometry_distance_m": None,
                "route_inundation_distance_m": None,
                "route_inundation_ratio": None,
                "route_tsunami_sample_count": 0,
                "route_inundated_sample_count": 0,
                "route_max_depth_class": None,
                "route_exposure_status": "route_unavailable",
            })
        else:
            try:
                points = json.loads(row["route_coordinates"])
                base.update(calculate_route(points, tiles, args.cache, args.sample_spacing_m))
            except Exception as exc:  # noqa: BLE001 - preserve row-level QA
                base.update({
                    "route_geometry_distance_m": None,
                    "route_inundation_distance_m": None,
                    "route_inundation_ratio": None,
                    "route_tsunami_sample_count": 0,
                    "route_inundated_sample_count": 0,
                    "route_max_depth_class": None,
                    "route_exposure_status": f"error:{type(exc).__name__}",
                })
        rows.append(base)
        if (index + 1) % 500 == 0:
            print(f"processed {index + 1}/{len(routes)} routes", flush=True)

    exposure = pd.DataFrame(rows)
    result = routes.drop(columns=[
        "route_geometry_distance_m", "route_inundation_distance_m",
        "route_inundation_ratio", "route_tsunami_sample_count",
        "route_inundated_sample_count", "route_max_depth_class",
        "route_exposure_status",
    ], errors="ignore").merge(exposure, on="mesh_id", how="left", validate="one_to_one")
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.out_csv, index=False, encoding="utf-8-sig")

    qa = {
        "route_count": len(result),
        "complete_exposure_routes": int((result["route_exposure_status"] == "complete").sum()),
        "unavailable_routes": int((result["route_exposure_status"] == "route_unavailable").sum()),
        "error_routes": int(result["route_exposure_status"].astype(str).str.startswith("error:").sum()),
        "sample_spacing_m": args.sample_spacing_m,
        "denominator": "route_geometry_distance_m computed from route polyline coordinates",
        "interpretation": "route_inundation_ratio is tsunami inundation exposure along the pedestrian route, not a probability of road failure",
    }
    args.out_qa.parent.mkdir(parents=True, exist_ok=True)
    args.out_qa.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False))


if __name__ == "__main__":
    main()
