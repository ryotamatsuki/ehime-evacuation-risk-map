#!/usr/bin/env python3
"""Reusable tsunami-raster sampling primitives for Analysis Core STEP 3.

This module intentionally contains no CLI or release-gate orchestration.  The
production STEP 3 entrypoint is ``calculate_route_exposure_step3.py``.  Keeping
the raster sampler separate prevents a retired analysis entrypoint from being
mistaken for a second production pipeline while preserving independent unit
coverage of tile absence, unclassified pixels, exposure ratios and inundated
segments.
"""

from __future__ import annotations

import json
import math
import pathlib
import time
from dataclasses import dataclass, field

import pandas as pd
from PIL import Image
from pyproj import Geod

from build_tsunami_exposure import Z, fetch_tile, nearest_depth, tile_xy

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
    """Prefer corrected STEP 2 geometry while retaining read compatibility."""
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
