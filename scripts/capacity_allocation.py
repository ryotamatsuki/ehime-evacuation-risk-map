#!/usr/bin/env python3
"""Deterministic capacity-constrained allocation core for STEP 8 and STEP 9.

Canonical Analysis Core v4 is read-only. Missing shelter capacity remains
unknown and is never coerced to zero. Strict scenarios allocate only to
shelters with a known positive capacity and leave shortage explicitly unserved.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import networkx as nx
import numpy as np
import pandas as pd

DEMAND_SCALE = 10
DISTANCE_SCALE = 10
COST_TIE_MULTIPLIER = 100
UNSERVED_PENALTY_M = 1_000_000.0
AUGMENTATION_TIE_COST = 1
DEFAULT_CANDIDATE_LIMIT = 10

@dataclass(frozen=True)
class AllocationResult:
    mesh: pd.DataFrame
    flow: pd.DataFrame
    shelters: pd.DataFrame
    summary: dict[str, object]

def _require(frame: pd.DataFrame, cols: set[str], label: str) -> None:
    missing=sorted(cols-set(frame.columns))
    if missing: raise ValueError(f"{label} missing required columns: {', '.join(missing)}")

def _units(value: float) -> int: return max(0,int(round(float(value)*DEMAND_SCALE)))
def _cap_units(value: float) -> int: return max(0,int(math.floor(float(value)*DEMAND_SCALE+1e-9)))
def _people(value: int) -> float: return float(value)/DEMAND_SCALE
def _walk_cost(distance: float, rank: int) -> int:
    return int(round(float(distance)*DISTANCE_SCALE))*COST_TIE_MULTIPLIER + max(0,min(int(rank),COST_TIE_MULTIPLIER-1))
def _unserved_cost() -> int: return int(round(UNSERVED_PENALTY_M*DISTANCE_SCALE))*COST_TIE_MULTIPLIER

def prepare_candidate_table(candidates: pd.DataFrame, capacities: pd.DataFrame, candidate_limit: int=DEFAULT_CANDIDATE_LIMIT) -> pd.DataFrame:
    _require(candidates,{"mesh_id","candidate_rank","shelter_key","shelter_common_id","shelter_name","total_walking_distance_m"},"candidate routes")
    _require(capacities,{"shelter_key","shelter_capacity"},"capacity table")
    if candidate_limit<=0: raise ValueError("candidate_limit must be positive")
    f=candidates.copy(); f["mesh_id"]=f["mesh_id"].astype(str); f["shelter_key"]=f["shelter_key"].astype(str)
    f["candidate_rank"]=pd.to_numeric(f["candidate_rank"],errors="coerce"); f["total_walking_distance_m"]=pd.to_numeric(f["total_walking_distance_m"],errors="coerce")
    f=f.loc[f["candidate_rank"].between(1,candidate_limit)&f["total_walking_distance_m"].notna()&f["total_walking_distance_m"].ge(0)&f["shelter_key"].ne("")].copy()
    if f.duplicated(["mesh_id","shelter_key"]).any(): raise ValueError("duplicate mesh_id/shelter_key candidate rows")
    for mesh_id,ranks in f.groupby("mesh_id")["candidate_rank"]:
        values=sorted(int(v) for v in ranks)
        if values!=list(range(1,len(values)+1)): raise ValueError(f"non-consecutive candidate ranks for mesh {mesh_id}: {values}")
    cap=capacities.copy(); cap["shelter_key"]=cap["shelter_key"].astype(str); cap["shelter_capacity"]=pd.to_numeric(cap["shelter_capacity"],errors="coerce")
    if cap.duplicated("shelter_key").any(): raise ValueError("capacity table contains duplicate shelter_key")
    keep=[c for c in ("shelter_key","shelter_capacity","capacity_status","common_id","shelter_name","shelter_city") if c in cap.columns]
    f=f.merge(cap[keep],on="shelter_key",how="left",validate="many_to_one")
    known=f["shelter_capacity"].notna()&f["shelter_capacity"].gt(0); f["capacity_known"]=known
    original=f["capacity_status"] if "capacity_status" in f else pd.Series("missing",index=f.index)
    f["capacity_status"]=np.where(known,"available",original.fillna("missing"))
    return f.sort_values(["mesh_id","candidate_rank","shelter_key"]).reset_index(drop=True)

def solve_capacity_allocation(candidates: pd.DataFrame, mesh_analysis: pd.DataFrame, capacities: pd.DataFrame, *, demand_column: str, candidate_limit: int=DEFAULT_CANDIDATE_LIMIT, added_capacity_budget: float=0.0, per_shelter_added_capacity_max: float|None=None, scenario_name: str="area_weighted") -> AllocationResult:
    _require(mesh_analysis,{"mesh_id","route_status","selected_shelter_common_id","selected_shelter_name",demand_column},"mesh analysis")
    prepared=prepare_candidate_table(candidates,capacities,candidate_limit)
    mesh=mesh_analysis.copy(); mesh["mesh_id"]=mesh["mesh_id"].astype(str)
    if mesh.duplicated("mesh_id").any(): raise ValueError("mesh analysis contains duplicate mesh_id")
    mesh[demand_column]=pd.to_numeric(mesh[demand_column],errors="coerce")
    if mesh[demand_column].isna().any() or mesh[demand_column].lt(0).any(): raise ValueError(f"{demand_column} must be non-negative and complete")
    demand={str(r.mesh_id):_units(getattr(r,demand_column)) for r in mesh[["mesh_id",demand_column]].itertuples(index=False)}
    total_units=sum(demand.values()); raw_total=float(mesh[demand_column].sum()); scaled_total=_people(total_units)
    cap=capacities.copy(); cap["shelter_key"]=cap["shelter_key"].astype(str); cap["shelter_capacity"]=pd.to_numeric(cap["shelter_capacity"],errors="coerce")
    cap=cap.loc[cap["shelter_capacity"].notna()&cap["shelter_capacity"].gt(0)]
    cap_map={str(r.shelter_key):float(r.shelter_capacity) for r in cap[["shelter_key","shelter_capacity"]].itertuples(index=False)}
    known=prepared.loc[prepared["capacity_known"]].copy(); groups={k:g for k,g in prepared.groupby("mesh_id",sort=False)}
    used=sorted(set(known["shelter_key"].astype(str))); source="__source__"; sink="__sink__"; unserved="__unserved__"; aug="__augmentation__"
    g=nx.DiGraph(); g.add_node(source,demand=-total_units); g.add_node(sink,demand=total_units); g.add_node(unserved,demand=0); g.add_edge(unserved,sink,capacity=total_units,weight=0)
    for mesh_id,units in demand.items():
        node=f"m:{mesh_id}"; g.add_node(node,demand=0)
        if units>0:
            g.add_edge(source,node,capacity=units,weight=0); g.add_edge(node,unserved,capacity=units,weight=_unserved_cost())
    budget_units=_units(max(0.0,float(added_capacity_budget)))
    if budget_units>0: g.add_node(aug,demand=0); g.add_edge(aug,sink,capacity=budget_units,weight=0)
    for key in used:
        node=f"s:{key}"; g.add_node(node,demand=0); g.add_edge(node,sink,capacity=_cap_units(cap_map[key]),weight=0)
        if budget_units>0:
            local=float(added_capacity_budget) if per_shelter_added_capacity_max is None else max(0.0,float(per_shelter_added_capacity_max))
            g.add_edge(node,aug,capacity=min(budget_units,_units(local)),weight=AUGMENTATION_TIE_COST)
    for r in known.itertuples(index=False):
        units=demand.get(str(r.mesh_id),0)
        if units>0 and str(r.shelter_key) in cap_map: g.add_edge(f"m:{r.mesh_id}",f"s:{r.shelter_key}",capacity=units,weight=_walk_cost(float(r.total_walking_distance_m),int(r.candidate_rank)))
    objective,flow=nx.network_simplex(g) if total_units else (0,{})
    meta={(str(r.mesh_id),str(r.shelter_key)):r for r in prepared.itertuples(index=False)}; flow_rows=[]; mesh_rows=[]; shelter_alloc={k:0 for k in used}
    for mr in mesh.itertuples(index=False):
        mid=str(mr.mesh_id); units=demand[mid]; mf=flow.get(f"m:{mid}",{}); unserved_units=int(mf.get(unserved,0)); allocated=0; weighted=0.0; assigned=[]
        for target,n_raw in mf.items():
            if not target.startswith("s:") or int(n_raw)<=0: continue
            n=int(n_raw); key=target[2:]; r=meta[(mid,key)]; dist=float(r.total_walking_distance_m); allocated+=n; weighted+=n*dist; shelter_alloc[key]=shelter_alloc.get(key,0)+n; assigned.append((key,n,dist))
            flow_rows.append({"mesh_id":mid,"shelter_key":key,"shelter_common_id":r.shelter_common_id,"shelter_name":r.shelter_name,"candidate_rank":int(r.candidate_rank),"cross_border":bool(getattr(r,"cross_border",False)),"total_walking_distance_m":dist,"allocated_demand":_people(n),"allocation_share_of_mesh_demand":float(n)/units if units else 0.0})
        group=groups.get(mid); ccount=0 if group is None else len(group); kcount=0 if group is None else int(group["capacity_known"].sum()); unknown=ccount-kcount
        nearest=None if group is None or group.empty else group.iloc[0]; nearest_key=None if nearest is None else str(nearest["shelter_key"]); nearest_dist=None if nearest is None else float(nearest["total_walking_distance_m"])
        mean=weighted/allocated if allocated else None; dominant=min(assigned,key=lambda x:(-x[1],x[2],x[0]))[0] if assigned else None
        if units==0: status="zero_demand"
        elif ccount==0: status="route_unavailable_or_no_candidate"
        elif kcount==0: status="unknown_capacity_only"
        elif unserved_units>0 and allocated>0: status="capacity_constrained_partial"
        elif unserved_units>0: status="capacity_constrained_unserved"
        elif len(assigned)>1: status="capacity_constrained_split"
        elif dominant!=nearest_key: status="capacity_constrained_rerouted"
        else: status="nearest_known_capacity"
        base_common=getattr(mr,"selected_shelter_common_id",""); base_name=getattr(mr,"selected_shelter_name",""); base_key=f"{'' if pd.isna(base_common) else base_common}||{'' if pd.isna(base_name) else base_name}"
        mesh_rows.append({"mesh_id":mid,"route_status":str(mr.route_status),"demand":_people(units),"allocated_demand":_people(allocated),"unserved_demand":_people(unserved_units),"candidate_count":int(ccount),"known_capacity_candidate_count":kcount,"unknown_capacity_candidate_count":unknown,"nearest_candidate_shelter_key":nearest_key,"baseline_selected_shelter_key":base_key,"dominant_allocated_shelter_key":dominant,"allocated_shelter_count":len(assigned),"mean_allocated_walking_distance_m":mean,"nearest_candidate_walking_distance_m":nearest_dist,"additional_walking_distance_m":None if mean is None or nearest_dist is None else mean-nearest_dist,"allocation_status":status})
    shelter_rows=[]; added_total=0
    for key in used:
        sf=flow.get(f"s:{key}",{}); added=int(sf.get(aug,0)) if budget_units else 0; added_total+=added; base=_cap_units(cap_map[key]); alloc=shelter_alloc.get(key,0); effective=base+added
        shelter_rows.append({"shelter_key":key,"shelter_capacity":cap_map[key],"added_capacity_used":_people(added),"effective_capacity":_people(effective),"allocated_demand":_people(alloc),"utilization":float(alloc)/effective if effective else None,"saturated":bool(effective and alloc>=effective)})
    mesh_df=pd.DataFrame(mesh_rows).sort_values("mesh_id").reset_index(drop=True); flow_df=pd.DataFrame(flow_rows); shelter_df=pd.DataFrame(shelter_rows).sort_values("shelter_key").reset_index(drop=True)
    if len(flow_df): flow_df=flow_df.sort_values(["mesh_id","candidate_rank","shelter_key"]).reset_index(drop=True)
    served=float(mesh_df["allocated_demand"].sum()); unserved_total=scaled_total-served; total_pm=float((flow_df["allocated_demand"]*flow_df["total_walking_distance_m"]).sum()) if len(flow_df) else 0.0
    nearest_map=mesh_df.set_index("mesh_id")["nearest_candidate_walking_distance_m"].to_dict(); additional_pm=float(sum(float(r.allocated_demand)*max(0.0,float(r.total_walking_distance_m)-float(nearest_map.get(str(r.mesh_id),r.total_walking_distance_m))) for r in flow_df.itertuples(index=False))) if len(flow_df) else 0.0
    rank_gt1=float(flow_df.loc[flow_df["candidate_rank"].gt(1),"allocated_demand"].sum()) if len(flow_df) else 0.0; cross=float(flow_df.loc[flow_df["cross_border"],"allocated_demand"].sum()) if len(flow_df) else 0.0
    summary={"model":"capacity_constrained_min_cost_flow_v1","scenario":scenario_name,"demand_column":demand_column,"candidate_limit":int(candidate_limit),"target_meshes":len(mesh_df),"raw_total_demand":raw_total,"scaled_total_demand":scaled_total,"demand_scaling_rounding_error":scaled_total-raw_total,"served_demand":served,"unserved_demand":unserved_total,"served_share":served/scaled_total if scaled_total else 1.0,"known_capacity_shelters_in_candidate_graph":len(used),"meshes_with_no_candidate":int(mesh_df["candidate_count"].eq(0).sum()),"meshes_with_unknown_capacity_only":int(mesh_df["allocation_status"].eq("unknown_capacity_only").sum()),"meshes_partially_unserved":int(mesh_df["allocation_status"].eq("capacity_constrained_partial").sum()),"meshes_fully_unserved":int(mesh_df["allocation_status"].isin(["capacity_constrained_unserved","unknown_capacity_only","route_unavailable_or_no_candidate"]).sum()),"meshes_split_across_shelters":int(mesh_df["allocated_shelter_count"].gt(1).sum()),"meshes_dominant_destination_changed":int((mesh_df["dominant_allocated_shelter_key"].notna()&mesh_df["baseline_selected_shelter_key"].ne(mesh_df["dominant_allocated_shelter_key"])).sum()),"cross_border_allocated_demand":cross,"total_allocated_person_metres":total_pm,"additional_person_metres_vs_nearest_candidate":additional_pm,"mean_additional_walking_distance_m_per_served_demand":additional_pm/served if served else 0.0,"demand_allocated_to_rank_gt1_candidate":rank_gt1,"added_capacity_budget":float(max(0.0,added_capacity_budget)),"added_capacity_used":_people(added_total),"objective_integer_cost":int(objective),"unserved_penalty_m":UNSERVED_PENALTY_M,"augmentation_tie_cost":AUGMENTATION_TIE_COST,"capacity_unknown_treatment":"excluded from strict allocation; retained in diagnostics; never coerced to zero","interpretation":"planning scenario; not an evacuation forecast, engineering feasibility study, or monetary cost-benefit analysis"}
    return AllocationResult(mesh_df,flow_df,shelter_df,summary)

def investment_plan_from_result(result: AllocationResult) -> pd.DataFrame:
    if result.shelters.empty: return result.shelters.copy()
    p=result.shelters.loc[result.shelters["added_capacity_used"].gt(0)].copy()
    return p.sort_values(["added_capacity_used","allocated_demand","shelter_key"],ascending=[False,False,True]).reset_index(drop=True) if len(p) else p

def compare_investment_plans(left: pd.DataFrame,right: pd.DataFrame,budget: float) -> dict[str,object]:
    def mapping(f): return {} if f.empty else {str(r.shelter_key):float(r.added_capacity_used) for r in f[["shelter_key","added_capacity_used"]].itertuples(index=False) if float(r.added_capacity_used)>0}
    a,b=mapping(left),mapping(right); union=set(a)|set(b); inter=set(a)&set(b); shared=sum(min(a.get(k,0.0),b.get(k,0.0)) for k in union); denom=max(float(budget),1e-9)
    return {"left_shelters":len(a),"right_shelters":len(b),"shared_shelters":len(inter),"shelter_jaccard":len(inter)/len(union) if union else 1.0,"shared_capacity":shared,"shared_capacity_share_of_budget":min(1.0,shared/denom)}
