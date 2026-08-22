#!/usr/bin/env python3
"""STEP 5: sensitivity, robustness, and final analytical QA.

Consumes the corrected Analysis Core v4 public export rather than legacy
repository data. It does not rewrite the canonical STEP 4 score. Instead it
tests how stable the ranking is under deterministic weight perturbations and
compares the two already-defined evacuation-demand scenarios.

The canonical five-component score remains 25/20/25/15/15. Sensitivity
scenarios multiply one component weight by 0.8 or 1.2 (others unchanged), plus
an equal-weight scenario. Scores are produced only for rows whose
``score_status`` is ``complete``; missing capacity, incomplete core data, and
route failures stay outside the ranked set.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
from typing import Any

import numpy as np
import pandas as pd

BASE_WEIGHTS = {
    "tsunami_exposure": 25.0,
    "vulnerable_population": 20.0,
    "walking_accessibility": 25.0,
    "route_inundation_exposure": 15.0,
    "shelter_capacity_pressure": 15.0,
}
COMPONENT_COLUMNS = {
    "tsunami_exposure": "tsunami_exposure_component",
    "vulnerable_population": "vulnerable_population_component",
    "walking_accessibility": "walking_accessibility_component",
    "route_inundation_exposure": "route_inundation_exposure_component",
    "shelter_capacity_pressure": "shelter_capacity_pressure_component_area_weighted",
}
EXPECTED_STATUS_COUNTS = {
    "complete": 813,
    "core_only_missing_capacity": 128,
    "core_data_incomplete": 121,
    "route_unavailable": 28,
}
EXPECTED_ROUTE_UNAVAILABLE = 28
EXPECTED_TARGET_ROWS = 1090
EXPECTED_OVER_CAPACITY_AREA = 35
EXPECTED_OVER_CAPACITY_FULL = 72


def load_public_risk(public_data_root: pathlib.Path) -> pd.DataFrame:
    index_path = public_data_root / "risk" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for entry in index:
        file_path = public_data_root / str(entry["file"])
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"risk payload is not a list: {file_path}")
        rows.extend(payload)
    frame = pd.DataFrame(rows)
    if "mesh_id" in frame:
        frame["mesh_id"] = frame["mesh_id"].astype(str)
    return frame


def load_public_shelters(public_data_root: pathlib.Path) -> pd.DataFrame:
    path = public_data_root / "shelters" / "capacity_pressure.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("capacity_pressure.json must be a list")
    return pd.DataFrame(payload)


def scenario_weights() -> dict[str, dict[str, float]]:
    scenarios: dict[str, dict[str, float]] = {"baseline": dict(BASE_WEIGHTS)}
    for component in BASE_WEIGHTS:
        for suffix, multiplier in (("low", 0.8), ("high", 1.2)):
            weights = dict(BASE_WEIGHTS)
            weights[component] = weights[component] * multiplier
            scenarios[f"{component}_{suffix}"] = weights
    scenarios["equal_weight"] = {component: 20.0 for component in BASE_WEIGHTS}
    return scenarios


def weighted_score(frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    columns = [COMPONENT_COLUMNS[key] for key in weights]
    components = frame[columns].apply(pd.to_numeric, errors="coerce")
    ready = components.notna().all(axis=1)
    aligned = pd.Series(
        {COMPONENT_COLUMNS[key]: value for key, value in weights.items()},
        dtype=float,
    )
    numerator = components.mul(aligned, axis="columns").sum(axis=1)
    denominator = float(sum(weights.values()))
    return (numerator / denominator).where(ready).clip(0.0, 100.0)


def rank_desc(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values, errors="coerce").rank(
        method="min", ascending=False, na_option="keep"
    )


def top_ids(ranks: pd.Series, mesh_ids: pd.Series, n: int) -> set[str]:
    ranked = pd.DataFrame({"mesh_id": mesh_ids.astype(str), "rank": ranks}).dropna()
    ranked = ranked.sort_values(["rank", "mesh_id"], kind="stable")
    return set(ranked.head(n)["mesh_id"])


def safe_float(value: object) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def build_step5(
    risk: pd.DataFrame,
    shelters: pd.DataFrame,
    *,
    expected_rows: int = EXPECTED_TARGET_ROWS,
) -> tuple[pd.DataFrame, dict[str, Any], list[str]]:
    failures: list[str] = []

    required = {
        "mesh_id",
        "score_status",
        "route_status",
        "evacuation_difficulty_score",
        "evacuation_difficulty_score_full_mesh_sensitivity",
        "mesh_evacuation_demand_area_weighted",
        "mesh_evacuation_demand_full_mesh",
        *COMPONENT_COLUMNS.values(),
    }
    missing = sorted(required - set(risk.columns))
    if missing:
        return pd.DataFrame(), {}, [f"missing risk columns: {missing}"]

    if len(risk) != expected_rows:
        failures.append(f"target rows={len(risk)} expected={expected_rows}")
    if risk["mesh_id"].astype(str).nunique() != len(risk):
        failures.append("duplicate mesh_id in public risk export")

    status_counts = {
        str(k): int(v) for k, v in risk["score_status"].value_counts().to_dict().items()
    }
    if expected_rows == EXPECTED_TARGET_ROWS and status_counts != EXPECTED_STATUS_COUNTS:
        failures.append(f"score status counts differ: {status_counts}")

    route_unavailable = int(risk["route_status"].astype(str).ne("complete").sum())
    if expected_rows == EXPECTED_TARGET_ROWS and route_unavailable != EXPECTED_ROUTE_UNAVAILABLE:
        failures.append(
            f"route unavailable={route_unavailable} expected={EXPECTED_ROUTE_UNAVAILABLE}"
        )

    complete_mask = risk["score_status"].astype(str).eq("complete")
    complete = risk.loc[complete_mask].copy()
    baseline_exported = pd.to_numeric(
        complete["evacuation_difficulty_score"], errors="coerce"
    )
    if baseline_exported.isna().any():
        failures.append("complete score row missing canonical exported score")
    if pd.to_numeric(
        risk.loc[~complete_mask, "evacuation_difficulty_score"], errors="coerce"
    ).notna().any():
        failures.append("non-complete row has canonical five-component score")

    scenarios = scenario_weights()
    scored = pd.DataFrame({"mesh_id": complete["mesh_id"].astype(str).values})
    scenario_summaries: dict[str, Any] = {}
    baseline_rank: pd.Series | None = None
    baseline_top10: set[str] = set()
    baseline_top50: set[str] = set()
    top10_n = max(1, int(math.ceil(len(complete) * 0.10)))
    top50_n = min(50, len(complete))

    for scenario_id, weights in scenarios.items():
        scores = weighted_score(complete, weights)
        ranks = rank_desc(scores)
        scored[f"score__{scenario_id}"] = scores.to_numpy()
        scored[f"rank__{scenario_id}"] = ranks.to_numpy()

        if scores.isna().any():
            failures.append(
                f"{scenario_id}: sensitivity score missing on complete baseline rows"
            )

        if scenario_id == "baseline":
            baseline_rank = ranks
            mismatch = (scores - baseline_exported).abs().dropna()
            if not mismatch.empty and float(mismatch.max()) > 1e-9:
                failures.append(
                    f"baseline recomputation differs from STEP 4 max_abs={float(mismatch.max())}"
                )
            baseline_top10 = top_ids(ranks, complete["mesh_id"], top10_n)
            baseline_top50 = top_ids(ranks, complete["mesh_id"], top50_n)
            spearman = 1.0
        else:
            if baseline_rank is None:
                raise RuntimeError("baseline scenario must be evaluated first")
            paired = pd.concat([baseline_rank, ranks], axis=1).dropna()
            spearman = float(paired.iloc[:, 0].corr(paired.iloc[:, 1], method="pearson"))
            if not math.isfinite(spearman) or not -1.0 <= spearman <= 1.0:
                failures.append(f"{scenario_id}: invalid Spearman rank correlation")

        scenario_top10 = top_ids(ranks, complete["mesh_id"], top10_n)
        scenario_top50 = top_ids(ranks, complete["mesh_id"], top50_n)
        scenario_summaries[scenario_id] = {
            "weights": weights,
            "score_median": safe_float(scores.median()),
            "score_p90": safe_float(scores.quantile(0.90)),
            "score_p95": safe_float(scores.quantile(0.95)),
            "score_max": safe_float(scores.max()),
            "spearman_vs_baseline": spearman,
            "top10pct_n": top10_n,
            "top10pct_overlap_n": len(baseline_top10 & scenario_top10),
            "top10pct_overlap_rate": (
                len(baseline_top10 & scenario_top10) / top10_n if top10_n else None
            ),
            "top50_n": top50_n,
            "top50_overlap_n": len(baseline_top50 & scenario_top50),
            "top50_overlap_rate": (
                len(baseline_top50 & scenario_top50) / top50_n if top50_n else None
            ),
        }

    rank_columns = [column for column in scored if column.startswith("rank__")]
    scored["rank_best"] = scored[rank_columns].min(axis=1)
    scored["rank_worst"] = scored[rank_columns].max(axis=1)
    scored["rank_range"] = scored["rank_worst"] - scored["rank_best"]
    scored["rank_median"] = scored[rank_columns].median(axis=1)

    top10_sets = []
    top50_sets = []
    for scenario_id in scenarios:
        ranks = scored[f"rank__{scenario_id}"]
        top10_sets.append(top_ids(ranks, scored["mesh_id"], top10_n))
        top50_sets.append(top_ids(ranks, scored["mesh_id"], top50_n))
    robust_top10 = set.intersection(*top10_sets) if top10_sets else set()
    robust_top50 = set.intersection(*top50_sets) if top50_sets else set()
    scored["robust_top10pct_all_scenarios"] = scored["mesh_id"].isin(robust_top10)
    scored["robust_top50_all_scenarios"] = scored["mesh_id"].isin(robust_top50)

    area_total = float(
        pd.to_numeric(risk["mesh_evacuation_demand_area_weighted"], errors="coerce").sum()
    )
    full_total = float(
        pd.to_numeric(risk["mesh_evacuation_demand_full_mesh"], errors="coerce").sum()
    )
    area_pressure = pd.to_numeric(
        shelters.get("capacity_pressure_area_weighted"), errors="coerce"
    )
    full_pressure = pd.to_numeric(
        shelters.get("capacity_pressure_full_mesh"), errors="coerce"
    )
    over_area = int(area_pressure.gt(1.0).sum())
    over_full = int(full_pressure.gt(1.0).sum())
    if expected_rows == EXPECTED_TARGET_ROWS:
        if over_area != EXPECTED_OVER_CAPACITY_AREA:
            failures.append(
                f"area-weighted over-capacity shelters={over_area} expected={EXPECTED_OVER_CAPACITY_AREA}"
            )
        if over_full != EXPECTED_OVER_CAPACITY_FULL:
            failures.append(
                f"full-mesh over-capacity shelters={over_full} expected={EXPECTED_OVER_CAPACITY_FULL}"
            )

    full_mesh_score = pd.to_numeric(
        complete["evacuation_difficulty_score_full_mesh_sensitivity"], errors="coerce"
    )
    if full_mesh_score.isna().any():
        failures.append("complete row missing STEP 4 full-mesh sensitivity score")

    missing_capacity = int(
        risk["score_status"].astype(str).eq("core_only_missing_capacity").sum()
    )
    core_incomplete = int(
        risk["score_status"].astype(str).eq("core_data_incomplete").sum()
    )

    if scored["mesh_id"].nunique() != len(scored):
        failures.append("STEP 5 robustness output has duplicate mesh_id")
    if len(scored) != int(complete_mask.sum()):
        failures.append("STEP 5 robustness output row count differs from complete baseline rows")
    if not scored[rank_columns].notna().all(axis=None):
        failures.append("STEP 5 rank output contains nulls for complete baseline rows")

    summary: dict[str, Any] = {
        "step": "STEP 5 - sensitivity, robustness, and final analytical QA",
        "methodology": {
            "canonical_weights": BASE_WEIGHTS,
            "canonical_score_is_rewritten": False,
            "weight_sensitivity": (
                "one component at a time multiplied by 0.8 or 1.2, "
                "plus equal 20/20/20/20/20 weights"
            ),
            "ranking_scope": "score_status=complete only",
            "interpretation": (
                "Robust rankings indicate prioritization stability under tested "
                "exploratory weights; they are not probabilities or official policy thresholds."
            ),
        },
        "baseline_contract": {
            "target_rows": int(len(risk)),
            "score_status_counts": status_counts,
            "complete_scored_rows": int(complete_mask.sum()),
            "route_unavailable_rows": route_unavailable,
            "capacity_missing_complete_core_rows": missing_capacity,
            "core_data_incomplete_rows": core_incomplete,
        },
        "weight_sensitivity": {
            "scenario_count": len(scenarios),
            "top10pct_n": top10_n,
            "top50_n": top50_n,
            "robust_top10pct_all_scenarios_n": len(robust_top10),
            "robust_top50_all_scenarios_n": len(robust_top50),
            "rank_range_median": safe_float(scored["rank_range"].median()),
            "rank_range_p90": safe_float(scored["rank_range"].quantile(0.90)),
            "rank_range_max": safe_float(scored["rank_range"].max()),
            "scenarios": scenario_summaries,
        },
        "demand_sensitivity": {
            "area_weighted_total_people_equivalent": area_total,
            "full_mesh_total_population": full_total,
            "full_to_area_weighted_ratio": (
                full_total / area_total if area_total > 0 else None
            ),
            "over_capacity_area_weighted_shelters": over_area,
            "over_capacity_full_mesh_shelters": over_full,
            "additional_over_capacity_shelters_full_mesh": over_full - over_area,
            "complete_score_full_mesh_sensitivity_median": safe_float(
                full_mesh_score.median()
            ),
        },
        "missing_data_impact": {
            "complete": int(complete_mask.sum()),
            "core_only_missing_capacity": missing_capacity,
            "core_data_incomplete": core_incomplete,
            "route_unavailable": route_unavailable,
            "excluded_from_weight_ranking_total": int((~complete_mask).sum()),
            "rule": "missing/unavailable rows remain explicit and are never filled or ranked as low risk",
        },
        "release_gate": {"pass": not failures, "failures": failures},
    }
    return scored, summary, failures


def write_json(path: pathlib.Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-data-root", type=pathlib.Path, required=True)
    parser.add_argument("--out-mesh-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-summary", type=pathlib.Path, required=True)
    parser.add_argument("--out-qa", type=pathlib.Path, required=True)
    parser.add_argument("--expected-rows", type=int, default=EXPECTED_TARGET_ROWS)
    args = parser.parse_args()

    risk = load_public_risk(args.public_data_root)
    shelters = load_public_shelters(args.public_data_root)
    scored, summary, failures = build_step5(
        risk, shelters, expected_rows=args.expected_rows
    )

    args.out_mesh_csv.parent.mkdir(parents=True, exist_ok=True)
    scored.to_csv(args.out_mesh_csv, index=False, encoding="utf-8-sig")
    write_json(args.out_summary, summary)
    write_json(args.out_qa, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit("STEP 5 release gate failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
