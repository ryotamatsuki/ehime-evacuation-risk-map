#!/usr/bin/env python3
"""STEP 8C/D: capacity-constrained allocation and baseline comparison."""
from __future__ import annotations
import argparse
import json
import pathlib
import pandas as pd
from calculate_step4_demand_capacity_risk import prepare_capacities
from capacity_allocation import solve_capacity_allocation
from capacity_planning_io import load_public_analysis

EXPECTED_TARGET_ROWS = 1090
PRODUCTION_CANDIDATE_LIMIT = 10
SENSITIVITY_CANDIDATE_LIMIT = 5
SCENARIOS = {
    "area_weighted": "mesh_evacuation_demand_area_weighted",
    "full_mesh": "mesh_evacuation_demand_full_mesh",
}

def _assert_allocation_contract(result, expected_rows: int) -> list[str]:
    failures=[]; summary=result.summary
    if int(summary["target_meshes"]) != expected_rows: failures.append(f"allocation target meshes={summary['target_meshes']} expected={expected_rows}")
    if abs(float(summary["served_demand"])+float(summary["unserved_demand"])-float(summary["scaled_total_demand"])) > 1e-6: failures.append("served + unserved does not equal scaled total demand")
    if float(summary["added_capacity_used"]) != 0: failures.append("STEP 8 baseline unexpectedly used added capacity")
    if len(result.shelters):
        over=result.shelters[result.shelters["allocated_demand"] > result.shelters["effective_capacity"] + 1e-9]
        if len(over): failures.append(f"capacity exceeded at {len(over)} shelters")
    if len(result.flow):
        missing=result.flow["shelter_key"].isna() | result.flow["shelter_key"].eq("")
        if missing.any(): failures.append("positive allocation flow missing shelter key")
    return failures

def run_step8(candidates: pd.DataFrame, mesh_analysis: pd.DataFrame, capacities: pd.DataFrame, step4_shelters: pd.DataFrame, *, expected_rows: int=EXPECTED_TARGET_ROWS):
    failures=[]; production_results={}; summaries={}
    if len(mesh_analysis)!=expected_rows or mesh_analysis["mesh_id"].astype(str).nunique()!=expected_rows: failures.append("STEP 4 mesh input must contain exactly one row per target mesh")
    baseline_overload={}
    for scenario,col in {"area_weighted":"capacity_pressure_area_weighted","full_mesh":"capacity_pressure_full_mesh"}.items():
        pressure=pd.to_numeric(step4_shelters.get(col),errors="coerce"); baseline_overload[scenario]=int(pressure.gt(1).sum())
    for scenario,demand_column in SCENARIOS.items():
        production=solve_capacity_allocation(candidates,mesh_analysis,capacities,demand_column=demand_column,candidate_limit=PRODUCTION_CANDIDATE_LIMIT,scenario_name=scenario)
        sensitivity=solve_capacity_allocation(candidates,mesh_analysis,capacities,demand_column=demand_column,candidate_limit=SENSITIVITY_CANDIDATE_LIMIT,scenario_name=f"{scenario}_k5_sensitivity")
        failures.extend(f"{scenario}: {m}" for m in _assert_allocation_contract(production,expected_rows)); failures.extend(f"{scenario} k5: {m}" for m in _assert_allocation_contract(sensitivity,expected_rows))
        if float(production.summary["unserved_demand"]) > float(sensitivity.summary["unserved_demand"])+1e-9: failures.append(f"{scenario}: K10 unserved demand exceeds K5")
        if int(production.summary["objective_integer_cost"]) > int(sensitivity.summary["objective_integer_cost"]): failures.append(f"{scenario}: K10 min-cost objective is worse than K5")
        production_results[scenario]=production
        summaries[scenario]={**production.summary,"baseline_fixed_assignment_over_capacity_shelters":baseline_overload[scenario],"capacity_constrained_over_capacity_shelters":0,"saturated_known_capacity_shelters":int(production.shelters["saturated"].sum()) if len(production.shelters) else 0,"candidate_limit_sensitivity":{"k5_served_demand":sensitivity.summary["served_demand"],"k10_served_demand":production.summary["served_demand"],"k5_unserved_demand":sensitivity.summary["unserved_demand"],"k10_unserved_demand":production.summary["unserved_demand"],"k5_objective_integer_cost":sensitivity.summary["objective_integer_cost"],"k10_objective_integer_cost":production.summary["objective_integer_cost"],"served_demand_gain_k10_vs_k5":float(production.summary["served_demand"])-float(sensitivity.summary["served_demand"])}}
    qa={"step":"STEP 8C/D - capacity constrained allocation","model":"capacity_constrained_min_cost_flow_v1","canonical_analysis_core_v4_modified":False,"production_candidate_limit":PRODUCTION_CANDIDATE_LIMIT,"sensitivity_candidate_limit":SENSITIVITY_CANDIDATE_LIMIT,"scenarios":summaries,"release_gate":"PASS" if not failures else "FAIL","failures":failures,"interpretation":"strict known-capacity allocation. Missing capacity is not zero; unknown-capacity shelters are retained as candidates but excluded from capacity-constrained flow."}
    return production_results,qa,failures

def main():
    p=argparse.ArgumentParser(); p.add_argument("--candidates-csv",type=pathlib.Path,required=True); p.add_argument("--public-data-root",type=pathlib.Path,required=True); p.add_argument("--shelters-csv",type=pathlib.Path,required=True); p.add_argument("--out-dir",type=pathlib.Path,required=True); p.add_argument("--out-qa",type=pathlib.Path,required=True); p.add_argument("--expected-rows",type=int,default=EXPECTED_TARGET_ROWS); a=p.parse_args()
    candidates=pd.read_csv(a.candidates_csv,encoding="utf-8-sig",dtype={"mesh_id":str,"shelter_common_id":str}); mesh,step4_shelters,metadata=load_public_analysis(a.public_data_root); source=pd.read_csv(a.shelters_csv,encoding="utf-8-sig",dtype={"common_id":str}); capacities,ambiguous=prepare_capacities(source)
    used=set(candidates["shelter_key"].astype(str)); selected_ambiguous=sorted(used & set(ambiguous))
    if selected_ambiguous: raise SystemExit(f"STEP 8 candidate graph contains {len(selected_ambiguous)} ambiguous shelter identities")
    results,qa,failures=run_step8(candidates,mesh,capacities,step4_shelters,expected_rows=a.expected_rows); qa["canonical_analysis_source_sha"]=metadata.get("analysis_source_sha")
    a.out_dir.mkdir(parents=True,exist_ok=True)
    for scenario,result in results.items():
        result.mesh.to_csv(a.out_dir/f"allocation_mesh_{scenario}.csv",index=False,encoding="utf-8-sig"); result.flow.to_csv(a.out_dir/f"allocation_flow_{scenario}.csv",index=False,encoding="utf-8-sig"); result.shelters.to_csv(a.out_dir/f"allocation_shelter_{scenario}.csv",index=False,encoding="utf-8-sig")
    a.out_qa.parent.mkdir(parents=True,exist_ok=True); a.out_qa.write_text(json.dumps(qa,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(qa,ensure_ascii=False,indent=2))
    if failures: raise SystemExit("STEP 8 release gate failed: "+"; ".join(failures))
if __name__=="__main__": main()
