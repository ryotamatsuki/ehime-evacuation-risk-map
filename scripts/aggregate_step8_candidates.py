#!/usr/bin/env python3
"""STEP 8B aggregate and release-gate multi-shelter candidate routes."""
from __future__ import annotations
import argparse, json, pathlib
import numpy as np
import pandas as pd
EXPECTED_TARGET_ROWS=1090; EXPECTED_COMPLETE_ROUTES=1062; EXPECTED_CROSS_BORDER_RANK1=13

def _read_many(input_dir:pathlib.Path,pattern:str,dtype=None):
    paths=sorted(input_dir.rglob(pattern))
    if not paths: raise SystemExit(f"no files matched {pattern} under {input_dir}")
    return pd.concat([pd.read_csv(path,encoding="utf-8-sig",dtype=dtype) for path in paths],ignore_index=True,sort=False)

def aggregate_step8_candidates(candidates,status,baseline,*,expected_rows=EXPECTED_TARGET_ROWS,expected_complete=EXPECTED_COMPLETE_ROUTES,candidate_limit=10):
    failures=[]; candidates=candidates.copy(); status=status.copy(); baseline=baseline.copy()
    for frame in (candidates,status,baseline): frame["mesh_id"]=frame["mesh_id"].astype(str)
    if len(status)!=expected_rows: failures.append(f"candidate status rows={len(status)} expected={expected_rows}")
    if status["mesh_id"].nunique()!=len(status): failures.append("duplicate mesh_id in candidate status")
    if len(baseline)!=expected_rows or baseline["mesh_id"].nunique()!=len(baseline): failures.append("baseline STEP 2 must contain one row per target mesh")
    if set(baseline["mesh_id"])!=set(status["mesh_id"]): failures.append("candidate status mesh set differs from STEP 2 baseline")
    complete_status=status["candidate_status"].astype(str).eq("complete")
    if int(complete_status.sum())!=expected_complete: failures.append(f"complete candidate meshes={int(complete_status.sum())} expected={expected_complete}")
    if int((~complete_status).sum())!=expected_rows-expected_complete: failures.append("candidate failure count differs from expected")
    merged_status=status.merge(baseline[["mesh_id","route_status","selected_shelter_common_id","selected_shelter_name","total_walking_distance_m","cross_border"]],on="mesh_id",how="left",validate="one_to_one",suffixes=("_step8","_step2"))
    mismatch=merged_status[merged_status["candidate_status"].astype(str)!=merged_status["route_status"].astype(str)]
    if len(mismatch): failures.append(f"candidate/STEP2 route status mismatch rows={len(mismatch)}")
    if len(candidates):
        candidates["candidate_rank"]=pd.to_numeric(candidates["candidate_rank"],errors="coerce"); candidates["total_walking_distance_m"]=pd.to_numeric(candidates["total_walking_distance_m"],errors="coerce")
        if candidates[["candidate_rank","total_walking_distance_m"]].isna().any().any(): failures.append("candidate rows contain invalid rank or distance")
        if candidates.duplicated(["mesh_id","shelter_key"]).any(): failures.append("duplicate mesh_id/shelter_key candidate rows")
        if candidates.duplicated(["mesh_id","candidate_rank"]).any(): failures.append("duplicate candidate rank within a mesh")
        if candidates["candidate_rank"].lt(1).any() or candidates["candidate_rank"].gt(candidate_limit).any(): failures.append("candidate rank outside configured limit")
        for mesh_id,group in candidates.groupby("mesh_id",sort=False):
            ordered=group.sort_values("candidate_rank"); ranks=[int(v) for v in ordered["candidate_rank"]]
            if ranks!=list(range(1,len(ranks)+1)): failures.append(f"non-consecutive candidate ranks for mesh {mesh_id}"); break
            distances=ordered["total_walking_distance_m"].astype(float).to_numpy()
            if len(distances)>1 and np.any(np.diff(distances)<-1e-7): failures.append(f"candidate distance order violation for mesh {mesh_id}"); break
    rank1=candidates.loc[candidates["candidate_rank"].eq(1)].copy() if len(candidates) else candidates.copy()
    if len(rank1)!=expected_complete: failures.append(f"rank1 candidate rows={len(rank1)} expected={expected_complete}")
    rank1_compare=baseline.loc[baseline["route_status"].astype(str).eq("complete")].merge(rank1[["mesh_id","shelter_common_id","shelter_name","total_walking_distance_m","cross_border"]],on="mesh_id",how="left",validate="one_to_one",suffixes=("_step2","_step8"))
    identity_match=(rank1_compare["selected_shelter_common_id"].fillna("").astype(str)==rank1_compare["shelter_common_id"].fillna("").astype(str))&(rank1_compare["selected_shelter_name"].fillna("").astype(str)==rank1_compare["shelter_name"].fillna("").astype(str))
    if not bool(identity_match.all()): failures.append(f"rank1 shelter identity mismatch rows={int((~identity_match).sum())}")
    d2=pd.to_numeric(rank1_compare["total_walking_distance_m_step2"],errors="coerce"); d8=pd.to_numeric(rank1_compare["total_walking_distance_m_step8"],errors="coerce"); delta=(d8-d2).abs(); max_delta=float(delta.max()) if len(delta) else 0.0
    if delta.isna().any() or (delta>1e-6).any(): failures.append(f"rank1 distance differs from STEP2; max absolute delta={max_delta}")
    step8_cross=rank1_compare["cross_border_step8"].astype(str).str.lower().eq("true"); cross=int(step8_cross.sum())
    if cross!=EXPECTED_CROSS_BORDER_RANK1: failures.append(f"rank1 cross-border routes={cross} expected={EXPECTED_CROSS_BORDER_RANK1}")
    counts=status["candidate_count"].fillna(0).astype(int)
    qa={"step":"STEP 8B - candidate routing aggregate release gate","target_meshes":int(len(status)),"complete_candidate_meshes":int(complete_status.sum()),"route_unavailable":int((~complete_status).sum()),"candidate_limit":int(candidate_limit),"candidate_route_rows":int(len(candidates)),"rank1_rows":int(len(rank1)),"rank1_identity_matches_step2":int(identity_match.sum()) if len(rank1_compare) else 0,"rank1_max_distance_delta_m":max_delta,"rank1_cross_border_routes":cross,"meshes_with_at_least_2_candidates":int(counts.ge(2).sum()),"meshes_with_at_least_5_candidates":int(counts.ge(5).sum()),"meshes_reaching_candidate_limit":int(counts.eq(candidate_limit).sum()),"mean_candidate_count_complete":float(counts[complete_status].mean()) if complete_status.any() else 0.0,"candidate_capacity_filtering":False,"canonical_step2_modified":False,"release_gate":"PASS" if not failures else "FAIL","failures":failures}
    return candidates.sort_values(["mesh_id","candidate_rank"]).reset_index(drop=True),status.sort_values("mesh_id").reset_index(drop=True),qa,failures

def main():
    p=argparse.ArgumentParser(); p.add_argument("--input-dir",type=pathlib.Path,required=True); p.add_argument("--baseline-routes-csv",type=pathlib.Path,required=True); p.add_argument("--out-candidates-csv",type=pathlib.Path,required=True); p.add_argument("--out-status-csv",type=pathlib.Path,required=True); p.add_argument("--out-qa",type=pathlib.Path,required=True); p.add_argument("--expected-rows",type=int,default=EXPECTED_TARGET_ROWS); p.add_argument("--expected-complete",type=int,default=EXPECTED_COMPLETE_ROUTES); p.add_argument("--candidate-limit",type=int,default=10); args=p.parse_args()
    candidates=_read_many(args.input_dir,"candidates-*.csv",dtype={"mesh_id":str,"municipality_code":str,"shelter_common_id":str}); status=_read_many(args.input_dir,"candidate-status-*.csv",dtype={"mesh_id":str,"municipality_code":str,"rank1_shelter_common_id":str}); baseline=pd.read_csv(args.baseline_routes_csv,encoding="utf-8-sig",dtype={"mesh_id":str,"municipality_code":str,"selected_shelter_common_id":str})
    c,s,qa,failures=aggregate_step8_candidates(candidates,status,baseline,expected_rows=args.expected_rows,expected_complete=args.expected_complete,candidate_limit=args.candidate_limit); args.out_candidates_csv.parent.mkdir(parents=True,exist_ok=True); args.out_status_csv.parent.mkdir(parents=True,exist_ok=True); args.out_qa.parent.mkdir(parents=True,exist_ok=True); c.to_csv(args.out_candidates_csv,index=False,encoding="utf-8-sig"); s.to_csv(args.out_status_csv,index=False,encoding="utf-8-sig"); args.out_qa.write_text(json.dumps(qa,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(qa,ensure_ascii=False,indent=2))
    if failures: raise SystemExit("STEP 8B release gate failed: "+"; ".join(failures))
if __name__=="__main__": main()
