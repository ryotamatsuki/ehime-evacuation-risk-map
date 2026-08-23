#!/usr/bin/env python3
"""Export additive STEP 8/9/10 public JSON without mutating canonical v4 assets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

VERSION = "capacity-planning-v2"


def _records(path: Path):
    frame = pd.read_csv(path, encoding="utf-8-sig")
    return json.loads(frame.to_json(orient="records", force_ascii=False))


def export_capacity_planning(
    step8_dir: Path,
    step8_qa_path: Path,
    step9_plan_path: Path,
    step9_qa_path: Path,
    step10_dir: Path,
    step10_qa_path: Path,
    public_root: Path,
    *,
    planning_source_sha: str,
    workflow_run_id: str,
):
    public_root = Path(public_root)
    canonical = json.loads((public_root / "metadata/analysis.json").read_text(encoding="utf-8"))
    step8 = json.loads(Path(step8_qa_path).read_text(encoding="utf-8"))
    step9 = json.loads(Path(step9_qa_path).read_text(encoding="utf-8"))
    step10 = json.loads(Path(step10_qa_path).read_text(encoding="utf-8"))
    canonical_sha = canonical.get("analysis_source_sha")
    for label, payload in (("STEP 8", step8), ("STEP 9", step9), ("STEP 10", step10)):
        if payload.get("release_gate") != "PASS":
            raise ValueError(f"{label} release gate is not PASS")
        if payload.get("canonical_analysis_source_sha") != canonical_sha:
            raise ValueError(f"{label} provenance does not match canonical artifact")

    out = public_root / "capacity-planning"
    out.mkdir(parents=True, exist_ok=True)
    metadata = {
        "version": VERSION,
        "canonical_analysis_version": canonical.get("analysis_version"),
        "canonical_analysis_source_sha": canonical_sha,
        "capacity_planning_source_sha": planning_source_sha,
        "workflow_run_id": str(workflow_run_id),
        "canonical_assets_modified": False,
        "production_candidate_limit": step8.get("production_candidate_limit"),
        "root_cause_candidate_limits": step10.get("candidate_limits"),
        "capacity_unknown_treatment": "unknown; excluded from strict allocation; never zero",
        "scope": "existing reachable shelters; STEP 10 diagnoses shortage causes and data gaps",
    }
    (out / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "step8-summary.json").write_text(json.dumps(step8, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "step9-summary.json").write_text(json.dumps(step9, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "step9-plan.json").write_text(json.dumps(_records(step9_plan_path), ensure_ascii=False), encoding="utf-8")
    for scenario in ("area_weighted", "full_mesh"):
        for kind in ("mesh", "shelter"):
            src = Path(step8_dir) / f"allocation_{kind}_{scenario}.csv"
            (out / f"step8-{kind}-{scenario}.json").write_text(json.dumps(_records(src), ensure_ascii=False), encoding="utf-8")

    step10_dir = Path(step10_dir)
    (out / "step10-summary.json").write_text(json.dumps(step10, ensure_ascii=False, indent=2), encoding="utf-8")
    for source_name, public_name in (
        ("unserved_root_causes.csv", "step10-root-causes.json"),
        ("capacity_data_gaps.csv", "step10-capacity-data-gaps.json"),
        ("municipality_root_cause_summary.csv", "step10-municipality-summary.json"),
    ):
        (out / public_name).write_text(json.dumps(_records(step10_dir / source_name), ensure_ascii=False), encoding="utf-8")
    return metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step8-dir", type=Path, required=True)
    parser.add_argument("--step8-qa", type=Path, required=True)
    parser.add_argument("--step9-plan", type=Path, required=True)
    parser.add_argument("--step9-qa", type=Path, required=True)
    parser.add_argument("--step10-dir", type=Path, required=True)
    parser.add_argument("--step10-qa", type=Path, required=True)
    parser.add_argument("--public-data-root", type=Path, required=True)
    parser.add_argument("--planning-source-sha", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    args = parser.parse_args()
    print(json.dumps(export_capacity_planning(
        args.step8_dir,
        args.step8_qa,
        args.step9_plan,
        args.step9_qa,
        args.step10_dir,
        args.step10_qa,
        args.public_data_root,
        planning_source_sha=args.planning_source_sha,
        workflow_run_id=args.workflow_run_id,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
