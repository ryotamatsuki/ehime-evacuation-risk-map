#!/usr/bin/env python3
"""Export compact, browser-safe files from ETL outputs.

The web build is split by municipality so each asset remains small enough for
GitHub Pages. Raw downloads and cached raster tiles are never copied here.
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
from collections import defaultdict


def json_safe(value: object) -> object:
    if value is None:
        return None
    text = str(value)
    if text.strip().lower() in {"", "nan", "none", "nat"}:
        return None
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--population-geojson", type=pathlib.Path, required=True)
    parser.add_argument("--shelters-csv", type=pathlib.Path, required=True)
    parser.add_argument("--tsunami-csv", type=pathlib.Path)
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
    population_dir.mkdir(parents=True, exist_ok=True)
    shelter_dir.mkdir(parents=True, exist_ok=True)
    risk_dir.mkdir(parents=True, exist_ok=True)
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
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": properties})
    (shelter_dir / "shelters.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (shelter_dir / "shelters_joined.csv").write_text(args.shelters_csv.read_text(encoding="utf-8-sig"), encoding="utf-8")
    if args.tsunami_csv:
        (risk_dir / "tsunami_exposure.csv").write_text(args.tsunami_csv.read_text(encoding="utf-8-sig"), encoding="utf-8")
    print(json.dumps({"population_files": len(index), "population_features": sum(item["feature_count"] for item in index), "shelter_features_with_coordinates": len(features), "tsunami_exposure_exported": bool(args.tsunami_csv)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
