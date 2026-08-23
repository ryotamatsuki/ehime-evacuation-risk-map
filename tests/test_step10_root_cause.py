from __future__ import annotations

import pandas as pd
import pytest

from analyze_step10_unserved_root_causes import run_step10


def _candidate(mesh: str, rank: int, key: str, distance: float) -> dict[str, object]:
    common_id, name = key.split("||", 1)
    return {
        "mesh_id": mesh,
        "candidate_rank": rank,
        "shelter_key": key,
        "shelter_common_id": common_id,
        "shelter_name": name,
        "shelter_municipality_code": "38201",
        "total_walking_distance_m": distance,
        "cross_border": False,
    }


def _frames():
    candidates: list[dict[str, object]] = []
    candidates += [_candidate("m_unknown", 1, "u1||Unknown 1", 100), _candidate("m_unknown", 2, "u2||Unknown 2", 150)]
    for rank in range(1, 11):
        candidates.append(_candidate("m_recover", rank, f"ru{rank}||Unknown {rank}", 100 + rank))
    candidates.append(_candidate("m_recover", 11, "rk||Known Recover", 300))
    candidates.append(_candidate("m_sat", 1, "sat||Known Saturated", 100))
    candidates.append(_candidate("m_served", 1, "ok||Known OK", 100))

    mesh = pd.DataFrame([
        {"mesh_id": "m_route", "municipality_code": "38201", "municipality": "A", "route_status": "no_network_path", "selected_shelter_common_id": "", "selected_shelter_name": "", "mesh_evacuation_demand_area_weighted": 2.0},
        {"mesh_id": "m_unknown", "municipality_code": "38201", "municipality": "A", "route_status": "complete", "selected_shelter_common_id": "u1", "selected_shelter_name": "Unknown 1", "mesh_evacuation_demand_area_weighted": 3.0},
        {"mesh_id": "m_recover", "municipality_code": "38202", "municipality": "B", "route_status": "complete", "selected_shelter_common_id": "ru1", "selected_shelter_name": "Unknown 1", "mesh_evacuation_demand_area_weighted": 4.0},
        {"mesh_id": "m_sat", "municipality_code": "38202", "municipality": "B", "route_status": "complete", "selected_shelter_common_id": "sat", "selected_shelter_name": "Known Saturated", "mesh_evacuation_demand_area_weighted": 2.0},
        {"mesh_id": "m_served", "municipality_code": "38203", "municipality": "C", "route_status": "complete", "selected_shelter_common_id": "ok", "selected_shelter_name": "Known OK", "mesh_evacuation_demand_area_weighted": 1.0},
    ])

    capacity_rows = [
        {"shelter_key": "u1||Unknown 1", "shelter_capacity": None},
        {"shelter_key": "u2||Unknown 2", "shelter_capacity": None},
        {"shelter_key": "rk||Known Recover", "shelter_capacity": 4.0},
        {"shelter_key": "sat||Known Saturated", "shelter_capacity": 1.0},
        {"shelter_key": "ok||Known OK", "shelter_capacity": 1.0},
    ]
    capacity_rows += [{"shelter_key": f"ru{rank}||Unknown {rank}", "shelter_capacity": None} for rank in range(1, 11)]
    capacities = pd.DataFrame(capacity_rows)
    return pd.DataFrame(candidates), mesh, capacities


def test_step10_decomposition_is_exhaustive_and_candidate_limit_gain_is_explicit():
    candidates, mesh, capacities = _frames()
    frozen = {"scenarios": {"area_weighted": {"unserved_demand": 10.0}}}
    root, gaps, municipalities, qa, failures, allocations = run_step10(
        candidates, mesh, capacities, expected_rows=5, frozen_step8=frozen
    )

    assert failures == []
    assert qa["release_gate"] == "PASS"
    assert qa["baseline_k10_unserved_demand"] == pytest.approx(10.0)
    assert qa["k30_residual_unserved_demand"] == pytest.approx(6.0)
    assert qa["decomposition_sum"] == pytest.approx(10.0)
    assert qa["decomposition_error"] == pytest.approx(0.0)
    assert qa["root_causes"]["route_unavailable"]["unserved_demand"] == pytest.approx(2.0)
    assert qa["root_causes"]["unknown_capacity_only"]["unserved_demand"] == pytest.approx(3.0)
    assert qa["root_causes"]["candidate_limit_recoverable"]["unserved_demand"] == pytest.approx(4.0)
    assert qa["root_causes"]["known_capacity_saturation"]["unserved_demand"] == pytest.approx(1.0)
    assert allocations[10].summary["unserved_demand"] >= allocations[20].summary["unserved_demand"] >= allocations[30].summary["unserved_demand"]
    assert root.loc[root["mesh_id"].eq("m_recover"), "root_cause"].item() == "candidate_limit_recoverable"
    assert gaps.iloc[0]["residual_unserved_exposure"] == pytest.approx(3.0)
    assert municipalities["k10_unserved_demand"].sum() == pytest.approx(10.0)


def test_step10_fails_when_frozen_k10_baseline_drifts():
    candidates, mesh, capacities = _frames()
    frozen = {"scenarios": {"area_weighted": {"unserved_demand": 9.9}}}
    _root, _gaps, _municipalities, qa, failures, _allocations = run_step10(
        candidates, mesh, capacities, expected_rows=5, frozen_step8=frozen
    )
    assert qa["release_gate"] == "FAIL"
    assert any("K10 baseline drifted" in failure for failure in failures)
