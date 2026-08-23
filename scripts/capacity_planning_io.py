#!/usr/bin/env python3
"""Shared I/O contract for STEP 8/9 production data."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

EXPECTED_ANALYSIS_VERSION = 'analysis-core-v4-corrected-public'
CAPACITY_CONTRACT_COLUMNS = ('shelter_key', 'shelter_capacity', 'capacity_status')


def normalize_capacity_contract(capacities: pd.DataFrame) -> pd.DataFrame:
    """Return only fields owned by the capacity side of the STEP 8/9 join.

    Candidate routing is the canonical owner of shelter identity/name fields.
    Keeping descriptive fields from the source shelter table here would make
    pandas suffix them to ``*_x``/``*_y`` during the many-to-one merge and can
    silently break downstream routing metadata access.
    """
    required = {'shelter_key', 'shelter_capacity'}
    missing = sorted(required - set(capacities.columns))
    if missing:
        raise ValueError(f"capacity table missing required columns: {', '.join(missing)}")
    keep = [column for column in CAPACITY_CONTRACT_COLUMNS if column in capacities.columns]
    result = capacities.loc[:, keep].copy()
    result['shelter_key'] = result['shelter_key'].astype(str)
    result['shelter_capacity'] = pd.to_numeric(result['shelter_capacity'], errors='coerce')
    if result.duplicated('shelter_key').any():
        raise ValueError('capacity table contains duplicate shelter_key')
    return result


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
