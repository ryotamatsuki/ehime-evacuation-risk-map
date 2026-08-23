#!/usr/bin/env python3
"""STEP 9: optimal added-capacity placement across existing known-capacity shelters."""
from __future__ import annotations
import argparse
import json
import pathlib
import pandas as pd
from calculate_step4_demand_capacity_risk import prepare_capacities
from capacity_planning_io import load_public_analysis
from capacity_allocation import compare_investment_plans, investment_plan_from_result, solve_capacity_allocation

EXPECTED_TARGET_ROWS=1090
CANDIDATE_LIMIT=10
BUDGETS=(100.0,500.0,1000.0,2000.0,5000.0)
SCENARIOS={"area_weighted":"mesh_evacuation_demand_area_weighted","full_mesh":"mesh_evacuation_demand_full_mesh"}

def optimise_investment(candidates:pd.DataFrame,mesh_analysis:pd.DataFrame,capacities:pd.DataFrame,*,budgets:BUDGETS.__class__=BUDGETS,expected_rows:int=EXPECTED_TARGET_ROWS):
    failures=[]; plan_parts=[]; scenario_results={}; scenario_summaries={}
    for scenario,demand_column in SCENARIOS.items():
        baseline=solve_capacity_allocation(candidates,mesh_analysis,capacities,demand_column=demand_column,candidate_limit=CANDIDATE_LIMIT,added_capacity_budget=0,scenario_name=scenario)
        if int(baseline.summary["target_meshes"])!=expected_rows: failures.append(f"{scenario}: target mesh count mismatch")
        results={0.0:baseline}; rows=[]; previous_unserved=float(baseline.summary["unserved_demand"]); previous_objective=int(baseline.summary["objective_integer_cost"])
        for budget in budgets:
            result=solve_capacity_allocation(candidates,mesh_analysis,capacities,demand_column=demand_column,candidate_limit=CANDIDATE_LIMIT,added_capacity_budget=float(budget),scenario_name=f"{scenario}_budget_{int(budget)}"); results[float(budget)]=result
            unserved=float(result.summary["unserved_demand"]); objective=int(result.summary["objective_integer_cost"])
            if unserved>previous_unserved+1e-9: failures.append(f"{scenario}: unserved demand increased at budget {budget}")
            if objective>previous_objective: failures.append(f"{scenario}: min-cost objective worsened at budget {budget}")
            if float(result.summary["added_capacity_used"])>float(budget)+1e-9: failures.append(f"{scenario}: added capacity exceeded budget {budget}")
            if len(result.shelters):
                over=result.shelters[result.shelters["allocated_demand"]>result.shelters["effective_capacity"]+1e-9]
                if len(over): failures.append(f"{scenario}: capacity exceeded at {len(over)} shelters for budget {budget}")
            plan=investment_plan_from_result(result)
            if len(plan):
                plan=plan.copy(); plan.insert(0,"scenario",scenario); plan.insert(1,"budget",float(budget)); plan["plan_rank"]=range(1,len(plan)+1); plan_parts.append(plan)
            rows.append({"budget":float(budget),"added_capacity_used":result.summary["added_capacity_used"],"served_demand":result.summary["served_demand"],"unserved_demand":unserved,"served_demand_gain_vs_no_investment":float(result.summary["served_demand"])-float(baseline.summary["served_demand"]),"unserved_demand_reduction_vs_no_investment":float(baseline.summary["unserved_demand"])-unserved,"total_allocated_person_metres":result.summary["total_allocated_person_metres"],"person_metres_change_vs_no_investment":float(result.summary["total_allocated_person_metres"])-float(baseline.summary["total_allocated_person_metres"]),"meshes_dominant_destination_changed":result.summary["meshes_dominant_destination_changed"],"investment_shelter_count":int(len(plan)),"objective_integer_cost":objective})
            previous_unserved=unserved; previous_objective=objective
        scenario_results[scenario]=results; scenario_summaries[scenario]={"baseline":baseline.summary,"budgets":rows}
    robustness={}
    for budget in budgets:
        robustness[str(int(budget))]=compare_investment_plans(investment_plan_from_result(scenario_results["area_weighted"][float(budget)]),investment_plan_from_result(scenario_results["full_mesh"][float(budget)]),float(budget))
    plan_table=pd.concat(plan_parts,ignore_index=True,sort=False) if plan_parts else pd.DataFrame(columns=["scenario","budget","shelter_key","added_capacity_used","plan_rank"])
    qa={"step":"STEP 9 - existing-shelter capacity investment optimisation","model":"global_capacity_budget_min_cost_flow_v1","candidate_limit":CANDIDATE_LIMIT,"budgets":[int(v) for v in budgets],"scenarios":scenario_summaries,"cross_scenario_plan_robustness":robustness,"global_optimum_scope":"exact network-simplex optimum for each demand scenario, candidate graph, known baseline capacities, and added-capacity budget","not_modelled":["new shelter site generation","construction feasibility","monetary project costs","land availability","staffing/operations constraints","simultaneous robustness objective across demand scenarios"],"release_gate":"PASS" if not failures else "FAIL","failures":failures}
    return plan_table,qa,failures

def main():
    p=argparse.ArgumentParser(); p.add_argument("--candidates-csv",type=pathlib.Path,required=True); p.add_argument("--public-data-root",type=pathlib.Path,required=True); p.add_argument("--shelters-csv",type=pathlib.Path,required=True); p.add_argument("--out-plan-csv",type=pathlib.Path,required=True); p.add_argument("--out-qa",type=pathlib.Path,required=True); p.add_argument("--expected-rows",type=int,default=EXPECTED_TARGET_ROWS); a=p.parse_args()
    candidates=pd.read_csv(a.candidates_csv,encoding="utf-8-sig",dtype={"mesh_id":str,"shelter_common_id":str}); mesh,_pressure,metadata=load_public_analysis(a.public_data_root); shelters=pd.read_csv(a.shelters_csv,encoding="utf-8-sig",dtype={"common_id":str}); capacities,ambiguous=prepare_capacities(shelters); selected=sorted(set(candidates["shelter_key"].astype(str)) & set(ambiguous))
    if selected: raise SystemExit(f"STEP 9 candidate graph contains {len(selected)} ambiguous shelter identities")
    plan,qa,failures=optimise_investment(candidates,mesh,capacities,expected_rows=a.expected_rows); qa["canonical_analysis_source_sha"]=metadata.get("analysis_source_sha"); a.out_plan_csv.parent.mkdir(parents=True,exist_ok=True); plan.to_csv(a.out_plan_csv,index=False,encoding="utf-8-sig"); a.out_qa.parent.mkdir(parents=True,exist_ok=True); a.out_qa.write_text(json.dumps(qa,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(qa,ensure_ascii=False,indent=2))
    if failures: raise SystemExit("STEP 9 release gate failed: "+"; ".join(failures))
if __name__=="__main__": main()
