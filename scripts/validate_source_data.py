#!/usr/bin/env python3
"""Validate committed source/intermediate data without assuming a legacy analysis schema.

Analysis Core STEP 1-4 owns routing, exposure, capacity and risk-output QA.  This
validator deliberately stops at the stable committed inputs used by those stages,
so source-data CI cannot accidentally revive pre-v4 score or routing semantics.
"""

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


def validate_geojson(directory: pathlib.Path) -> tuple[int, int, str]:
    files = sorted(directory.glob("mesh_500m_*.geojson"))
    invalid = 0
    features = 0
    try:
        from shapely.geometry import shape

        detail = "shapely validity"
        for path in files:
            document = json.loads(path.read_text(encoding="utf-8"))
            for feature in document.get("features", []):
                features += 1
                try:
                    if not shape(feature["geometry"]).is_valid:
                        invalid += 1
                except Exception:
                    invalid += 1
    except ModuleNotFoundError:
        detail = "structural polygon check (Shapely unavailable)"
        for path in files:
            document = json.loads(path.read_text(encoding="utf-8"))
            for feature in document.get("features", []):
                features += 1
                geometry = feature.get("geometry", {})
                if geometry.get("type") != "Polygon" or not geometry.get("coordinates"):
                    invalid += 1
                    continue
                for ring in geometry["coordinates"]:
                    if len(ring) < 4 or ring[0] != ring[-1]:
                        invalid += 1
                        break
    return features, invalid, f"GeoJSON files={len(files)}, features={features}; {detail}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--population-csv", type=pathlib.Path, required=True)
    parser.add_argument("--shelters-csv", type=pathlib.Path, required=True)
    parser.add_argument("--tsunami-exposure-csv", type=pathlib.Path, required=True)
    parser.add_argument("--population-geojson-dir", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    population = pd.read_csv(args.population_csv, encoding="utf-8-sig", dtype={"mesh_id": str})
    shelters = pd.read_csv(args.shelters_csv, encoding="utf-8-sig", dtype={"common_id": str})
    tsunami = pd.read_csv(args.tsunami_exposure_csv, encoding="utf-8-sig", dtype={"mesh_id": str})
    checks: list[dict[str, object]] = []

    mesh_duplicates = int(population["mesh_id"].duplicated().sum())
    checks.append(check("mesh_id_unique", "pass" if mesh_duplicates == 0 else "failure", mesh_duplicates))

    total = pd.to_numeric(population["total_population"], errors="coerce")
    total_null = int(total.isna().sum())
    checks.append(check("population_total_null", "pass" if total_null == 0 else "failure", total_null, "欠損を0へ補完しない"))

    p65 = pd.to_numeric(population["population_65plus"], errors="coerce")
    p65_gt_total = int((p65.notna() & total.notna() & (p65 > total)).sum())
    checks.append(check("population_65plus_not_over_total", "pass" if p65_gt_total == 0 else "failure", p65_gt_total))

    age_missing = int(population[["population_65plus", "population_75plus"]].isna().any(axis=1).sum())
    checks.append(check("age_missing_preserved", "warning" if age_missing else "pass", age_missing, "欠損年齢構成は推測補完しない"))

    duplicate_rows = int(shelters["common_id"].duplicated(keep=False).sum())
    duplicate_ids = int(shelters.loc[shelters["common_id"].duplicated(keep=False), "common_id"].nunique())
    checks.append(check("shelter_common_id_duplicates", "warning" if duplicate_rows else "pass", duplicate_ids, f"重複レコード数={duplicate_rows}; 原典属性を保持"))

    coordinate_missing = int(shelters[["latitude", "longitude"]].isna().any(axis=1).sum())
    checks.append(check("shelter_coordinate_missing", "warning" if coordinate_missing else "pass", coordinate_missing, "未照合施設は推測座標で補完しない"))

    tsunami_flags = set(pd.to_numeric(shelters["tsunami"], errors="coerce").dropna().unique().tolist())
    invalid_flags = sorted(value for value in tsunami_flags if value not in {0, 1})
    checks.append(check("shelter_tsunami_flag_values", "pass" if not invalid_flags else "failure", invalid_flags))

    tsunami_shelter = pd.to_numeric(shelters["tsunami"], errors="coerce").eq(1)
    parsed_capacity = shelters["capacity"].map(parse_capacity)
    capacity_missing = int(parsed_capacity[tsunami_shelter].isna().sum())
    checks.append(check("tsunami_shelter_capacity_missing", "warning" if capacity_missing else "pass", capacity_missing, "容量欠損は0として扱わない"))

    tsunami_duplicates = int(tsunami["mesh_id"].duplicated().sum())
    checks.append(check("tsunami_mesh_id_unique", "pass" if tsunami_duplicates == 0 else "failure", tsunami_duplicates))

    population_ids = set(population["mesh_id"].astype(str))
    orphan_tsunami = int((~tsunami["mesh_id"].astype(str).isin(population_ids)).sum())
    checks.append(check("tsunami_mesh_in_population", "pass" if orphan_tsunami == 0 else "failure", orphan_tsunami))

    ratios = pd.to_numeric(tsunami["tsunami_inundation_ratio"], errors="coerce")
    invalid_ratios = int((ratios.isna() | (ratios < 0) | (ratios > 1)).sum())
    checks.append(check("tsunami_inundation_ratio_0_1", "pass" if invalid_ratios == 0 else "failure", invalid_ratios))

    scores = pd.to_numeric(tsunami["tsunami_exposure_score"], errors="coerce")
    score_mismatch = int((scores.notna() & ratios.notna() & ((scores - ratios * 100).abs() > 1e-8)).sum())
    checks.append(check("tsunami_exposure_score_formula", "pass" if score_mismatch == 0 else "failure", score_mismatch, "score = inundation_ratio × 100"))

    samples = pd.to_numeric(tsunami["tsunami_sample_count"], errors="coerce")
    inundated_samples = pd.to_numeric(tsunami["tsunami_inundated_sample_count"], errors="coerce")
    bad_sample_counts = int((samples.isna() | inundated_samples.isna() | (samples <= 0) | (inundated_samples < 0) | (inundated_samples > samples)).sum())
    checks.append(check("tsunami_sample_counts", "pass" if bad_sample_counts == 0 else "failure", bad_sample_counts))

    try:
        geojson_features, invalid_geometry, geometry_detail = validate_geojson(args.population_geojson_dir)
        checks.append(check("mesh_geometry_valid", "pass" if invalid_geometry == 0 else "failure", invalid_geometry, geometry_detail))
        checks.append(check("mesh_geojson_feature_count", "pass" if geojson_features == len(population) else "failure", geojson_features, f"population rows={len(population)}"))
    except Exception as exc:  # pragma: no cover - environment-level failure
        checks.append(check("mesh_geometry_read", "failure", str(exc)))

    failures = [item for item in checks if item["status"] == "failure"]
    warnings = [item for item in checks if item["status"] == "warning"]
    report = {
        "status": "failure" if failures else "pass",
        "scope": "stable committed source/intermediate data only; Analysis Core outputs are validated by STEP 1-7 gates",
        "checks": checks,
        "failure_count": len(failures),
        "warning_count": len(warnings),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if args.strict and failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
