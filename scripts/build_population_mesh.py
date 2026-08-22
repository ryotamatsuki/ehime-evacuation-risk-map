#!/usr/bin/env python3
"""Build 500m JGD2011 population features for the 14 coastal municipalities."""

from __future__ import annotations

import argparse
import io
import json
import pathlib
import zipfile

import pandas as pd
from shapely.geometry import Point, Polygon, mapping
from shapely.prepared import prep
from shapely.ops import unary_union

from mesh500 import mesh_centroid, mesh_polygon


COASTAL = {
    "38201": "松山市", "38202": "今治市", "38203": "宇和島市", "38204": "八幡浜市",
    "38205": "新居浜市", "38206": "西条市", "38207": "大洲市", "38210": "伊予市",
    "38213": "四国中央市", "38214": "西予市", "38356": "上島町", "38401": "松前町",
    "38442": "伊方町", "38506": "愛南町",
}
POP_COLUMNS = {"total_population": "T001141001"}
AGE_TOTAL_COLUMNS = {
    # T001192043 onward are the five-year age-band totals. T001192064 is
    # average age and must not be included in a population count.
    "population_65plus": ["T001192043", "T001192046", "T001192049", "T001192052", "T001192055", "T001192058", "T001192061"],
    "population_75plus": ["T001192049", "T001192052", "T001192055", "T001192058", "T001192061"],
}


def read_table(path: pathlib.Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        name = archive.namelist()[0]
        data = archive.read(name)
    df = pd.read_csv(io.BytesIO(data), encoding="cp932", dtype=str)
    df["KEY_CODE"] = df["KEY_CODE"].fillna("").astype(str).str.strip()
    return df[df["KEY_CODE"].str.fullmatch(r"\d{9}")].copy()


def load_boundaries(path: pathlib.Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        payload = json.loads(archive.read("N03-20240101_38.geojson"))
    grouped: dict[str, list[object]] = {}
    for feature in payload["features"]:
        code = str(feature["properties"].get("N03_007") or "")
        if code in COASTAL:
            from shapely.geometry import shape
            grouped.setdefault(code, []).append(shape(feature["geometry"]))
    return {code: unary_union(geometries) for code, geometries in grouped.items()}


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.replace({"": None, "-": None, "*": None, "***": None}), errors="coerce")


def nullable_text(value: object) -> str | None:
    """Convert pandas NaN/NA to a JSON-safe null without imputing a value."""
    return None if pd.isna(value) else str(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=pathlib.Path, required=True)
    parser.add_argument("--out-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-geojson", type=pathlib.Path, required=True)
    parser.add_argument("--out-metadata", type=pathlib.Path, required=True)
    args = parser.parse_args()

    pop_parts = [read_table(args.raw / f"population_households_{code}.zip") for code in ["4932", "5032", "5033", "5132", "5133"]]
    age_parts = [read_table(args.raw / f"age_5year_{code}.zip") for code in ["4932", "5032", "5033", "5132", "5133"]]
    pop = pd.concat(pop_parts, ignore_index=True).drop_duplicates("KEY_CODE")
    age = pd.concat(age_parts, ignore_index=True).drop_duplicates("KEY_CODE")
    age_columns = list(dict.fromkeys(c for cols in AGE_TOTAL_COLUMNS.values() for c in cols))
    merged = pop[["KEY_CODE", "HTKSYORI", "HTKSAKI", "GASSAN", POP_COLUMNS["total_population"]]].merge(
        age[["KEY_CODE"] + age_columns], on="KEY_CODE", how="left", suffixes=("", "_age")
    )
    merged = merged.rename(columns={"KEY_CODE": "mesh_id", "HTKSYORI": "population_status", "HTKSAKI": "population_status_target", "GASSAN": "population_aggregation_target"})
    merged["total_population"] = numeric(merged[POP_COLUMNS["total_population"]])
    for name, cols in AGE_TOTAL_COLUMNS.items():
        values = merged[cols].apply(numeric)
        merged[name] = values.sum(axis=1, min_count=len(cols))
        merged[f"{name}_missing"] = values.isna().any(axis=1)
    merged["age_quality_flag"] = "ok"
    age_inconsistent = (
        merged["population_65plus"].notna()
        & merged["population_75plus"].notna()
        & merged["total_population"].notna()
        & (
            merged["population_65plus"].gt(merged["total_population"])
            | merged["population_75plus"].gt(merged["population_65plus"])
            | merged["population_75plus"].gt(merged["total_population"])
        )
    )
    # Some e-Stat aggregation rows carry age-band figures that do not reconcile
    # with the row total. Keep the source quality state, but do not publish an
    # impossible rate or silently clamp the counts.
    merged.loc[age_inconsistent, "age_quality_flag"] = "inconsistent_with_total"
    merged.loc[age_inconsistent, ["population_65plus", "population_75plus"]] = pd.NA
    merged.loc[age_inconsistent, ["population_65plus_missing", "population_75plus_missing"]] = True
    merged["aging_rate_65plus"] = merged["population_65plus"] / merged["total_population"]
    merged["aging_rate_75plus"] = merged["population_75plus"] / merged["total_population"]
    merged.loc[merged["total_population"].le(0) | merged["total_population"].isna(), ["aging_rate_65plus", "aging_rate_75plus"]] = pd.NA

    boundaries = load_boundaries(args.raw / "N03-20240101_38_GML.zip")
    prepared = {code: (prep(geometry), geometry) for code, geometry in boundaries.items()}
    records = []
    for row in merged.itertuples(index=False):
        lon, lat = mesh_centroid(row.mesh_id)
        point = Point(lon, lat)
        municipality_code = None
        for code, (candidate, _) in prepared.items():
            if candidate.contains(point) or candidate.intersects(point):
                municipality_code = code
                break
        if municipality_code not in COASTAL:
            continue
        total = None if pd.isna(row.total_population) else int(row.total_population)
        p65 = None if pd.isna(row.population_65plus) else int(row.population_65plus)
        p75 = None if pd.isna(row.population_75plus) else int(row.population_75plus)
        record = {
            "type": "Feature", "mesh_id": row.mesh_id, "municipality_code": municipality_code,
            "municipality": COASTAL[municipality_code], "total_population": total,
            "population_65plus": p65, "population_75plus": p75,
            "aging_rate_65plus": None if pd.isna(row.aging_rate_65plus) else float(row.aging_rate_65plus),
            "aging_rate_75plus": None if pd.isna(row.aging_rate_75plus) else float(row.aging_rate_75plus),
            "population_status": nullable_text(row.population_status),
            "population_status_target": nullable_text(row.population_status_target),
            "population_aggregation_target": nullable_text(row.population_aggregation_target),
            "age_65plus_missing": bool(row.population_65plus_missing),
            "age_75plus_missing": bool(row.population_75plus_missing),
            "age_quality_flag": nullable_text(row.age_quality_flag),
            "data_completeness": "complete" if total is not None and p65 is not None and p75 is not None else "partial",
            "geometry": {"type": "Polygon", "coordinates": [mesh_polygon(row.mesh_id)]},
        }
        records.append(record)

    columns = ["mesh_id", "municipality_code", "municipality", "total_population", "population_65plus", "population_75plus", "aging_rate_65plus", "aging_rate_75plus", "population_status", "population_status_target", "population_aggregation_target", "age_65plus_missing", "age_75plus_missing", "age_quality_flag", "data_completeness"]
    frame = pd.DataFrame([{k: item[k] for k in columns} for item in records])
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.out_csv, index=False, encoding="utf-8-sig")
    feature_collection = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": item.pop("geometry"), "properties": item} for item in records]}
    args.out_geojson.parent.mkdir(parents=True, exist_ok=True)
    args.out_geojson.write_text(json.dumps(feature_collection, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    metadata = {
        "source": "e-Stat 令和2年国勢調査 地域メッシュ統計 500m JGD2011",
        "population_table": "T001141",
        "age_table": "T001192",
        "boundary_source": "国土数値情報 N03-20240101_38_GML",
        "feature_count": len(records),
        "municipality_counts": frame["municipality"].value_counts().sort_index().to_dict(),
        "missing_total_population": int(frame["total_population"].isna().sum()),
        "missing_65plus": int(frame["population_65plus"].isna().sum()),
        "missing_75plus": int(frame["population_75plus"].isna().sum()),
        "age_inconsistent_with_total": int((frame["age_quality_flag"] == "inconsistent_with_total").sum()),
        "null_policy": "missing and confidentiality values remain null; never converted to zero",
    }
    args.out_metadata.parent.mkdir(parents=True, exist_ok=True)
    args.out_metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
