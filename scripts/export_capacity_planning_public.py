#!/usr/bin/env python3
"""Export additive STEP 8/9 public JSON without mutating canonical v4 assets."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import pandas as pd
VERSION="capacity-planning-v1"
def _records(path:Path):
    frame=pd.read_csv(path,encoding="utf-8-sig"); return json.loads(frame.to_json(orient="records",force_ascii=False))
def export_capacity_planning(step8_dir:Path,step8_qa_path:Path,step9_plan_path:Path,step9_qa_path:Path,public_root:Path,*,planning_source_sha:str,workflow_run_id:str):
    public_root=Path(public_root); canonical=json.loads((public_root/'metadata/analysis.json').read_text(encoding='utf-8')); step8=json.loads(Path(step8_qa_path).read_text(encoding='utf-8')); step9=json.loads(Path(step9_qa_path).read_text(encoding='utf-8')); canonical_sha=canonical.get('analysis_source_sha')
    if step8.get('release_gate')!='PASS' or step9.get('release_gate')!='PASS': raise ValueError('STEP 8/9 release gate is not PASS')
    if step8.get('canonical_analysis_source_sha')!=canonical_sha or step9.get('canonical_analysis_source_sha')!=canonical_sha: raise ValueError('capacity-planning provenance does not match canonical artifact')
    out=public_root/'capacity-planning'; out.mkdir(parents=True,exist_ok=True)
    metadata={"version":VERSION,"canonical_analysis_version":canonical.get('analysis_version'),"canonical_analysis_source_sha":canonical_sha,"capacity_planning_source_sha":planning_source_sha,"workflow_run_id":str(workflow_run_id),"canonical_assets_modified":False,"candidate_limit":step8.get('production_candidate_limit'),"capacity_unknown_treatment":"unknown; excluded from strict allocation; never zero","scope":"existing known-capacity shelters; planning scenario"}
    (out/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8'); (out/'step8-summary.json').write_text(json.dumps(step8,ensure_ascii=False,indent=2),encoding='utf-8'); (out/'step9-summary.json').write_text(json.dumps(step9,ensure_ascii=False,indent=2),encoding='utf-8'); (out/'step9-plan.json').write_text(json.dumps(_records(step9_plan_path),ensure_ascii=False),encoding='utf-8')
    for scenario in ('area_weighted','full_mesh'):
        for kind in ('mesh','shelter'):
            src=Path(step8_dir)/f'allocation_{kind}_{scenario}.csv'; (out/f'step8-{kind}-{scenario}.json').write_text(json.dumps(_records(src),ensure_ascii=False),encoding='utf-8')
    return metadata
def main():
    p=argparse.ArgumentParser(); p.add_argument('--step8-dir',type=Path,required=True); p.add_argument('--step8-qa',type=Path,required=True); p.add_argument('--step9-plan',type=Path,required=True); p.add_argument('--step9-qa',type=Path,required=True); p.add_argument('--public-data-root',type=Path,required=True); p.add_argument('--planning-source-sha',required=True); p.add_argument('--workflow-run-id',required=True); a=p.parse_args(); print(json.dumps(export_capacity_planning(a.step8_dir,a.step8_qa,a.step9_plan,a.step9_qa,a.public_data_root,planning_source_sha=a.planning_source_sha,workflow_run_id=a.workflow_run_id),ensure_ascii=False,indent=2))
if __name__=='__main__': main()
