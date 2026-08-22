#!/usr/bin/env python3
"""Calculate the transparent exploratory evacuation-difficulty score."""

from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np
import pandas as pd


WEIGHTS = {
    "tsunami_exposure": 25.0,
    "vulnerable_population": 20.0,
    "walking_accessibility": 25.0,
    "route_inundation_exposure": 15.0,
    "shelter_capacity_pressure": 15.0,
}

LABELS = {
    "tsunami_exposure": "津波浸水曝露が大きい",
    "vulnerable_population": "65歳以上人口割合が高い",
    "walking_accessibility": "津波対応避難場所までの徒歩距離が長い",
    "route_inundation_exposure": "避難経路の一部が津波浸水域を通る",
    "shelter_capacity_pressure": "最寄り避難場所の収容負荷が高い",
}


def percentile_bounds(values: pd.Series) -> tuple[float, float]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return 0.0, 1.0
    low = float(clean.quantile(0.05))
    high = float(clean.quantile(0.95))
    if high <= low:
        high = low + 1.0
    return low, high


def normalize_walking(values: pd.Series, low: float, high: float) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    return ((numeric - low) / (high - low) * 100.0).clip(0.0, 100.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--risk-base-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-qa", type=pathlib.Path, required=True)
    args = parser.parse_args()

    result = pd.read_csv(args.risk_base_csv, encoding="utf-8-sig", dtype={"mesh_id": str})
    numeric_columns = [
        "tsunami_exposure_score", "aging_rate_65plus", "network_distance_m",
        "route_inundation_ratio", "capacity_pressure",
    ]
    for column in numeric_columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    walking_low, walking_high = percentile_bounds(result["network_distance_m"])
    result["tsunami_exposure_component"] = result["tsunami_exposure_score"].clip(0.0, 100.0)
    result["vulnerable_population_component"] = (result["aging_rate_65plus"] * 100.0).clip(0.0, 100.0)
    result["walking_accessibility_component"] = normalize_walking(result["network_distance_m"], walking_low, walking_high)
    result["route_inundation_exposure_component"] = (result["route_inundation_ratio"] * 100.0).clip(0.0, 100.0)
    # A pressure of 1.0 means the hypothetical assigned population equals the
    # official capacity. Higher values remain urgent but saturate at 100.
    result["shelter_capacity_pressure_component"] = (result["capacity_pressure"] * 100.0).clip(0.0, 100.0)

    component_columns = {
        "tsunami_exposure": "tsunami_exposure_component",
        "vulnerable_population": "vulnerable_population_component",
        "walking_accessibility": "walking_accessibility_component",
        "route_inundation_exposure": "route_inundation_exposure_component",
        "shelter_capacity_pressure": "shelter_capacity_pressure_component",
    }
    component_frame = result[list(component_columns.values())].rename(columns={v: k for k, v in component_columns.items()})
    available_weights = component_frame.notna().astype(float).mul(pd.Series(WEIGHTS))
    result["score_available_weight"] = available_weights.sum(axis=1)
    weighted_values = component_frame.mul(pd.Series(WEIGHTS), axis="columns")
    result["evacuation_difficulty_score"] = weighted_values.sum(axis=1, skipna=True).div(result["score_available_weight"]).where(result["score_available_weight"] > 0).clip(0.0, 100.0)
    result["data_completeness"] = component_frame.notna().sum(axis=1) / len(component_columns)
    result["data_completeness_pct"] = result["data_completeness"] * 100.0
    result["score_status"] = np.select(
        [result["data_completeness"].eq(1.0), result["data_completeness"].gt(0)],
        ["complete", "partial"],
        default="unavailable",
    )

    def reasons(row: pd.Series) -> str:
        items = []
        for key, column in component_columns.items():
            value = row[column]
            if pd.isna(value):
                continue
            items.append({
                "key": key,
                "label": LABELS[key],
                "component_score": round(float(value), 3),
                "weighted_contribution": round(float(value) * WEIGHTS[key] / 100.0, 3),
            })
        items.sort(key=lambda item: item["weighted_contribution"], reverse=True)
        return json.dumps(items, ensure_ascii=False, separators=(",", ":"))

    result["risk_reasons"] = result.apply(reasons, axis=1)
    result["score_method_note"] = "PoC用探索的重み。欠損要素は利用可能な重みで再正規化。公式な政策基準ではない。"

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.out_csv, index=False, encoding="utf-8-sig")
    qa = {
        "mesh_count": len(result),
        "score_complete": int((result["score_status"] == "complete").sum()),
        "score_partial": int((result["score_status"] == "partial").sum()),
        "score_unavailable": int((result["score_status"] == "unavailable").sum()),
        "weights": WEIGHTS,
        "walking_distance_normalization": {"method": "5th_to_95th_percentile_global_clip", "p05_m": walking_low, "p95_m": walking_high},
        "capacity_pressure_normalization": {"method": "pressure_1_0_equals_100_and_saturates", "threshold": 1.0},
        "vulnerable_population_definition": "65歳以上人口割合を0-100へ変換。75歳以上割合は別表示。",
        "missing_handling": "available weights are renormalized per mesh; data_completeness is reported separately",
        "exploratory_only": True,
    }
    args.out_qa.parent.mkdir(parents=True, exist_ok=True)
    args.out_qa.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False))


if __name__ == "__main__":
    main()
