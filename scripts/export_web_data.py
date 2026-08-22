#!/usr/bin/env python3
"""Export compact, browser-safe files from ETL outputs.

The web build is split by municipality so each asset remains small enough for
GitHub Pages. Raw downloads and cached raster tiles are never copied here.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import pathlib
import re
import unicodedata
from collections import defaultdict

import pandas as pd


def json_safe(value: object) -> object:
    if value is None:
        return None
    text = str(value)
    if text.strip().lower() in {"", "nan", "none", "nat"}:
        return None
    return value


def json_safe_record(record: dict[str, object]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in record.items():
        if isinstance(value, float) and math.isnan(value):
            output[key] = None
        elif key == "route_coordinates" and isinstance(value, str):
            try:
                output[key] = json.loads(value)
            except json.JSONDecodeError:
                output[key] = None
        else:
            output[key] = value.item() if hasattr(value, "item") else value
    return output


def parse_capacity_value(value: object) -> float | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    match = re.match(r"^([0-9][0-9,]*(?:\.[0-9]+)?)", text)
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    return number if number > 0 else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--population-geojson", type=pathlib.Path, required=True)
    parser.add_argument("--shelters-csv", type=pathlib.Path, required=True)
    parser.add_argument("--tsunami-csv", type=pathlib.Path)
    parser.add_argument("--risk-csv", type=pathlib.Path)
    parser.add_argument("--routes-csv", type=pathlib.Path)
    parser.add_argument("--out-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.population_geojson.read_text(encoding="utf-8"))
    by_municipality: dict[str, list[dict[str, object]]] = defaultdict(list)
    municipality_names: dict[str, str] = {}
    for feature in payload.get("features", []):
        props = feature.get("properties", {})
        code = str(props.get("municipality_code") or "unknown")
        by_municipality[code].append(feature)
        municipality_names[code] = str(props.get("municipality") or code)

    population_dir = args.out_dir / "population"
    shelter_dir = args.out_dir / "shelters"
    risk_dir = args.out_dir / "risk"
    route_dir = args.out_dir / "routes"
    population_dir.mkdir(parents=True, exist_ok=True)
    shelter_dir.mkdir(parents=True, exist_ok=True)
    risk_dir.mkdir(parents=True, exist_ok=True)
    route_dir.mkdir(parents=True, exist_ok=True)
    index = []
    for code in sorted(by_municipality):
        filename = f"mesh_500m_{code}.geojson"
        output = {"type": "FeatureCollection", "features": by_municipality[code]}
        (population_dir / filename).write_text(
            json.dumps(output, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
            encoding="utf-8",
        )
        index.append({"municipality_code": code, "municipality": municipality_names[code], "file": f"population/{filename}", "feature_count": len(by_municipality[code])})
    (population_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    with args.shelters_csv.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    features = []
    for row in rows:
        try:
            lon = float(row["longitude"])
            lat = float(row["latitude"])
        except (KeyError, TypeError, ValueError):
            continue
        properties = {key: json_safe(value) for key, value in row.items() if key not in {"longitude", "latitude"}}
        properties["capacity_numeric"] = parse_capacity_value(row.get("capacity"))
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": properties})
    (shelter_dir / "shelters.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (shelter_dir / "shelters_joined.csv").write_text(args.shelters_csv.read_text(encoding="utf-8-sig"), encoding="utf-8")
    if args.tsunami_csv:
        (risk_dir / "tsunami_exposure.csv").write_text(args.tsunami_csv.read_text(encoding="utf-8-sig"), encoding="utf-8")

    if args.risk_csv:
        risk = pd.read_csv(args.risk_csv, encoding="utf-8-sig", dtype={"mesh_id": str, "municipality_code": str})
        risk_columns = [
            "mesh_id", "municipality_code", "municipality", "total_population", "population_65plus", "population_75plus",
            "aging_rate_65plus", "aging_rate_75plus", "age_65plus_missing", "age_75plus_missing", "age_quality_flag",
            "tsunami_inundation_ratio", "tsunami_max_depth_class", "tsunami_exposure_score",
            "nearest_shelter_id", "network_distance_m", "walking_time_1_0_s", "walking_time_0_62_s", "walking_time_0_5_s",
            "route_inundation_distance_m", "route_inundation_ratio", "route_max_depth_class",
            "assigned_population", "shelter_capacity", "capacity_pressure", "capacity_component_status",
            "evacuation_difficulty_score", "data_completeness", "data_completeness_pct", "score_status",
            "tsunami_exposure_component", "vulnerable_population_component", "walking_accessibility_component",
            "route_inundation_exposure_component", "shelter_capacity_pressure_component",
        ]
        risk_columns = [column for column in risk_columns if column in risk.columns]
        risk_index = []
        for code, group in risk.groupby("municipality_code", sort=True):
            records = [json_safe_record(record) for record in group[risk_columns].to_dict(orient="records")]
            filename = f"risk_mesh_{code}.json"
            (risk_dir / filename).write_text(json.dumps(records, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")
            risk_index.append({"municipality_code": str(code), "municipality": str(group["municipality"].iloc[0]), "file": f"risk/{filename}", "feature_count": len(records)})
        (risk_dir / "index.json").write_text(json.dumps(risk_index, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.routes_csv:
        routes = pd.read_csv(args.routes_csv, encoding="utf-8-sig", dtype={"mesh_id": str, "municipality_code": str})
        route_index = []
        route_columns = [
            "mesh_id", "municipality_code", "municipality", "nearest_shelter_id",
            "network_distance_m", "walking_time_1_0_s", "walking_time_0_62_s", "walking_time_0_5_s",
            "route_coordinates", "route_status", "route_geometry_distance_m",
            "route_inundation_distance_m", "route_inundation_ratio", "route_max_depth_class",
            "route_exposure_status",
        ]
        for code, group in routes.groupby("municipality_code", sort=True):
            columns = [column for column in route_columns if column in group.columns]
            records = [json_safe_record(record) for record in group[columns].to_dict(orient="records")]
            filename = f"routes_{code}.json"
            (route_dir / filename).write_text(json.dumps(records, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")
            route_index.append({"municipality_code": str(code), "municipality": str(group["municipality"].iloc[0]), "file": f"routes/{filename}", "feature_count": len(records)})
        (route_dir / "index.json").write_text(json.dumps(route_index, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"population_files": len(index), "population_features": sum(item["feature_count"] for item in index), "shelter_features_with_coordinates": len(features), "tsunami_exposure_exported": bool(args.tsunami_csv), "risk_exported": bool(args.risk_csv), "route_exported": bool(args.routes_csv)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
