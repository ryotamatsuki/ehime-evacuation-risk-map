from __future__ import annotations
import json
import pandas as pd
import pytest
from capacity_planning_io import load_public_analysis
from run_step8_allocation import run_step8
from run_step9_investment_optimization import optimise_investment

def _model_frames():
    candidates=pd.DataFrame([
        {"mesh_id":"m1","candidate_rank":1,"shelter_key":"1||A","shelter_common_id":"1","shelter_name":"A","total_walking_distance_m":100.0,"cross_border":False},
        {"mesh_id":"m1","candidate_rank":2,"shelter_key":"2||B","shelter_common_id":"2","shelter_name":"B","total_walking_distance_m":300.0,"cross_border":False},
        {"mesh_id":"m2","candidate_rank":1,"shelter_key":"1||A","shelter_common_id":"1","shelter_name":"A","total_walking_distance_m":120.0,"cross_border":False},
        {"mesh_id":"m2","candidate_rank":2,"shelter_key":"2||B","shelter_common_id":"2","shelter_name":"B","total_walking_distance_m":180.0,"cross_border":True},
    ])
    mesh=pd.DataFrame([
        {"mesh_id":"m1","route_status":"complete","selected_shelter_common_id":"1","selected_shelter_name":"A","mesh_evacuation_demand_area_weighted":8.0,"mesh_evacuation_demand_full_mesh":10.0},
        {"mesh_id":"m2","route_status":"complete","selected_shelter_common_id":"1","selected_shelter_name":"A","mesh_evacuation_demand_area_weighted":8.0,"mesh_evacuation_demand_full_mesh":12.0},
    ])
    capacities=pd.DataFrame([{"shelter_key":"1||A","shelter_capacity":10.0},{"shelter_key":"2||B","shelter_capacity":10.0}])
    pressure=pd.DataFrame([{"capacity_pressure_area_weighted":1.6,"capacity_pressure_full_mesh":2.2},{"capacity_pressure_area_weighted":0.0,"capacity_pressure_full_mesh":0.0}])
    return candidates,mesh,capacities,pressure

def test_step8_runs_primary_and_k5_sensitivity_without_capacity_violation():
    candidates,mesh,capacities,pressure=_model_frames(); results,qa,failures=run_step8(candidates,mesh,capacities,pressure,expected_rows=2)
    assert failures==[]; assert qa["release_gate"]=="PASS"; assert qa["scenarios"]["area_weighted"]["baseline_fixed_assignment_over_capacity_shelters"]==1
    assert results["area_weighted"].summary["served_demand"]==pytest.approx(16.0); assert results["full_mesh"].summary["unserved_demand"]==pytest.approx(2.0)

def test_step9_budget_monotonically_reduces_shortage_and_never_exceeds_budget():
    candidates,mesh,capacities,_=_model_frames(); plan,qa,failures=optimise_investment(candidates,mesh,capacities,budgets=(1.0,2.0,5.0),expected_rows=2)
    assert failures==[]; assert qa["release_gate"]=="PASS"; full=qa["scenarios"]["full_mesh"]["budgets"]; values=[r["unserved_demand"] for r in full]
    assert values==sorted(values,reverse=True); assert all(r["added_capacity_used"]<=r["budget"] for r in full); assert set(plan["scenario"]).issubset({"area_weighted","full_mesh"})

def test_public_analysis_loader_enforces_version_and_counts(tmp_path):
    root=tmp_path; (root/"metadata").mkdir(); (root/"risk").mkdir(); (root/"shelters").mkdir()
    (root/"metadata/analysis.json").write_text(json.dumps({"analysis_version":"analysis-core-v4-corrected-public","analysis_source_sha":"abc"}),encoding="utf-8")
    chunk=[{"mesh_id":str(i),"route_status":"complete"} for i in range(1090)]; (root/"risk/all.json").write_text(json.dumps(chunk),encoding="utf-8"); (root/"risk/index.json").write_text(json.dumps([{"file":"risk/all.json","feature_count":1090}]),encoding="utf-8"); (root/"shelters/capacity_pressure.json").write_text("[]",encoding="utf-8")
    mesh,shelters,metadata=load_public_analysis(root); assert len(mesh)==1090 and mesh["mesh_id"].nunique()==1090; assert shelters.empty; assert metadata["analysis_source_sha"]=="abc"
