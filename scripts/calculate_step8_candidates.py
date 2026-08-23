#!/usr/bin/env python3
"""STEP 8B: build multiple reachable shelter candidates for each target mesh.

This is a parallel analytical layer. It does not replace STEP 2's canonical
nearest/reachable route. The release gate requires candidate rank 1 to match
STEP 2 exactly for every complete route.
"""
from __future__ import annotations
import argparse, heapq, json, math, pathlib
import networkx as nx
import osmnx as ox
import pandas as pd
from build_walking_network import DEFAULT_BUFFER_M, buffered_aoi, load_boundaries
from calculate_evacuation_routes_v2 import MAX_SHELTER_CONNECTOR_M, WALKING_SPEEDS_MPS, municipality_code_from_common_id, origin_seed_options, select_origins_for_targets, tsunami_candidate_shelters, walking_time_fields
from calculate_step4_demand_capacity_risk import shelter_key
from routing_foundation_qa import as_walk_graph
DEFAULT_CANDIDATE_LIMIT = 10
ALLOWED_MESH_STATUSES = {"complete","network_coverage_gap","no_candidate_shelter_in_aoi","all_candidate_shelters_snap_excluded","no_network_path"}

def snap_candidate_shelters(graph: nx.Graph, candidates: pd.DataFrame, home_code: str):
    if candidates.empty: return [], [], 0
    nodes, distances = ox.distance.nearest_nodes(graph, X=candidates["longitude"].astype(float).tolist(), Y=candidates["latitude"].astype(float).tolist(), return_dist=True)
    if not hasattr(nodes, "__iter__") or isinstance(nodes, (str, bytes)): nodes, distances = [nodes], [distances]
    accepted_by_key, exclusions, duplicate_identity_count = {}, [], 0
    for (_, row), node, connector in zip(candidates.iterrows(), nodes, distances):
        common_id, name = str(row.get("common_id") or ""), str(row.get("name") or "")
        key = shelter_key(common_id, name); shelter_code = municipality_code_from_common_id(common_id)
        record = {"shelter_key":key,"common_id":common_id,"name":name,"shelter_municipality_code":shelter_code,"home_municipality_code":home_code,"cross_border":None if shelter_code is None else shelter_code != home_code,"latitude":float(row["latitude"]),"longitude":float(row["longitude"]),"shelter_node":node,"shelter_connector_distance_m":float(connector)}
        if float(connector) > MAX_SHELTER_CONNECTOR_M:
            record["exclusion_reason"]="shelter_connector_over_500m"; exclusions.append(record); continue
        previous = accepted_by_key.get(key)
        if previous is None: accepted_by_key[key]=record; continue
        duplicate_identity_count += 1
        old=(float(previous["shelter_connector_distance_m"]),str(previous["shelter_node"]),float(previous["latitude"]),float(previous["longitude"]))
        new=(float(record["shelter_connector_distance_m"]),str(record["shelter_node"]),float(record["latitude"]),float(record["longitude"]))
        if new < old: accepted_by_key[key]=record
    return list(accepted_by_key.values()), exclusions, duplicate_identity_count

def multisource_k_shelter_dijkstra(graph: nx.Graph, candidates: list[dict[str, object]], k: int) -> dict[object, dict[str,float]]:
    if k <= 0: raise ValueError("k must be positive")
    labels={}; heap=[]
    def offer(node,key,cost):
        bucket=labels.setdefault(node,{})
        old=bucket.get(key)
        if old is not None:
            if cost >= old-1e-9: return False
            bucket[key]=cost; return True
        if len(bucket)<k: bucket[key]=cost; return True
        worst_key,worst_cost=max(bucket.items(),key=lambda item:(float(item[1]),str(item[0])))
        if (float(cost),str(key)) >= (float(worst_cost),str(worst_key)): return False
        del bucket[worst_key]; bucket[key]=cost; return True
    for candidate in candidates:
        node,key,cost=candidate["shelter_node"],str(candidate["shelter_key"]),float(candidate["shelter_connector_distance_m"])
        if offer(node,key,cost): heapq.heappush(heap,(cost,key,node))
    while heap:
        cost,key,node=heapq.heappop(heap); current=labels.get(node,{}).get(key)
        if current is None or abs(current-cost)>1e-9: continue
        for neighbor,data in graph[node].items():
            try: length=float(data.get("length",0.0))
            except (TypeError,ValueError): continue
            if length<0 or not math.isfinite(length): continue
            new_cost=cost+length
            if offer(neighbor,key,new_cost): heapq.heappush(heap,(new_cost,key,neighbor))
    return labels

def candidate_choices_for_origin(origin, labels, candidate_by_key, k):
    best={}
    for node,access_cost in origin_seed_options(origin):
        for key,label_cost in labels.get(node,{}).items():
            total=float(access_cost)+float(label_cost); previous=best.get(key); candidate_tuple=(total,float(access_cost),node)
            if previous is None or (total,str(key),str(node)) < (previous[0],str(key),str(previous[2])): best[key]=candidate_tuple
    ranked=sorted(((total,key,access,node) for key,(total,access,node) in best.items()),key=lambda item:(item[0],item[1],str(item[3])))[:k]
    rows=[]
    for rank,(total,key,access_cost,_node) in enumerate(ranked,start=1):
        shelter=candidate_by_key[key]; connector=float(shelter["shelter_connector_distance_m"]); network_path=max(0.0,float(total)-float(access_cost)-connector)
        row={"candidate_rank":rank,"shelter_key":key,"shelter_common_id":str(shelter["common_id"]),"shelter_name":str(shelter["name"]),"shelter_municipality_code":shelter["shelter_municipality_code"],"cross_border":shelter["cross_border"],"origin_access_distance_m":float(access_cost),"network_path_distance_m":network_path,"shelter_connector_distance_m":connector,"total_walking_distance_m":float(total)}
        row.update(walking_time_fields(float(total))); rows.append(row)
    return rows

def main():
    p=argparse.ArgumentParser()
    for name in ("municipality-code","mesh-csv","tsunami-exposure-csv","shelters-csv","boundary-zip","graphml","out-candidates","out-status","out-qa","out-exclusions"): p.add_argument("--"+name, required=True if name=="municipality-code" else False, type=None if name=="municipality-code" else pathlib.Path)
    p.add_argument("--buffer-m",type=float,default=DEFAULT_BUFFER_M); p.add_argument("--candidate-limit",type=int,default=DEFAULT_CANDIDATE_LIMIT); args=p.parse_args()
    required_paths=(args.mesh_csv,args.tsunami_exposure_csv,args.shelters_csv,args.boundary_zip,args.graphml,args.out_candidates,args.out_status,args.out_qa,args.out_exclusions)
    if any(v is None for v in required_paths): raise SystemExit("required STEP 8B path argument missing")
    if args.candidate_limit<=0: raise SystemExit("--candidate-limit must be positive")
    home_code=str(args.municipality_code)
    population=pd.read_csv(args.mesh_csv,encoding="utf-8-sig",dtype={"mesh_id":str,"municipality_code":str}); exposure=pd.read_csv(args.tsunami_exposure_csv,encoding="utf-8-sig",dtype={"mesh_id":str})
    exposure["tsunami_inundation_ratio"]=pd.to_numeric(exposure["tsunami_inundation_ratio"],errors="coerce")
    targets=population.merge(exposure[["mesh_id","tsunami_inundation_ratio"]],on="mesh_id",how="inner",validate="one_to_one"); targets=targets[targets["municipality_code"].eq(home_code)&targets["tsunami_inundation_ratio"].gt(0)].copy().sort_values("mesh_id")
    raw_graph=ox.io.load_graphml(filepath=args.graphml); graph=as_walk_graph(raw_graph); boundaries=load_boundaries(args.boundary_zip)
    if home_code not in boundaries: raise SystemExit(f"N03 boundary missing for municipality code {home_code}")
    aoi=buffered_aoi(boundaries[home_code],args.buffer_m); shelters=pd.read_csv(args.shelters_csv,encoding="utf-8-sig",dtype={"common_id":str}); candidate_frame=tsunami_candidate_shelters(shelters,aoi)
    candidates,exclusions,duplicate_identities=snap_candidate_shelters(graph,candidate_frame,home_code); candidate_by_key={str(x["shelter_key"]):x for x in candidates}; origins=select_origins_for_targets(raw_graph,graph,targets); labels=multisource_k_shelter_dijkstra(graph,candidates,args.candidate_limit) if candidates else {}
    candidate_rows=[]; status_rows=[]
    for _,target in targets.iterrows():
        mesh_id=str(target["mesh_id"]); origin=origins[mesh_id]
        if origin.get("origin_method")=="network_coverage_gap": status,choices="network_coverage_gap",[]
        elif candidate_frame.empty: status,choices="no_candidate_shelter_in_aoi",[]
        elif not candidates: status,choices="all_candidate_shelters_snap_excluded",[]
        else:
            choices=candidate_choices_for_origin(origin,labels,candidate_by_key,args.candidate_limit); status="complete" if choices else "no_network_path"
        if status not in ALLOWED_MESH_STATUSES: raise RuntimeError(f"unexpected STEP 8 candidate status: {status}")
        for choice in choices: candidate_rows.append({"mesh_id":mesh_id,"municipality_code":home_code,"municipality":str(target["municipality"]),"tsunami_inundation_ratio":float(target["tsunami_inundation_ratio"]),"origin_method":origin.get("origin_method"),**choice})
        status_rows.append({"mesh_id":mesh_id,"municipality_code":home_code,"municipality":str(target["municipality"]),"candidate_status":status,"candidate_count":len(choices),"rank1_shelter_common_id":choices[0]["shelter_common_id"] if choices else None,"rank1_shelter_name":choices[0]["shelter_name"] if choices else None,"rank1_total_walking_distance_m":choices[0]["total_walking_distance_m"] if choices else None})
    candidate_result=pd.DataFrame(candidate_rows); status_result=pd.DataFrame(status_rows); args.out_candidates.parent.mkdir(parents=True,exist_ok=True); args.out_status.parent.mkdir(parents=True,exist_ok=True); args.out_exclusions.parent.mkdir(parents=True,exist_ok=True)
    candidate_result.to_csv(args.out_candidates,index=False,encoding="utf-8-sig"); status_result.to_csv(args.out_status,index=False,encoding="utf-8-sig"); pd.DataFrame(exclusions).to_csv(args.out_exclusions,index=False,encoding="utf-8-sig")
    counts=status_result["candidate_status"].value_counts().to_dict(); complete=status_result["candidate_status"].eq("complete")
    qa={"step":"STEP 8B - multi-shelter candidate routing","municipality_code":home_code,"municipality":None if targets.empty else str(targets["municipality"].iloc[0]),"target_mesh_count":int(len(targets)),"candidate_limit":int(args.candidate_limit),"candidate_route_rows":int(len(candidate_result)),"mesh_status_counts":counts,"complete_candidate_meshes":int(complete.sum()),"meshes_with_at_least_2_candidates":int(status_result["candidate_count"].ge(2).sum()),"meshes_reaching_candidate_limit":int(status_result["candidate_count"].eq(args.candidate_limit).sum()),"candidate_shelters_before_snap":int(len(candidate_frame)),"candidate_shelters_after_connector_filter":int(len(candidates)),"duplicate_shelter_identities_removed":int(duplicate_identities),"excluded_shelter_connector_over_500m":int(len(exclusions)),"max_shelter_connector_m":MAX_SHELTER_CONNECTOR_M,"distance_formula":"origin_access_distance_m + network_path_distance_m + shelter_connector_distance_m","capacity_affects_candidate_routing":False,"missing_capacity_is_not_zero":True,"walking_speeds_mps":list(WALKING_SPEEDS_MPS)}
    args.out_qa.parent.mkdir(parents=True,exist_ok=True); args.out_qa.write_text(json.dumps(qa,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(qa,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
