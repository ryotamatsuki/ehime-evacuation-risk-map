#!/usr/bin/env python3
"""STEP 4: aggregate evacuation demand by selected shelter and recalculate risk.

This stage consumes the verified STEP 3 route-exposure table (1,090 tsunami-
target meshes; 1,062 complete routes).  It fixes two legacy methodological
problems:

1. shelter pressure is calculated from the *sum of demand assigned to each
   selected shelter*, not mesh_population / shelter_capacity independently for
   every mesh;
2. a missing capacity value is never treated as zero and the five-component
   score is never silently re-normalized over the remaining four components.

Demand is a scenario/proxy, not an evacuee forecast:
- primary: total_population * tsunami_inundation_ratio, which assumes population
  is distributed uniformly inside the 500 m mesh;
- sensitivity: the full mesh population evacuates.

The existing 25/20/25/15/15 weights remain an exploratory PoC convention and
are not an official policy standard.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import unicodedata

import numpy as np
import pandas as pd

EXPECTED_TARGET_ROWS = 1090
EXPECTED_COMPLETE_ROUTES = 1062
EXPECTED_ROUTE_FAILURES = 28

FULL_WEIGHTS = {
    "tsunami_exposure": 25.0,
    "vulnerable_population": 20.0,
    "walking_accessibility": 25.0,
    "route_inundation_exposure": 15.0,
    "shelter_capacity_pressure": 15.0,
}
CORE_WEIGHTS = {key: value for key, value in FULL_WEIGHTS.items() if key != "shelter_capacity_pressure"}

LABELS = {
    "tsunami_exposure": "津波浸水曝露が大きい",
    "vulnerable_population": "65歳以上人口割合が高い",
    "walking_accessibility": "避難場所までの徒歩距離が長い",
    "route_inundation_exposure": "避難経路の津波浸水曝露が大きい",
    "shelter_capacity_pressure": "割当需要に対して避難場所の収容余力が小さい",
}


def normalize_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def shelter_key(common_id: object, name: object) -> str:
    return f"{normalize_text(common_id)}||{normalize_text(name)}"


def parse_capacity(value: object) -> tuple[float | None, str]:
    """Parse an official capacity value without coercing missing values to zero."""
    if value is None or pd.isna(value):
        return None, "missing"
    text = normalize_text(value)
    match = re.match(r"^([0-9][0-9,]*(?:\.[0-9]+)?)", text)
    if not match:
        return None, "unparseable"
    number = float(match.group(1).replace(",", ""))
    if not np.isfinite(number) or number <= 0:
        return None, "nonpositive"
    return number, "numeric_prefix" if text != match.group(1) else "numeric"


def prepare_capacities(shelters: pd.DataFrame) -> tuple[pd.DataFrame, set[str]]:
    frame = shelters.copy()
    frame["tsunami_flag"] = pd.to_numeric(frame["tsunami"], errors="coerce")
    frame = frame.loc[frame["tsunami_flag"].eq(1)].copy()
    frame["common_id"] = frame["common_id"].map(normalize_text)
    frame["name"] = frame["name"].map(normalize_text)
    frame = frame.loc[frame["common_id"].ne("") & frame["name"].ne("")].copy()
    frame["shelter_key"] = [shelter_key(cid, name) for cid, name in zip(frame["common_id"], frame["name"])]

    parsed = frame["capacity"].map(parse_capacity)
    frame["shelter_capacity"] = parsed.map(lambda item: item[0])
    frame["capacity_parse_status"] = parsed.map(lambda item: item[1])
    frame["capacity_record_count"] = 1

    city = frame.get("address_city", pd.Series("", index=frame.index)).map(normalize_text)
    lat = pd.to_numeric(frame.get("latitude"), errors="coerce")
    lon = pd.to_numeric(frame.get("longitude"), errors="coerce")
    frame["facility_signature"] = [
        f"{c}|{'' if pd.isna(y) else round(float(y), 6)}|{'' if pd.isna(x) else round(float(x), 6)}"
        for c, y, x in zip(city, lat, lon)
    ]

    signature_counts = frame.groupby("shelter_key")["facility_signature"].nunique(dropna=False)
    ambiguous_keys = set(signature_counts[signature_counts.gt(1)].index.astype(str))

    def first_nonempty(values: pd.Series) -> str | None:
        for value in values:
            text = normalize_text(value)
            if text:
                return text
        return None

    grouped = frame.groupby("shelter_key", as_index=False).agg(
        common_id=("common_id", "first"),
        shelter_name=("name", "first"),
        shelter_city=("address_city", first_nonempty) if "address_city" in frame.columns else ("name", lambda _v: None),
        shelter_capacity=("shelter_capacity", lambda values: values.sum(min_count=1)),
        capacity_record_count=("capacity_record_count", "sum"),
        capacity_parse_status=("capacity_parse_status", lambda values: ";".join(sorted(set(values)))),
        facility_signature_count=("facility_signature", "nunique"),
    )
    grouped["capacity_status"] = np.select(
        [grouped["shelter_capacity"].notna() & grouped["shelter_capacity"].gt(0), grouped["shelter_capacity"].isna()],
        ["available", "missing"],
        default="invalid",
    )
    return grouped, ambiguous_keys


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


def weighted_score(components: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Return a score only when every required component is present."""
    ordered = list(weights)
    ready = components[ordered].notna().all(axis=1)
    denominator = float(sum(weights.values()))
    weighted = components[ordered].mul(pd.Series(weights), axis="columns").sum(axis=1)
    return (weighted / denominator).where(ready).clip(0.0, 100.0)


def risk_reasons(row: pd.Series) -> str:
    component_fields = {
        "tsunami_exposure": "tsunami_exposure_component",
        "vulnerable_population": "vulnerable_population_component",
        "walking_accessibility": "walking_accessibility_component",
        "route_inundation_exposure": "route_inundation_exposure_component",
        "shelter_capacity_pressure": "shelter_capacity_pressure_component_area_weighted",
    }
    items: list[dict[str, object]] = []
    for key, field in component_fields.items():
        value = row.get(field)
        if value is None or pd.isna(value):
            continue
        items.append(
            {
                "key": key,
                "label": LABELS[key],
                "component_score": round(float(value), 3),
                "weighted_contribution": round(float(value) * FULL_WEIGHTS[key] / 100.0, 3),
            }
        )
    items.sort(key=lambda item: float(item["weighted_contribution"]), reverse=True)
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def build_step4(
    route_exposure: pd.DataFrame,
    population: pd.DataFrame,
    tsunami: pd.DataFrame,
    shelters: pd.DataFrame,
    expected_rows: int = EXPECTED_TARGET_ROWS,
    expected_complete_routes: int = EXPECTED_COMPLETE_ROUTES,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object], list[str]]:
    failures: list[str] = []

    routes = route_exposure.copy()
    pop = population.copy()
    exposure = tsunami.copy()
    for frame in (routes, pop, exposure):
        frame["mesh_id"] = frame["mesh_id"].astype(str)

    if len(routes) != expected_rows:
        failures.append(f"STEP 3 row count={len(routes)} expected={expected_rows}")
    if routes["mesh_id"].nunique() != len(routes):
        failures.append("duplicate mesh_id in STEP 3 input")

    source_complete = routes["route_status"].astype(str).eq("complete")
    if int(source_complete.sum()) != expected_complete_routes:
        failures.append(f"complete routes={int(source_complete.sum())} expected={expected_complete_routes}")
    if int((~source_complete).sum()) != expected_rows - expected_complete_routes:
        failures.append("route-failure count differs from expected target-minus-complete count")

    target_exposure = exposure.copy()
    target_exposure["tsunami_inundation_ratio"] = pd.to_numeric(target_exposure["tsunami_inundation_ratio"], errors="coerce")
    target_exposure = target_exposure.loc[target_exposure["tsunami_inundation_ratio"].gt(0)].copy()
    target_ids = set(target_exposure["mesh_id"])
    route_ids = set(routes["mesh_id"])
    if len(target_exposure) != expected_rows:
        failures.append(f"source tsunami target count={len(target_exposure)} expected={expected_rows}")
    if target_ids != route_ids:
        failures.append("STEP 3 mesh set differs from tsunami target mesh set")

    pop_columns = [
        "mesh_id",
        "total_population",
        "aging_rate_65plus",
        "aging_rate_75plus",
        "population_status",
        "age_quality_flag",
    ]
    pop_columns = [column for column in pop_columns if column in pop.columns]
    tsunami_columns = ["mesh_id", "tsunami_inundation_ratio", "tsunami_exposure_score", "tsunami_max_depth_class"]
    tsunami_columns = [column for column in tsunami_columns if column in exposure.columns]

    routes = routes.drop(columns=[column for column in pop_columns + tsunami_columns if column != "mesh_id"], errors="ignore")
    result = routes.merge(pop[pop_columns], on="mesh_id", how="left", validate="one_to_one")
    result = result.merge(exposure[tsunami_columns], on="mesh_id", how="left", validate="one_to_one")

    numeric_columns = [
        "total_population",
        "aging_rate_65plus",
        "aging_rate_75plus",
        "tsunami_inundation_ratio",
        "tsunami_exposure_score",
        "total_walking_distance_m",
        "route_inundation_ratio",
        "route_inundation_ratio_classified",
        "route_unknown_ratio",
    ]
    for column in numeric_columns:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")

    if result["total_population"].isna().any():
        failures.append(f"target meshes missing total_population={int(result['total_population'].isna().sum())}")
    invalid_ratio = ~result["tsunami_inundation_ratio"].between(0, 1)
    if invalid_ratio.any():
        failures.append(f"invalid tsunami_inundation_ratio rows={int(invalid_ratio.sum())}")

    # Demand proxy and full-mesh sensitivity scenario.
    result["mesh_evacuation_demand_area_weighted"] = result["total_population"] * result["tsunami_inundation_ratio"]
    result["mesh_evacuation_demand_full_mesh"] = result["total_population"]
    result["demand_scenario_note"] = (
        "area_weighted assumes population is uniformly distributed within the 500m mesh; full_mesh is a sensitivity scenario; neither is an evacuee forecast"
    )

    assigned = result["route_status"].astype(str).eq("complete")
    result["demand_assignment_status"] = np.where(assigned, "assigned_to_selected_shelter", "route_unavailable_unassigned")
    result["selected_shelter_key"] = [
        shelter_key(cid, name) if is_assigned else ""
        for cid, name, is_assigned in zip(
            result.get("selected_shelter_common_id", pd.Series("", index=result.index)),
            result.get("selected_shelter_name", pd.Series("", index=result.index)),
            assigned,
        )
    ]
    if result.loc[assigned, "selected_shelter_key"].eq("||").any() or result.loc[assigned, "selected_shelter_key"].eq("").any():
        failures.append("complete route missing selected shelter identity")

    capacity_table, ambiguous_keys = prepare_capacities(shelters)
    selected_keys = set(result.loc[assigned, "selected_shelter_key"])
    selected_ambiguous = sorted(selected_keys & ambiguous_keys)
    if selected_ambiguous:
        failures.append(f"ambiguous selected shelter identities={len(selected_ambiguous)}")

    assigned_rows = result.loc[assigned].copy()
    assigned_rows["cross_border_bool"] = assigned_rows.get("cross_border", False).astype(str).str.lower().eq("true")
    shelter_demand = assigned_rows.groupby("selected_shelter_key", as_index=False).agg(
        selected_shelter_common_id=("selected_shelter_common_id", "first"),
        selected_shelter_name=("selected_shelter_name", "first"),
        shelter_municipality_code=("shelter_municipality_code", "first"),
        assigned_mesh_count=("mesh_id", "size"),
        cross_border_assigned_mesh_count=("cross_border_bool", "sum"),
        assigned_demand_area_weighted=("mesh_evacuation_demand_area_weighted", "sum"),
        assigned_demand_full_mesh=("mesh_evacuation_demand_full_mesh", "sum"),
    )
    shelter_summary = shelter_demand.merge(
        capacity_table,
        left_on="selected_shelter_key",
        right_on="shelter_key",
        how="left",
        validate="one_to_one",
        suffixes=("", "_capacity_source"),
    )
    shelter_summary["shelter_capacity"] = pd.to_numeric(shelter_summary["shelter_capacity"], errors="coerce")
    capacity_ready = shelter_summary["shelter_capacity"].gt(0)
    shelter_summary["capacity_pressure_area_weighted"] = (
        shelter_summary["assigned_demand_area_weighted"] / shelter_summary["shelter_capacity"]
    ).where(capacity_ready)
    shelter_summary["capacity_pressure_full_mesh"] = (
        shelter_summary["assigned_demand_full_mesh"] / shelter_summary["shelter_capacity"]
    ).where(capacity_ready)
    shelter_summary["capacity_component_status"] = np.where(capacity_ready, "complete", "missing_capacity")
    shelter_summary["capacity_pressure_interpretation"] = (
        "assigned scenario demand divided by official shelter capacity; values above 1 mean assigned demand exceeds recorded capacity under that scenario"
    )

    selected_unresolved = shelter_summary["shelter_key"].isna()
    if selected_unresolved.any():
        failures.append(f"selected shelters absent from tsunami shelter source={int(selected_unresolved.sum())}")

    merge_columns = [
        "selected_shelter_key",
        "assigned_mesh_count",
        "assigned_demand_area_weighted",
        "assigned_demand_full_mesh",
        "shelter_capacity",
        "capacity_parse_status",
        "capacity_record_count",
        "capacity_pressure_area_weighted",
        "capacity_pressure_full_mesh",
        "capacity_component_status",
    ]
    result = result.merge(shelter_summary[merge_columns], on="selected_shelter_key", how="left", validate="many_to_one")
    for column in [
        "assigned_mesh_count",
        "assigned_demand_area_weighted",
        "assigned_demand_full_mesh",
        "shelter_capacity",
        "capacity_pressure_area_weighted",
        "capacity_pressure_full_mesh",
    ]:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    # Capacity pressure must be the shelter-level aggregate repeated for every
    # mesh assigned to the same shelter. Unassigned routes intentionally remain null.
    if result.loc[~assigned, "capacity_pressure_area_weighted"].notna().any():
        failures.append("route-unavailable row has capacity pressure")
    missing_capacity_assigned = assigned & result["shelter_capacity"].isna()
    if result.loc[missing_capacity_assigned, "capacity_pressure_area_weighted"].notna().any():
        failures.append("missing capacity was converted into a numeric pressure")

    if len(shelter_summary):
        recomputed_area = shelter_summary["assigned_demand_area_weighted"] / shelter_summary["shelter_capacity"]
        mismatch = capacity_ready & (recomputed_area - shelter_summary["capacity_pressure_area_weighted"]).abs().gt(1e-9)
        if mismatch.any():
            failures.append("shelter area-weighted capacity pressure formula mismatch")

    total_area = float(result["mesh_evacuation_demand_area_weighted"].sum())
    assigned_area = float(result.loc[assigned, "mesh_evacuation_demand_area_weighted"].sum())
    unassigned_area = float(result.loc[~assigned, "mesh_evacuation_demand_area_weighted"].sum())
    total_full = float(result["mesh_evacuation_demand_full_mesh"].sum())
    assigned_full = float(result.loc[assigned, "mesh_evacuation_demand_full_mesh"].sum())
    unassigned_full = float(result.loc[~assigned, "mesh_evacuation_demand_full_mesh"].sum())
    if abs(total_area - assigned_area - unassigned_area) > 1e-6:
        failures.append("area-weighted demand conservation failed")
    if abs(total_full - assigned_full - unassigned_full) > 1e-6:
        failures.append("full-mesh demand conservation failed")
    if (result["mesh_evacuation_demand_area_weighted"] - result["mesh_evacuation_demand_full_mesh"] > 1e-9).any():
        failures.append("area-weighted demand exceeds full-mesh demand")

    # Risk components. Explicit route failures never receive a numeric risk
    # score: they remain a separate critical operational/data status.
    route_ready = assigned & result["route_exposure_status"].astype(str).eq("complete")
    walking_low, walking_high = percentile_bounds(result.loc[route_ready, "total_walking_distance_m"])
    result["tsunami_exposure_component"] = pd.to_numeric(result["tsunami_exposure_score"], errors="coerce").clip(0.0, 100.0)
    result["vulnerable_population_component"] = (pd.to_numeric(result["aging_rate_65plus"], errors="coerce") * 100.0).clip(0.0, 100.0)
    result["walking_accessibility_component"] = normalize_walking(result["total_walking_distance_m"], walking_low, walking_high).where(route_ready)

    route_lower = (pd.to_numeric(result["route_inundation_ratio"], errors="coerce") * 100.0).clip(0.0, 100.0)
    route_point = (pd.to_numeric(result["route_inundation_ratio_classified"], errors="coerce") * 100.0).clip(0.0, 100.0)
    route_upper = ((pd.to_numeric(result["route_inundation_ratio"], errors="coerce") + pd.to_numeric(result["route_unknown_ratio"], errors="coerce")) * 100.0).clip(0.0, 100.0)
    result["route_inundation_exposure_component_lower"] = route_lower.where(route_ready)
    result["route_inundation_exposure_component"] = route_point.where(route_ready)
    result["route_inundation_exposure_component_upper"] = route_upper.where(route_ready)
    result["route_exposure_uncertainty_flag"] = route_ready & pd.to_numeric(result["route_unknown_ratio"], errors="coerce").gt(0)

    result["shelter_capacity_pressure_component_area_weighted"] = (
        result["capacity_pressure_area_weighted"] * 100.0
    ).clip(0.0, 100.0).where(route_ready)
    result["shelter_capacity_pressure_component_full_mesh"] = (
        result["capacity_pressure_full_mesh"] * 100.0
    ).clip(0.0, 100.0).where(route_ready)

    core_base = pd.DataFrame(
        {
            "tsunami_exposure": result["tsunami_exposure_component"].where(route_ready),
            "vulnerable_population": result["vulnerable_population_component"].where(route_ready),
            "walking_accessibility": result["walking_accessibility_component"].where(route_ready),
            "route_inundation_exposure": result["route_inundation_exposure_component"].where(route_ready),
        }
    )
    core_lower = core_base.copy()
    core_upper = core_base.copy()
    core_lower["route_inundation_exposure"] = result["route_inundation_exposure_component_lower"].where(route_ready)
    core_upper["route_inundation_exposure"] = result["route_inundation_exposure_component_upper"].where(route_ready)
    result["core_evacuation_difficulty_score"] = weighted_score(core_base, CORE_WEIGHTS)
    result["core_evacuation_difficulty_score_lower"] = weighted_score(core_lower, CORE_WEIGHTS)
    result["core_evacuation_difficulty_score_upper"] = weighted_score(core_upper, CORE_WEIGHTS)

    full_area = core_base.copy()
    full_area["shelter_capacity_pressure"] = result["shelter_capacity_pressure_component_area_weighted"].where(route_ready)
    full_area_lower = core_lower.copy()
    full_area_lower["shelter_capacity_pressure"] = result["shelter_capacity_pressure_component_area_weighted"].where(route_ready)
    full_area_upper = core_upper.copy()
    full_area_upper["shelter_capacity_pressure"] = result["shelter_capacity_pressure_component_area_weighted"].where(route_ready)
    full_mesh = core_base.copy()
    full_mesh["shelter_capacity_pressure"] = result["shelter_capacity_pressure_component_full_mesh"].where(route_ready)

    result["evacuation_difficulty_score"] = weighted_score(full_area, FULL_WEIGHTS)
    result["evacuation_difficulty_score_lower"] = weighted_score(full_area_lower, FULL_WEIGHTS)
    result["evacuation_difficulty_score_upper"] = weighted_score(full_area_upper, FULL_WEIGHTS)
    result["evacuation_difficulty_score_full_mesh_sensitivity"] = weighted_score(full_mesh, FULL_WEIGHTS)

    core_fields = [
        "tsunami_exposure_component",
        "vulnerable_population_component",
        "walking_accessibility_component",
        "route_inundation_exposure_component",
    ]
    full_fields = core_fields + ["shelter_capacity_pressure_component_area_weighted"]
    result["core_data_completeness"] = result[core_fields].notna().sum(axis=1) / len(core_fields)
    result["data_completeness"] = result[full_fields].notna().sum(axis=1) / len(full_fields)
    result["data_completeness_pct"] = result["data_completeness"] * 100.0
    result["score_status"] = np.select(
        [
            ~route_ready,
            result["core_evacuation_difficulty_score"].isna(),
            result["evacuation_difficulty_score"].isna(),
        ],
        [
            "route_unavailable",
            "core_data_incomplete",
            "core_only_missing_capacity",
        ],
        default="complete",
    )
    result["risk_reasons"] = result.apply(risk_reasons, axis=1)
    result["score_method_note"] = (
        "PoC exploratory weights 25/20/25/15/15. Full score requires all five components; missing capacity is not re-normalized away. Route failures receive no numeric score."
    )
    result["route_exposure_score_uncertainty_note"] = (
        "point estimate uses inundation share among classified route coverage; lower/upper scores bound unknown raster coverage as dry/inundated respectively"
    )

    # Release gates for scores.
    for column in [
        "core_evacuation_difficulty_score",
        "core_evacuation_difficulty_score_lower",
        "core_evacuation_difficulty_score_upper",
        "evacuation_difficulty_score",
        "evacuation_difficulty_score_lower",
        "evacuation_difficulty_score_upper",
        "evacuation_difficulty_score_full_mesh_sensitivity",
    ]:
        values = pd.to_numeric(result[column], errors="coerce").dropna()
        if (~values.between(0, 100)).any():
            failures.append(f"score outside [0,100]: {column}")
    if result.loc[~route_ready, "core_evacuation_difficulty_score"].notna().any():
        failures.append("route-unavailable row received a core numeric score")
    if result.loc[~route_ready, "evacuation_difficulty_score"].notna().any():
        failures.append("route-unavailable row received a full numeric score")
    if result.loc[missing_capacity_assigned, "evacuation_difficulty_score"].notna().any():
        failures.append("missing-capacity row received a five-component score")

    score_ready = result["evacuation_difficulty_score"].notna()
    if score_ready.any():
        point = result.loc[score_ready, "evacuation_difficulty_score"]
        lower = result.loc[score_ready, "evacuation_difficulty_score_lower"]
        upper = result.loc[score_ready, "evacuation_difficulty_score_upper"]
        if (lower - point > 1e-9).any() or (point - upper > 1e-9).any():
            failures.append("risk uncertainty bounds do not contain point estimate")

    route_status_counts = result["route_status"].value_counts().to_dict()
    score_status_counts = result["score_status"].value_counts().to_dict()
    overload_primary = shelter_summary["capacity_pressure_area_weighted"].gt(1.0)
    overload_full = shelter_summary["capacity_pressure_full_mesh"].gt(1.0)
    capacity_known = shelter_summary["shelter_capacity"].gt(0)

    qa: dict[str, object] = {
        "step": "STEP 4 - evacuation demand, shelter capacity pressure, and exploratory risk",
        "input_target_rows": int(len(result)),
        "route_status_counts": route_status_counts,
        "complete_routes": int(route_ready.sum()),
        "route_unavailable_rows": int((~route_ready).sum()),
        "demand": {
            "definition_primary": "total_population * tsunami_inundation_ratio",
            "primary_assumption": "population uniformly distributed within each 500m mesh",
            "definition_sensitivity": "total_population",
            "forecast_warning": "scenario/proxy only; not an actual evacuee forecast",
            "total_area_weighted": total_area,
            "assigned_area_weighted": assigned_area,
            "unassigned_area_weighted": unassigned_area,
            "total_full_mesh": total_full,
            "assigned_full_mesh": assigned_full,
            "unassigned_full_mesh": unassigned_full,
        },
        "shelters": {
            "assigned_shelter_count": int(len(shelter_summary)),
            "capacity_known_assigned_shelters": int(capacity_known.sum()),
            "capacity_missing_assigned_shelters": int((~capacity_known).sum()),
            "assigned_meshes_with_missing_capacity": int(missing_capacity_assigned.sum()),
            "over_capacity_area_weighted_shelters": int(overload_primary.sum()),
            "over_capacity_full_mesh_shelters": int(overload_full.sum()),
            "max_capacity_pressure_area_weighted": float(shelter_summary["capacity_pressure_area_weighted"].max()) if capacity_known.any() else None,
            "max_capacity_pressure_full_mesh": float(shelter_summary["capacity_pressure_full_mesh"].max()) if capacity_known.any() else None,
            "selected_ambiguous_shelter_keys": selected_ambiguous,
        },
        "risk": {
            "weights_full": FULL_WEIGHTS,
            "weights_core": CORE_WEIGHTS,
            "official_policy_standard": False,
            "walking_distance_normalization": {
                "method": "5th_to_95th_percentile among complete STEP 3 routes",
                "p05_m": walking_low,
                "p95_m": walking_high,
            },
            "capacity_component": "min(capacity_pressure_area_weighted * 100, 100)",
            "missing_capacity_handling": "full five-component score is null; no weight renormalization",
            "route_failure_handling": "numeric risk score is null; explicit route failure status retained",
            "route_exposure_uncertainty": "point uses classified coverage; lower/upper bounds treat unknown coverage as dry/inundated",
            "routes_with_unknown_exposure": int(result["route_exposure_uncertainty_flag"].sum()),
            "score_status_counts": score_status_counts,
            "core_score_nonnull": int(result["core_evacuation_difficulty_score"].notna().sum()),
            "full_score_nonnull": int(result["evacuation_difficulty_score"].notna().sum()),
        },
        "release_gate": {"pass": not failures, "failures": failures},
    }
    return result, shelter_summary, qa, failures


def load_csv(path: pathlib.Path, dtypes: dict[str, type] | None = None) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", dtype=dtypes or {})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route-exposure-csv", type=pathlib.Path, required=True)
    parser.add_argument("--population-csv", type=pathlib.Path, required=True)
    parser.add_argument("--tsunami-csv", type=pathlib.Path, required=True)
    parser.add_argument("--shelters-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-mesh-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-shelter-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-qa", type=pathlib.Path, required=True)
    parser.add_argument("--expected-rows", type=int, default=EXPECTED_TARGET_ROWS)
    parser.add_argument("--expected-complete-routes", type=int, default=EXPECTED_COMPLETE_ROUTES)
    args = parser.parse_args()

    route_dtypes = {
        "mesh_id": str,
        "municipality_code": str,
        "selected_shelter_common_id": str,
        "shelter_municipality_code": str,
    }
    routes = load_csv(args.route_exposure_csv, route_dtypes)
    population = load_csv(args.population_csv, {"mesh_id": str, "municipality_code": str})
    tsunami = load_csv(args.tsunami_csv, {"mesh_id": str})
    shelters = load_csv(args.shelters_csv, {"common_id": str})

    mesh, shelter_summary, qa, failures = build_step4(
        routes,
        population,
        tsunami,
        shelters,
        expected_rows=args.expected_rows,
        expected_complete_routes=args.expected_complete_routes,
    )

    args.out_mesh_csv.parent.mkdir(parents=True, exist_ok=True)
    mesh.to_csv(args.out_mesh_csv, index=False, encoding="utf-8-sig")
    args.out_shelter_csv.parent.mkdir(parents=True, exist_ok=True)
    shelter_summary.to_csv(args.out_shelter_csv, index=False, encoding="utf-8-sig")
    args.out_qa.parent.mkdir(parents=True, exist_ok=True)
    args.out_qa.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit("STEP 4 release gate failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
