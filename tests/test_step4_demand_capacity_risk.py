from __future__ import annotations

import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from calculate_step4_demand_capacity_risk import (  # noqa: E402
    build_step4,
    parse_capacity,
    shelter_key,
)


def sample_frames():
    routes = pd.DataFrame(
        [
            {
                "mesh_id": "m1",
                "municipality_code": "38201",
                "route_status": "complete",
                "route_exposure_status": "complete",
                "selected_shelter_common_id": "S1",
                "selected_shelter_name": "Shelter 1",
                "shelter_municipality_code": "38201",
                "cross_border": False,
                "total_walking_distance_m": 500.0,
                "route_inundation_ratio": 0.20,
                "route_inundation_ratio_classified": 0.20,
                "route_unknown_ratio": 0.0,
            },
            {
                "mesh_id": "m2",
                "municipality_code": "38201",
                "route_status": "complete",
                "route_exposure_status": "complete",
                "selected_shelter_common_id": "S1",
                "selected_shelter_name": "Shelter 1",
                "shelter_municipality_code": "38201",
                "cross_border": False,
                "total_walking_distance_m": 1000.0,
                "route_inundation_ratio": 0.40,
                "route_inundation_ratio_classified": 0.40,
                "route_unknown_ratio": 0.0,
            },
            {
                "mesh_id": "m3",
                "municipality_code": "38202",
                "route_status": "complete",
                "route_exposure_status": "complete",
                "selected_shelter_common_id": "01201",
                "selected_shelter_name": "Shelter 2",
                "shelter_municipality_code": None,
                "cross_border": None,
                "total_walking_distance_m": 1500.0,
                "route_inundation_ratio": 0.30,
                "route_inundation_ratio_classified": 0.375,
                "route_unknown_ratio": 0.20,
            },
            {
                "mesh_id": "m4",
                "municipality_code": "38203",
                "route_status": "no_network_path",
                "route_exposure_status": "route_unavailable",
                "selected_shelter_common_id": None,
                "selected_shelter_name": None,
                "shelter_municipality_code": None,
                "cross_border": None,
                "total_walking_distance_m": None,
                "route_inundation_ratio": None,
                "route_inundation_ratio_classified": None,
                "route_unknown_ratio": None,
            },
        ]
    )
    population = pd.DataFrame(
        [
            {"mesh_id": "m1", "total_population": 100, "aging_rate_65plus": 0.20, "aging_rate_75plus": 0.10},
            {"mesh_id": "m2", "total_population": 200, "aging_rate_65plus": 0.30, "aging_rate_75plus": 0.15},
            {"mesh_id": "m3", "total_population": 80, "aging_rate_65plus": 0.40, "aging_rate_75plus": 0.20},
            {"mesh_id": "m4", "total_population": 50, "aging_rate_65plus": 0.25, "aging_rate_75plus": 0.12},
        ]
    )
    tsunami = pd.DataFrame(
        [
            {"mesh_id": "m1", "tsunami_inundation_ratio": 0.50, "tsunami_exposure_score": 40, "tsunami_max_depth_class": 2},
            {"mesh_id": "m2", "tsunami_inundation_ratio": 0.25, "tsunami_exposure_score": 50, "tsunami_max_depth_class": 3},
            {"mesh_id": "m3", "tsunami_inundation_ratio": 1.00, "tsunami_exposure_score": 80, "tsunami_max_depth_class": 4},
            {"mesh_id": "m4", "tsunami_inundation_ratio": 0.20, "tsunami_exposure_score": 30, "tsunami_max_depth_class": 2},
        ]
    )
    shelters = pd.DataFrame(
        [
            {
                "common_id": "S1",
                "name": "Shelter 1",
                "address_city": "City 1",
                "latitude": 33.8,
                "longitude": 132.7,
                "tsunami": 1,
                "capacity": 200,
            },
            {
                "common_id": "01201",
                "name": "Shelter 2",
                "address_city": "City 2",
                "latitude": 33.9,
                "longitude": 132.8,
                "tsunami": 1,
                "capacity": None,
            },
        ]
    )
    return routes, population, tsunami, shelters


def test_capacity_parser_never_converts_missing_to_zero():
    assert parse_capacity(None) == (None, "missing")
    assert parse_capacity("") == (None, "unparseable")
    assert parse_capacity("1,200人") == (1200.0, "numeric_prefix")
    assert parse_capacity("0") == (None, "nonpositive")


def test_shelter_key_preserves_leading_zero_id():
    assert shelter_key("01201", "Shelter 2").startswith("01201||")


def test_demand_is_aggregated_by_shelter_before_capacity_pressure():
    routes, population, tsunami, shelters = sample_frames()
    mesh, shelter_summary, qa, failures = build_step4(
        routes, population, tsunami, shelters, expected_rows=4, expected_complete_routes=3
    )
    assert failures == []
    assert qa["release_gate"]["pass"] is True

    s1 = shelter_summary.loc[shelter_summary["selected_shelter_common_id"].eq("S1")].iloc[0]
    # m1: 100*0.5=50, m2: 200*0.25=50 -> assigned demand 100.
    assert s1["assigned_demand_area_weighted"] == 100.0
    assert s1["assigned_demand_full_mesh"] == 300.0
    assert s1["capacity_pressure_area_weighted"] == 0.5
    assert s1["capacity_pressure_full_mesh"] == 1.5

    assigned = mesh.loc[mesh["selected_shelter_common_id"].eq("S1")]
    assert assigned["capacity_pressure_area_weighted"].tolist() == [0.5, 0.5]


def test_missing_capacity_keeps_full_score_null_but_core_score_available():
    routes, population, tsunami, shelters = sample_frames()
    mesh, _shelters, _qa, failures = build_step4(
        routes, population, tsunami, shelters, expected_rows=4, expected_complete_routes=3
    )
    assert failures == []

    m3 = mesh.loc[mesh["mesh_id"].eq("m3")].iloc[0]
    assert pd.isna(m3["capacity_pressure_area_weighted"])
    assert pd.isna(m3["evacuation_difficulty_score"])
    assert pd.notna(m3["core_evacuation_difficulty_score"])
    assert m3["score_status"] == "core_only_missing_capacity"


def test_route_failure_is_unassigned_and_never_gets_numeric_risk_score():
    routes, population, tsunami, shelters = sample_frames()
    mesh, _shelters, qa, failures = build_step4(
        routes, population, tsunami, shelters, expected_rows=4, expected_complete_routes=3
    )
    assert failures == []

    m4 = mesh.loc[mesh["mesh_id"].eq("m4")].iloc[0]
    assert m4["demand_assignment_status"] == "route_unavailable_unassigned"
    assert pd.isna(m4["core_evacuation_difficulty_score"])
    assert pd.isna(m4["evacuation_difficulty_score"])
    assert qa["demand"]["unassigned_area_weighted"] == 10.0
    assert qa["demand"]["unassigned_full_mesh"] == 50.0


def test_unknown_route_coverage_produces_score_bounds():
    routes, population, tsunami, shelters = sample_frames()
    mesh, _shelters, _qa, failures = build_step4(
        routes, population, tsunami, shelters, expected_rows=4, expected_complete_routes=3
    )
    assert failures == []

    m3 = mesh.loc[mesh["mesh_id"].eq("m3")].iloc[0]
    assert bool(m3["route_exposure_uncertainty_flag"]) is True
    assert m3["route_inundation_exposure_component_lower"] == 30.0
    assert m3["route_inundation_exposure_component"] == 37.5
    assert m3["route_inundation_exposure_component_upper"] == 50.0
    assert m3["core_evacuation_difficulty_score_lower"] <= m3["core_evacuation_difficulty_score"]
    assert m3["core_evacuation_difficulty_score"] <= m3["core_evacuation_difficulty_score_upper"]
