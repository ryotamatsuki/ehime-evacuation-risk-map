#!/usr/bin/env python3
"""Export verified STEP 4 analysis into GitHub Pages assets.

The public site must not read the legacy 5,821-row routing/risk products after
Analysis Core STEP 4.  This exporter takes the verified 1,090-target STEP 4
mesh table plus shelter-level capacity table and creates compact municipality
JSON assets for the React application.

A final metadata correction is intentionally performed here for shelters whose
``common_id`` does not encode a five-digit municipality code.  The source
shelter table's ``address_city`` is used to resolve the selected shelter
municipality.  This affects cross-border display metadata only; it does not
change the already verified route, distance, exposure, demand, capacity, or
risk calculations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import shutil
from collections import Counter
from typing import Any

import pandas as pd

MUNICIPALITY_ORDER = [
    "38201",
    "38202",
    "38203",
    "38204",
    "38205",
    "38206",
    "38207",
    "38210",
    "38213",
    "38214",
    "38356",
    "38401",
    "38442",
    "38506",
]

EHIME_MUNICIPALITY_CODES = {
    "松山市": "38201",
    "今治市": "38202",
    "宇和島市": "38203",
    "八幡浜市": "38204",
    "新居浜市": "38205",
    "西条市": "38206",
    "大洲市": "38207",
    "伊予市": "38210",
    "四国中央市": "38213",
    "西予市": "38214",
    "東温市": "38215",
    "上島町": "38356",
    "久万高原町": "38386",
    "松前町": "38401",
    "砥部町": "38402",
    "内子町": "38422",
    "伊方町": "38442",
    "松野町": "38484",
    "鬼北町": "38488",
    "愛南町": "38506",
}

RISK_COLUMNS = [
    "mesh_id",
    "municipality_code",
    "municipality",
    "route_status",
    "selected_shelter_common_id",
    "selected_shelter_name",
    "shelter_municipality_code",
    "shelter_municipality_resolution",
    "cross_border",
    "total_population",
    "aging_rate_65plus",
    "aging_rate_75plus",
    "population_status",
    "age_quality_flag",
    "tsunami_inundation_ratio",
    "tsunami_exposure_score",
    "tsunami_max_depth_class",
    "mesh_evacuation_demand_area_weighted",
    "mesh_evacuation_demand_full_mesh",
    "demand_assignment_status",
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
    "tsunami_exposure_component",
    "vulnerable_population_component",
    "walking_accessibility_component",
    "route_inundation_exposure_component_lower",
    "route_inundation_exposure_component",
    "route_inundation_exposure_component_upper",
    "route_exposure_uncertainty_flag",
    "shelter_capacity_pressure_component_area_weighted",
    "shelter_capacity_pressure_component_full_mesh",
    "core_evacuation_difficulty_score",
    "core_evacuation_difficulty_score_lower",
    "core_evacuation_difficulty_score_upper",
    "evacuation_difficulty_score",
    "evacuation_difficulty_score_lower",
    "evacuation_difficulty_score_upper",
    "evacuation_difficulty_score_full_mesh_sensitivity",
    "core_data_completeness",
    "data_completeness",
    "data_completeness_pct",
    "score_status",
    "risk_reasons",
    "score_method_note",
    "route_exposure_score_uncertainty_note",
]

ROUTE_COLUMNS = [
    "mesh_id",
    "municipality_code",
    "municipality",
    "route_status",
    "selected_shelter_common_id",
    "selected_shelter_name",
    "shelter_municipality_code",
    "shelter_municipality_resolution",
    "cross_border",
    "origin_method",
    "origin_access_distance_m",
    "network_path_distance_m",
    "shelter_connector_distance_m",
    "total_walking_distance_m",
    "route_network_coordinates",
    "walking_time_min_1p0mps",
    "walking_time_min_0p62mps",
    "walking_time_min_0p5mps",
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
    "route_modeled_geometry_distance_m",
]

SHELTER_COLUMNS = [
    "selected_shelter_key",
    "selected_shelter_common_id",
    "selected_shelter_name",
    "shelter_municipality_code",
    "shelter_municipality_resolution",
    "assigned_mesh_count",
    "cross_border_assigned_mesh_count",
    "assigned_demand_area_weighted",
    "assigned_demand_full_mesh",
    "shelter_capacity",
    "capacity_record_count",
    "capacity_parse_status",
    "capacity_status",
    "capacity_pressure_area_weighted",
    "capacity_pressure_full_mesh",
    "capacity_component_status",
    "capacity_pressure_interpretation",
]

JSON_COLUMNS = {"route_network_coordinates", "route_inundated_segments", "risk_reasons"}
BOOL_COLUMNS = {"cross_border", "route_exposure_uncertainty_flag"}
CODE_COLUMNS = {"municipality_code", "shelter_municipality_code"}


def nullable_text(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def normalize_code(value: object) -> str | None:
    text = nullable_text(value)
    if text is None:
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def municipality_code_from_city(value: object) -> str | None:
    city = nullable_text(value)
    if city is None:
        return None
    if city in EHIME_MUNICIPALITY_CODES:
        return EHIME_MUNICIPALITY_CODES[city]
    compact = city.replace(" ", "").replace("　", "")
    for name, code in EHIME_MUNICIPALITY_CODES.items():
        if name in compact:
            return code
    return None


def build_shelter_resolver(source: pd.DataFrame) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    by_pair_candidates: dict[tuple[str, str], set[str]] = {}
    by_id_candidates: dict[str, set[str]] = {}
    for row in source.to_dict(orient="records"):
        common_id = nullable_text(row.get("common_id"))
        name = nullable_text(row.get("name"))
        code = municipality_code_from_city(row.get("address_city"))
        if code is None:
            code = municipality_code_from_city(row.get("address"))
        if common_id is None or code is None:
            continue
        by_id_candidates.setdefault(common_id, set()).add(code)
        if name is not None:
            by_pair_candidates.setdefault((common_id, name), set()).add(code)

    by_pair = {key: next(iter(values)) for key, values in by_pair_candidates.items() if len(values) == 1}
    by_id = {key: next(iter(values)) for key, values in by_id_candidates.items() if len(values) == 1}
    return by_pair, by_id


def resolve_shelter_municipality(
    common_id: object,
    name: object,
    existing_code: object,
    pair_resolver: dict[tuple[str, str], str],
    id_resolver: dict[str, str],
) -> tuple[str | None, str]:
    existing = normalize_code(existing_code)
    if existing is not None:
        return existing, "route_output"
    common = nullable_text(common_id)
    shelter_name = nullable_text(name)
    if common is not None and shelter_name is not None and (common, shelter_name) in pair_resolver:
        return pair_resolver[(common, shelter_name)], "shelter_source_common_id_name"
    if common is not None and common in id_resolver:
        return id_resolver[common], "shelter_source_common_id"
    return None, "unresolved"


def prepare_mesh_for_public(
    mesh: pd.DataFrame,
    pair_resolver: dict[tuple[str, str], str],
    id_resolver: dict[str, str],
) -> tuple[pd.DataFrame, dict[str, int]]:
    result = mesh.copy()
    original_cross_border = result["cross_border"].astype(str).str.lower().eq("true")
    resolutions: list[str] = []
    codes: list[str | None] = []
    cross_border: list[bool | None] = []
    resolved_missing = 0

    for row in result.to_dict(orient="records"):
        route_status = nullable_text(row.get("route_status"))
        code, resolution = resolve_shelter_municipality(
            row.get("selected_shelter_common_id"),
            row.get("selected_shelter_name"),
            row.get("shelter_municipality_code"),
            pair_resolver,
            id_resolver,
        )
        if normalize_code(row.get("shelter_municipality_code")) is None and code is not None:
            resolved_missing += 1
        home_code = normalize_code(row.get("municipality_code"))
        if route_status == "complete" and code is not None and home_code is not None:
            border_value: bool | None = code != home_code
        else:
            border_value = None
        codes.append(code)
        resolutions.append(resolution)
        cross_border.append(border_value)

    result["shelter_municipality_code"] = codes
    result["shelter_municipality_resolution"] = resolutions
    result["cross_border"] = cross_border
    resolved_cross_border = result["cross_border"].fillna(False).astype(bool)
    correction = {
        "source_cross_border_true": int(original_cross_border.sum()),
        "resolved_cross_border_true": int(resolved_cross_border.sum()),
        "resolved_missing_shelter_municipality_rows": resolved_missing,
        "additional_cross_border_detected": int((resolved_cross_border & ~original_cross_border).sum()),
    }
    return result, correction


def prepare_shelter_capacity_for_public(
    shelters: pd.DataFrame,
    pair_resolver: dict[tuple[str, str], str],
    id_resolver: dict[str, str],
) -> pd.DataFrame:
    result = shelters.copy()
    codes: list[str | None] = []
    resolutions: list[str] = []
    for row in result.to_dict(orient="records"):
        code, resolution = resolve_shelter_municipality(
            row.get("selected_shelter_common_id"),
            row.get("selected_shelter_name"),
            row.get("shelter_municipality_code"),
            pair_resolver,
            id_resolver,
        )
        codes.append(code)
        resolutions.append(resolution)
    result["shelter_municipality_code"] = codes
    result["shelter_municipality_resolution"] = resolutions
    return result


def jsonable(value: object, key: str) -> Any:
    if key in JSON_COLUMNS:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return []
        if isinstance(value, str):
            if not value.strip():
                return []
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return []
            return parsed
        return value
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if key in BOOL_COLUMNS:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes"}
    if key in CODE_COLUMNS:
        return normalize_code(value)
    if hasattr(value, "item"):
        return value.item()
    return value


def records(frame: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"missing export columns: {missing}")
    output: list[dict[str, Any]] = []
    for row in frame[columns].to_dict(orient="records"):
        output.append({key: jsonable(value, key) for key, value in row.items()})
    return output


def write_json(path: pathlib.Path, payload: Any, *, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
    path.write_text(text + "\n", encoding="utf-8")


def validate_public_export(
    mesh: pd.DataFrame,
    shelter_capacity: pd.DataFrame,
    expected_rows: int,
    expected_complete_routes: int,
    expected_cross_border: int,
) -> tuple[list[str], dict[str, Any]]:
    failures: list[str] = []
    if len(mesh) != expected_rows:
        failures.append(f"target row count={len(mesh)} expected={expected_rows}")
    if mesh["mesh_id"].astype(str).nunique() != len(mesh):
        failures.append("duplicate mesh_id in public export source")
    if set(mesh["municipality_code"].astype(str)) != set(MUNICIPALITY_ORDER):
        failures.append("public export municipality set differs from coastal 14")

    complete = mesh["route_status"].eq("complete")
    if int(complete.sum()) != expected_complete_routes:
        failures.append(
            f"complete routes={int(complete.sum())} expected={expected_complete_routes}"
        )
    unresolved = complete & mesh["shelter_municipality_code"].isna()
    if unresolved.any():
        failures.append(f"complete routes with unresolved shelter municipality={int(unresolved.sum())}")
    cross_border = complete & mesh["cross_border"].fillna(False).astype(bool)
    if int(cross_border.sum()) != expected_cross_border:
        failures.append(
            f"resolved cross-border routes={int(cross_border.sum())} expected={expected_cross_border}"
        )

    expected_status_counts = {
        "complete": 813,
        "core_only_missing_capacity": 128,
        "core_data_incomplete": 121,
        "route_unavailable": 28,
    }
    status_counts = {str(k): int(v) for k, v in mesh["score_status"].value_counts().to_dict().items()}
    if status_counts != expected_status_counts:
        failures.append(f"score status counts differ: {status_counts}")

    full_score = pd.to_numeric(mesh["evacuation_difficulty_score"], errors="coerce")
    if int(full_score.notna().sum()) != expected_status_counts["complete"]:
        failures.append("full five-component score count differs from complete score status")
    if full_score[mesh["score_status"].ne("complete")].notna().any():
        failures.append("non-complete score status has numeric five-component score")
    if full_score[mesh["route_status"].ne("complete")].notna().any():
        failures.append("route-unavailable row has numeric five-component score")

    route_unavailable = int(mesh["route_status"].ne("complete").sum())
    if route_unavailable != expected_rows - expected_complete_routes:
        failures.append("route-unavailable count inconsistent with expected complete routes")

    over_capacity = pd.to_numeric(
        shelter_capacity["capacity_pressure_area_weighted"], errors="coerce"
    ).gt(1)
    if int(over_capacity.sum()) != 35:
        failures.append(f"area-weighted over-capacity shelter count={int(over_capacity.sum())} expected=35")

    invalid_route_json = 0
    invalid_segment_json = 0
    for row in mesh.loc[complete].to_dict(orient="records"):
        try:
            coordinates = json.loads(str(row["route_network_coordinates"]))
            if not isinstance(coordinates, list) or len(coordinates) < 1:
                invalid_route_json += 1
        except Exception:  # noqa: BLE001
            invalid_route_json += 1
        try:
            segments = json.loads(str(row["route_inundated_segments"]))
            if not isinstance(segments, list):
                invalid_segment_json += 1
        except Exception:  # noqa: BLE001
            invalid_segment_json += 1
    if invalid_route_json:
        failures.append(f"invalid complete route geometry JSON={invalid_route_json}")
    if invalid_segment_json:
        failures.append(f"invalid inundated-segment JSON={invalid_segment_json}")

    summary = {
        "step": "Corrected Final Export & Deployment - public data gate",
        "target_rows": int(len(mesh)),
        "complete_routes": int(complete.sum()),
        "route_unavailable": route_unavailable,
        "resolved_cross_border_routes": int(cross_border.sum()),
        "score_status_counts": status_counts,
        "full_score_rows": int(full_score.notna().sum()),
        "assigned_shelters": int(len(shelter_capacity)),
        "over_capacity_area_weighted_shelters": int(over_capacity.sum()),
        "release_gate": {"pass": not failures, "failures": failures},
    }
    return failures, summary


def build_manifest(out_root: pathlib.Path, relative_paths: list[pathlib.Path]) -> dict[str, dict[str, object]]:
    manifest: dict[str, dict[str, object]] = {}
    for path in sorted(relative_paths):
        full_path = out_root / path
        manifest[path.as_posix()] = {
            "sha256": hashlib.sha256(full_path.read_bytes()).hexdigest(),
            "bytes": full_path.stat().st_size,
        }
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mesh-analysis-csv", type=pathlib.Path, required=True)
    parser.add_argument("--shelter-capacity-csv", type=pathlib.Path, required=True)
    parser.add_argument("--step4-qa", type=pathlib.Path, required=True)
    parser.add_argument("--shelter-source-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-root", type=pathlib.Path, required=True)
    parser.add_argument("--out-qa", type=pathlib.Path, required=True)
    parser.add_argument("--expected-rows", type=int, default=1090)
    parser.add_argument("--expected-complete-routes", type=int, default=1062)
    parser.add_argument("--expected-cross-border", type=int, default=13)
    parser.add_argument("--analysis-source-sha", default="")
    parser.add_argument("--workflow-run-id", default="")
    args = parser.parse_args()

    mesh_dtypes = {
        "mesh_id": str,
        "municipality_code": str,
        "selected_shelter_common_id": str,
        "shelter_municipality_code": str,
        "selected_shelter_key": str,
    }
    mesh = pd.read_csv(args.mesh_analysis_csv, encoding="utf-8-sig", dtype=mesh_dtypes)
    shelter_capacity = pd.read_csv(
        args.shelter_capacity_csv,
        encoding="utf-8-sig",
        dtype={
            "selected_shelter_common_id": str,
            "shelter_municipality_code": str,
            "selected_shelter_key": str,
        },
    )
    shelter_source = pd.read_csv(
        args.shelter_source_csv,
        encoding="utf-8-sig",
        dtype={"common_id": str},
    )
    step4_qa = json.loads(args.step4_qa.read_text(encoding="utf-8"))

    pair_resolver, id_resolver = build_shelter_resolver(shelter_source)
    mesh, cross_border_correction = prepare_mesh_for_public(mesh, pair_resolver, id_resolver)
    shelter_capacity = prepare_shelter_capacity_for_public(
        shelter_capacity, pair_resolver, id_resolver
    )

    failures, qa = validate_public_export(
        mesh,
        shelter_capacity,
        args.expected_rows,
        args.expected_complete_routes,
        args.expected_cross_border,
    )
    qa["cross_border_metadata_correction"] = cross_border_correction
    qa["analysis_source_sha"] = args.analysis_source_sha or None
    qa["workflow_run_id"] = args.workflow_run_id or None

    risk_dir = args.out_root / "risk"
    route_dir = args.out_root / "routes"
    if risk_dir.exists():
        shutil.rmtree(risk_dir)
    if route_dir.exists():
        shutil.rmtree(route_dir)
    risk_dir.mkdir(parents=True, exist_ok=True)
    route_dir.mkdir(parents=True, exist_ok=True)

    risk_index: list[dict[str, object]] = []
    route_index: list[dict[str, object]] = []
    generated_paths: list[pathlib.Path] = []

    for code in MUNICIPALITY_ORDER:
        subset = mesh[mesh["municipality_code"].astype(str).eq(code)].copy()
        if subset.empty:
            failures.append(f"no public rows for municipality {code}")
            continue
        municipality = str(subset["municipality"].iloc[0])
        risk_payload = records(subset, RISK_COLUMNS)
        route_payload = records(subset, ROUTE_COLUMNS)
        risk_relative = pathlib.Path("risk") / f"risk_mesh_{code}.json"
        route_relative = pathlib.Path("routes") / f"routes_{code}.json"
        write_json(args.out_root / risk_relative, risk_payload)
        write_json(args.out_root / route_relative, route_payload)
        generated_paths.extend([risk_relative, route_relative])
        risk_index.append(
            {
                "municipality_code": code,
                "municipality": municipality,
                "file": risk_relative.as_posix(),
                "feature_count": len(risk_payload),
            }
        )
        route_index.append(
            {
                "municipality_code": code,
                "municipality": municipality,
                "file": route_relative.as_posix(),
                "feature_count": len(route_payload),
            }
        )

    risk_index_relative = pathlib.Path("risk/index.json")
    route_index_relative = pathlib.Path("routes/index.json")
    write_json(args.out_root / risk_index_relative, risk_index, pretty=True)
    write_json(args.out_root / route_index_relative, route_index, pretty=True)
    generated_paths.extend([risk_index_relative, route_index_relative])

    capacity_relative = pathlib.Path("shelters/capacity_pressure.json")
    write_json(args.out_root / capacity_relative, records(shelter_capacity, SHELTER_COLUMNS))
    generated_paths.append(capacity_relative)

    analysis_metadata = {
        "analysis_version": "analysis-core-v4-corrected-public",
        "target_definition": "tsunami_inundation_ratio > 0",
        "target_meshes": int(len(mesh)),
        "complete_routes": int(mesh["route_status"].eq("complete").sum()),
        "route_unavailable": int(mesh["route_status"].ne("complete").sum()),
        "cross_border_routes": int(
            (mesh["route_status"].eq("complete") & mesh["cross_border"].fillna(False).astype(bool)).sum()
        ),
        "cross_border_metadata_correction": cross_border_correction,
        "score_status_counts": {
            str(k): int(v) for k, v in mesh["score_status"].value_counts().to_dict().items()
        },
        "demand": step4_qa.get("demand"),
        "shelters": step4_qa.get("shelters"),
        "risk": step4_qa.get("risk"),
        "analysis_source_sha": args.analysis_source_sha or None,
        "workflow_run_id": args.workflow_run_id or None,
        "methodology_note": (
            "Corrected STEP 2-4 outputs. Evacuation demand is a scenario/proxy; "
            "the 25/20/25/15/15 composite is exploratory and not an official policy standard. "
            "Selected-shelter municipality metadata is resolved from official shelter address_city "
            "when common_id alone does not encode a municipality."
        ),
    }
    analysis_relative = pathlib.Path("metadata/analysis.json")
    write_json(args.out_root / analysis_relative, analysis_metadata, pretty=True)
    generated_paths.append(analysis_relative)

    manifest_relative = pathlib.Path("metadata/corrected_manifest.json")
    manifest = build_manifest(args.out_root, generated_paths)
    write_json(args.out_root / manifest_relative, manifest, pretty=True)

    qa["generated_file_count"] = len(generated_paths) + 1
    qa["risk_index_count"] = int(sum(item["feature_count"] for item in risk_index))
    qa["route_index_count"] = int(sum(item["feature_count"] for item in route_index))
    qa["release_gate"] = {"pass": not failures, "failures": failures}
    args.out_qa.parent.mkdir(parents=True, exist_ok=True)
    args.out_qa.write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit("final public export gate failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
