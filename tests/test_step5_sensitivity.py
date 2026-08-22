import importlib.util
import pathlib

import numpy as np
import pandas as pd

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "analyze_step5_sensitivity.py"
SPEC = importlib.util.spec_from_file_location("step5", SCRIPT)
step5 = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(step5)


def _risk_rows() -> pd.DataFrame:
    rows = []
    for i in range(6):
        tsunami = 10.0 + i * 10
        vulnerable = 20.0 + i * 5
        walking = 30.0 + i * 4
        route = 5.0 + i * 7
        capacity = 15.0 + i * 8
        weights = step5.BASE_WEIGHTS
        baseline = (
            tsunami * weights["tsunami_exposure"]
            + vulnerable * weights["vulnerable_population"]
            + walking * weights["walking_accessibility"]
            + route * weights["route_inundation_exposure"]
            + capacity * weights["shelter_capacity_pressure"]
        ) / sum(weights.values())
        rows.append(
            {
                "mesh_id": str(100 + i),
                "score_status": "complete",
                "route_status": "complete",
                "evacuation_difficulty_score": baseline,
                "evacuation_difficulty_score_full_mesh_sensitivity": min(100.0, baseline + 2.0),
                "mesh_evacuation_demand_area_weighted": 10.0 + i,
                "mesh_evacuation_demand_full_mesh": 20.0 + i,
                "tsunami_exposure_component": tsunami,
                "vulnerable_population_component": vulnerable,
                "walking_accessibility_component": walking,
                "route_inundation_exposure_component": route,
                "shelter_capacity_pressure_component_area_weighted": capacity,
            }
        )
    rows.extend(
        [
            {
                "mesh_id": "900",
                "score_status": "core_only_missing_capacity",
                "route_status": "complete",
                "evacuation_difficulty_score": np.nan,
                "evacuation_difficulty_score_full_mesh_sensitivity": np.nan,
                "mesh_evacuation_demand_area_weighted": 5.0,
                "mesh_evacuation_demand_full_mesh": 10.0,
                "tsunami_exposure_component": 50.0,
                "vulnerable_population_component": 50.0,
                "walking_accessibility_component": 50.0,
                "route_inundation_exposure_component": 50.0,
                "shelter_capacity_pressure_component_area_weighted": np.nan,
            },
            {
                "mesh_id": "901",
                "score_status": "core_data_incomplete",
                "route_status": "complete",
                "evacuation_difficulty_score": np.nan,
                "evacuation_difficulty_score_full_mesh_sensitivity": np.nan,
                "mesh_evacuation_demand_area_weighted": 5.0,
                "mesh_evacuation_demand_full_mesh": 10.0,
                "tsunami_exposure_component": 50.0,
                "vulnerable_population_component": np.nan,
                "walking_accessibility_component": 50.0,
                "route_inundation_exposure_component": 50.0,
                "shelter_capacity_pressure_component_area_weighted": 50.0,
            },
            {
                "mesh_id": "902",
                "score_status": "route_unavailable",
                "route_status": "no_network_path",
                "evacuation_difficulty_score": np.nan,
                "evacuation_difficulty_score_full_mesh_sensitivity": np.nan,
                "mesh_evacuation_demand_area_weighted": 5.0,
                "mesh_evacuation_demand_full_mesh": 10.0,
                "tsunami_exposure_component": 50.0,
                "vulnerable_population_component": 50.0,
                "walking_accessibility_component": np.nan,
                "route_inundation_exposure_component": np.nan,
                "shelter_capacity_pressure_component_area_weighted": np.nan,
            },
        ]
    )
    return pd.DataFrame(rows)


def _shelters() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "capacity_pressure_area_weighted": [0.5, 1.2, np.nan],
            "capacity_pressure_full_mesh": [0.8, 1.8, np.nan],
        }
    )


def test_scenario_set_is_deterministic_and_bounded():
    scenarios = step5.scenario_weights()
    assert list(scenarios)[0] == "baseline"
    assert len(scenarios) == 12
    assert scenarios["baseline"] == step5.BASE_WEIGHTS
    assert scenarios["equal_weight"] == {key: 20.0 for key in step5.BASE_WEIGHTS}
    assert scenarios["tsunami_exposure_low"]["tsunami_exposure"] == 20.0
    assert scenarios["tsunami_exposure_high"]["tsunami_exposure"] == 30.0


def test_step5_ranks_complete_rows_only_and_preserves_baseline():
    risk = _risk_rows()
    scored, summary, failures = step5.build_step5(risk, _shelters(), expected_rows=len(risk))
    assert failures == []
    assert len(scored) == 6
    assert set(scored["mesh_id"]) == {str(100 + i) for i in range(6)}
    assert summary["weight_sensitivity"]["scenario_count"] == 12
    assert summary["missing_data_impact"]["excluded_from_weight_ranking_total"] == 3
    assert scored.filter(like="rank__").notna().all(axis=None)
    assert scored["rank_best"].le(scored["rank_worst"]).all()
    assert scored["robust_top50_all_scenarios"].dtype == bool

    complete = risk[risk["score_status"].eq("complete")].reset_index(drop=True)
    baseline_recomputed = scored["score__baseline"].reset_index(drop=True)
    np.testing.assert_allclose(
        baseline_recomputed,
        complete["evacuation_difficulty_score"],
        rtol=0,
        atol=1e-9,
    )


def test_noncomplete_numeric_score_is_rejected():
    risk = _risk_rows()
    risk.loc[risk["score_status"].eq("core_only_missing_capacity"), "evacuation_difficulty_score"] = 42.0
    _scored, _summary, failures = step5.build_step5(risk, _shelters(), expected_rows=len(risk))
    assert any("non-complete row" in failure for failure in failures)


def test_demand_sensitivity_reports_more_full_mesh_pressure():
    risk = _risk_rows()
    _scored, summary, failures = step5.build_step5(risk, _shelters(), expected_rows=len(risk))
    assert failures == []
    demand = summary["demand_sensitivity"]
    assert demand["full_mesh_total_population"] > demand["area_weighted_total_people_equivalent"]
    assert demand["over_capacity_full_mesh_shelters"] >= demand["over_capacity_area_weighted_shelters"]
