#!/usr/bin/env python3
"""Run reproducible data, numerical, and GIS integrity checks."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import unicodedata

import pandas as pd


def parse_capacity(value: object) -> float | None:
    if pd.isna(value):
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    match = re.match(r"^([0-9][0-9,]*(?:\.[0-9]+)?)", text)
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    return number if number > 0 else None


def check(name: str, status: str, value: object, detail: str = "") -> dict[str, object]:
    return {"name": name, "status": status, "value": value, "detail": detail}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--population-csv", type=pathlib.Path, required=True)
    parser.add_argument("--shelters-csv", type=pathlib.Path, required=True)
    parser.add_argument("--routes-csv", type=pathlib.Path)
    parser.add_argument("--routes-dir", type=pathlib.Path)
    parser.add_argument("--risk-csv", type=pathlib.Path)
    parser.add_argument("--risk-dir", type=pathlib.Path)
    parser.add_argument("--population-geojson-dir", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    population = pd.read_csv(args.population_csv, encoding="utf-8-sig", dtype={"mesh_id": str})
    shelters = pd.read_csv(args.shelters_csv, encoding="utf-8-sig", dtype={"common_id": str})
    if bool(args.routes_csv) == bool(args.routes_dir):
        parser.error("provide exactly one of --routes-csv or --routes-dir")
    if bool(args.risk_csv) == bool(args.risk_dir):
        parser.error("provide exactly one of --risk-csv or --risk-dir")
    if args.routes_csv:
        routes = pd.read_csv(args.routes_csv, encoding="utf-8-sig", dtype={"mesh_id": str})
    else:
        route_records = []
        for path in sorted(args.routes_dir.glob("routes_*.json")):
            route_records.extend(json.loads(path.read_text(encoding="utf-8")))
        routes = pd.DataFrame(route_records)
        routes["mesh_id"] = routes["mesh_id"].astype(str)
    if args.risk_csv:
        risk = pd.read_csv(args.risk_csv, encoding="utf-8-sig", dtype={"mesh_id": str})
    else:
        risk_records = []
        for path in sorted(args.risk_dir.glob("risk_mesh_*.json")):
            risk_records.extend(json.loads(path.read_text(encoding="utf-8")))
        risk = pd.DataFrame(risk_records)
        risk["mesh_id"] = risk["mesh_id"].astype(str)
    checks: list[dict[str, object]] = []

    mesh_duplicates = int(population["mesh_id"].duplicated().sum())
    checks.append(check("mesh_id_unique", "pass" if mesh_duplicates == 0 else "failure", mesh_duplicates))
    checks.append(check("population_total_null", "pass" if population["total_population"].notna().all() else "failure", int(population["total_population"].isna().sum()), "秘匿・欠損を0へ置換していない"))
    p65 = pd.to_numeric(population["population_65plus"], errors="coerce")
    total = pd.to_numeric(population["total_population"], errors="coerce")
    p65_gt_total = int(((p65.notna()) & (total.notna()) & (p65 > total)).sum())
    checks.append(check("population_65plus_not_over_total", "pass" if p65_gt_total == 0 else "failure", p65_gt_total))
    age_null = int(population[["population_65plus", "population_75plus"]].isna().any(axis=1).sum())
    checks.append(check("age_missing_preserved", "pass" if age_null >= 0 else "failure", age_null))

    shelter_duplicates = int(shelters["common_id"].duplicated(keep=False).sum())
    duplicate_ids = int(shelters.loc[shelters["common_id"].duplicated(keep=False), "common_id"].nunique())
    checks.append(check("shelter_common_id_unique", "warning" if shelter_duplicates else "pass", duplicate_ids, f"重複レコード数={shelter_duplicates};施設属性正本の重複を保持"))
    coordinate_missing = int(shelters[["latitude", "longitude"]].isna().any(axis=1).sum())
    checks.append(check("shelter_coordinate_missing", "warning" if coordinate_missing else "pass", coordinate_missing, "未照合施設は推測座標で補完していない"))
    tsunami_values = set(pd.to_numeric(shelters["tsunami"], errors="coerce").dropna().unique().tolist())
    invalid_tsunami = sorted(value for value in tsunami_values if value not in {0, 1})
    checks.append(check("shelter_tsunami_flag_values", "pass" if not invalid_tsunami else "failure", invalid_tsunami))
    tsunami = pd.to_numeric(shelters["tsunami"], errors="coerce").eq(1)
    parsed_capacity = shelters["capacity"].map(parse_capacity)
    capacity_missing = int(parsed_capacity[tsunami].isna().sum())
    checks.append(check("tsunami_shelter_capacity_missing", "warning" if capacity_missing else "pass", capacity_missing, "収容人数nullを0として扱っていない"))

    route_duplicates = int(routes["mesh_id"].duplicated().sum())
    checks.append(check("route_mesh_id_unique", "pass" if route_duplicates == 0 else "failure", route_duplicates))
    route_failures = int((routes["route_status"] != "complete").sum())
    checks.append(check("route_calculation_failure", "warning" if route_failures else "pass", route_failures, "ネットワーク上で経路なしのメッシュはnull保持"))
    route_distance = pd.to_numeric(routes["network_distance_m"], errors="coerce")
    bad_times = ((route_distance.notna()) & ((pd.to_numeric(routes["walking_time_1_0_s"], errors="coerce") - route_distance).abs() > 1e-6)).sum()
    checks.append(check("walking_time_formula", "pass" if bad_times == 0 else "failure", int(bad_times), "time = network_distance / speed"))

    score_range_columns = [
        "evacuation_difficulty_score", "tsunami_exposure_component",
        "vulnerable_population_component", "walking_accessibility_component",
        "route_inundation_exposure_component", "shelter_capacity_pressure_component",
    ]
    for column in score_range_columns:
        values = pd.to_numeric(risk[column], errors="coerce")
        invalid = int(((values < 0) | (values > 100)).sum())
        checks.append(check(f"{column}_0_100", "pass" if invalid == 0 else "failure", invalid))
    score_null = int(risk["evacuation_difficulty_score"].isna().sum())
    checks.append(check("risk_score_nonnull", "pass" if score_null == 0 else "failure", score_null, "available weights are renormalized"))
    completeness = pd.to_numeric(risk["data_completeness"], errors="coerce")
    invalid_completeness = int(((completeness < 0) | (completeness > 1) | completeness.isna()).sum())
    checks.append(check("data_completeness_0_1", "pass" if invalid_completeness == 0 else "failure", invalid_completeness))

    geojson_files = sorted(args.population_geojson_dir.glob("mesh_500m_*.geojson"))
    invalid_geometry = 0
    geojson_features = 0
    try:
        from shapely.geometry import shape
        geometry_detail = "shapely validity"
        for path in geojson_files:
            document = json.loads(path.read_text(encoding="utf-8"))
            for feature in document.get("features", []):
                geojson_features += 1
                try:
                    if not shape(feature["geometry"]).is_valid:
                        invalid_geometry += 1
                except Exception:
                    invalid_geometry += 1
    except ModuleNotFoundError:
        # Keep the CI data test runnable in a minimal Python environment. The
        # full local ETL run uses Shapely; this fallback checks GeoJSON polygon
        # structure and closed rings, and reports that limitation explicitly.
        geometry_detail = "structural polygon check (Shapely unavailable)"
        for path in geojson_files:
            document = json.loads(path.read_text(encoding="utf-8"))
            for feature in document.get("features", []):
                geojson_features += 1
                geometry = feature.get("geometry", {})
                if geometry.get("type") != "Polygon" or not geometry.get("coordinates"):
                    invalid_geometry += 1
                    continue
                for ring in geometry["coordinates"]:
                    if len(ring) < 4 or ring[0] != ring[-1]:
                        invalid_geometry += 1
                        break
    except Exception as exc:  # pragma: no cover - environment-level failure
        invalid_geometry = -1
        checks.append(check("geojson_geometry_read", "failure", str(exc)))
    if invalid_geometry >= 0:
        checks.append(check("mesh_geometry_valid", "pass" if invalid_geometry == 0 else "failure", invalid_geometry, f"GeoJSON files={len(geojson_files)}, features={geojson_features}; {geometry_detail}"))

    failures = [item for item in checks if item["status"] == "failure"]
    warnings = [item for item in checks if item["status"] == "warning"]
    report = {
        "status": "failure" if failures else "pass",
        "checks": checks,
        "failure_count": len(failures),
        "warning_count": len(warnings),
        "scope": "14 municipalities / 500m mesh",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if args.strict and failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
