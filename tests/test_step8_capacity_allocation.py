from __future__ import annotations

import pandas as pd
import pytest

from capacity_allocation import compare_investment_plans, investment_plan_from_result, solve_capacity_allocation


def _frames():
    candidates = pd.DataFrame([
        {"mesh_id":"m1","candidate_rank":1,"shelter_key":"1||A","shelter_common_id":"1","shelter_name":"A","total_walking_distance_m":100.0,"cross_border":False},
        {"mesh_id":"m1","candidate_rank":2,"shelter_key":"2||B","shelter_common_id":"2","shelter_name":"B","total_walking_distance_m":300.0,"cross_border":False},
        {"mesh_id":"m2","candidate_rank":1,"shelter_key":"1||A","shelter_common_id":"1","shelter_name":"A","total_walking_distance_m":120.0,"cross_border":False},
        {"mesh_id":"m2","candidate_rank":2,"shelter_key":"2||B","shelter_common_id":"2","shelter_name":"B","total_walking_distance_m":180.0,"cross_border":True},
    ])
    mesh = pd.DataFrame([
        {"mesh_id":"m1","route_status":"complete","selected_shelter_common_id":"1","selected_shelter_name":"A","demand":8.0},
        {"mesh_id":"m2","route_status":"complete","selected_shelter_common_id":"1","selected_shelter_name":"A","demand":8.0},
    ])
    capacities = pd.DataFrame([
        {"shelter_key":"1||A","shelter_capacity":10.0},
        {"shelter_key":"2||B","shelter_capacity":10.0},
    ])
    return candidates, mesh, capacities


def test_capacity_constraint_splits_flow_and_conserves_demand():
    candidates, mesh, capacities = _frames()
    result = solve_capacity_allocation(candidates, mesh, capacities, demand_column="demand")
    assert result.summary["served_demand"] == pytest.approx(16.0)
    assert result.summary["unserved_demand"] == pytest.approx(0.0)
    assert result.summary["added_capacity_used"] == 0
    assert (result.shelters["allocated_demand"] <= result.shelters["effective_capacity"]).all()
    assert result.flow["allocated_demand"].sum() == pytest.approx(16.0)


def test_missing_capacity_is_not_zero_and_is_not_allocated():
    candidates, mesh, capacities = _frames()
    capacities.loc[capacities["shelter_key"].eq("2||B"), "shelter_capacity"] = None
    result = solve_capacity_allocation(candidates, mesh, capacities, demand_column="demand")
    assert result.summary["served_demand"] == pytest.approx(10.0)
    assert result.summary["unserved_demand"] == pytest.approx(6.0)
    assert set(result.flow["shelter_key"]) == {"1||A"}
    assert result.mesh["unknown_capacity_candidate_count"].sum() == 2


def test_added_capacity_budget_is_globally_placed_where_it_reduces_cost():
    candidates, mesh, capacities = _frames()
    result = solve_capacity_allocation(candidates, mesh, capacities, demand_column="demand", added_capacity_budget=6.0)
    plan = investment_plan_from_result(result)
    assert result.summary["unserved_demand"] == pytest.approx(0.0)
    assert result.summary["added_capacity_used"] == pytest.approx(6.0)
    assert len(plan) == 1
    assert plan.iloc[0]["shelter_key"] == "1||A"


def test_larger_candidate_limit_cannot_worsen_min_cost_objective():
    candidates, mesh, capacities = _frames()
    k1 = solve_capacity_allocation(candidates, mesh, capacities, demand_column="demand", candidate_limit=1)
    k2 = solve_capacity_allocation(candidates, mesh, capacities, demand_column="demand", candidate_limit=2)
    assert k2.summary["unserved_demand"] <= k1.summary["unserved_demand"]
    assert k2.summary["objective_integer_cost"] <= k1.summary["objective_integer_cost"]


def test_investment_plan_robustness_metrics():
    left = pd.DataFrame([{"shelter_key":"a","added_capacity_used":60.0},{"shelter_key":"b","added_capacity_used":40.0}])
    right = pd.DataFrame([{"shelter_key":"a","added_capacity_used":50.0},{"shelter_key":"c","added_capacity_used":50.0}])
    metrics = compare_investment_plans(left, right, 100.0)
    assert metrics["shared_shelters"] == 1
    assert metrics["shelter_jaccard"] == pytest.approx(1/3)
    assert metrics["shared_capacity"] == pytest.approx(50.0)
    assert metrics["shared_capacity_share_of_budget"] == pytest.approx(0.5)
