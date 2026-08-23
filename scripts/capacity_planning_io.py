#!/usr/bin/env python3
"""Shared I/O contract for STEP 8/9 production data."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

EXPECTED_ANALYSIS_VERSION = 'analysis-core-v4-corrected-public'

def load_public_analysis(root: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    root=Path(root)
    metadata=json.loads((root/'metadata/analysis.json').read_text(encoding='utf-8'))
    if metadata.get('analysis_version') != EXPECTED_ANALYSIS_VERSION:
        raise ValueError(f"unexpected analysis_version: {metadata.get('analysis_version')}")
    index=json.loads((root/'risk/index.json').read_text(encoding='utf-8'))
    rows=[]
    for item in index:
        path=root/str(item['file'])
        chunk=json.loads(path.read_text(encoding='utf-8'))
        if len(chunk) != int(item['feature_count']):
            raise ValueError(f"risk index count mismatch: {path}")
        rows.extend(chunk)
    mesh=pd.DataFrame(rows)
    mesh['mesh_id']=mesh['mesh_id'].astype(str)
    if len(mesh)!=1090 or mesh['mesh_id'].nunique()!=1090:
        raise ValueError(f"production mesh contract mismatch: rows={len(mesh)} unique={mesh['mesh_id'].nunique()}")
    shelters=pd.DataFrame(json.loads((root/'shelters/capacity_pressure.json').read_text(encoding='utf-8')))
    return mesh, shelters, metadata
