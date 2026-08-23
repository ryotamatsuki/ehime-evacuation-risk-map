from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one exact match, got {count}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# AppCorrected remains the canonical Analysis Core UI.  Only remove the obsolete
# STEP 7 placeholder and route primitive parsing/loading through the shared helper.
replace_once(
    "src/AppCorrected.tsx",
    "import { centroid } from '@turf/turf'\n\ntype JsonRecord = Record<string, unknown>\ntype ModeId = 'current' | 'elderly' | 'capacity' | 'simulation'",
    "import { centroid } from '@turf/turf'\nimport { DATA_BASE, formatNumber, loadJson, recordNumber, recordText, type JsonRecord } from './dataContract'\n\ntype ModeId = 'current' | 'elderly' | 'capacity'",
)
replace_once("src/AppCorrected.tsx", "const BASE = import.meta.env.BASE_URL", "const BASE = DATA_BASE")
replace_once("src/AppCorrected.tsx", "  capacity: '避難場所容量',\n  simulation: '対策シミュレーション',", "  capacity: '避難場所容量',")
replace_once(
    "src/AppCorrected.tsx",
    "const toNumber = (row: JsonRecord | undefined | null, key: string): number | null => {\n  if (!row) return null\n  const value = row[key]\n  if (value === null || value === undefined || value === '') return null\n  const number = typeof value === 'number' ? value : Number(value)\n  return Number.isFinite(number) ? number : null\n}\n\nconst toText = (row: JsonRecord | undefined | null, key: string): string | null => {\n  if (!row) return null\n  const value = row[key]\n  return value === null || value === undefined || value === '' ? null : String(value)\n}\n\nconst formatNumber = (value: number | null, digits = 0): string => {\n  if (value === null || !Number.isFinite(value)) return '—'\n  return new Intl.NumberFormat('ja-JP', { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(value)\n}",
    "const toNumber = recordNumber\nconst toText = recordText",
)
replace_once(
    "src/AppCorrected.tsx",
    "\nasync function loadJson<T>(path: string): Promise<T> {\n  const response = await fetch(path)\n  if (!response.ok) throw new Error(`${response.status} ${path}`)\n  return response.json() as Promise<T>\n}\n",
    "\n",
)
replace_once("src/AppCorrected.tsx", "    if (next === 'simulation') return\n", "")
replace_once(
    "src/AppCorrected.tsx",
    "        {(Object.keys(MODE_LABELS) as ModeId[]).map((id) => { const disabled = id === 'simulation' || (id === 'capacity' && !capacityTrusted); return <button key={id} className={`${mode === id ? 'active' : ''} ${disabled ? 'coming-soon' : ''}`} onClick={() => setModeAndMetric(id)} disabled={disabled}>{MODE_LABELS[id]}{id === 'simulation' && <small>準備中</small>}{id === 'capacity' && !capacityTrusted && <small>容量データなし</small>}</button> })}",
    "        {(Object.keys(MODE_LABELS) as ModeId[]).map((id) => { const disabled = id === 'capacity' && !capacityTrusted; return <button key={id} className={`${mode === id ? 'active' : ''} ${disabled ? 'coming-soon' : ''}`} onClick={() => setModeAndMetric(id)} disabled={disabled}>{MODE_LABELS[id]}{id === 'capacity' && !capacityTrusted && <small>容量データなし</small>}</button> })}",
)

replace_once(
    "src/policy-simulation.css",
    ".mode-strip > button:nth-child(4) {\n  display: none;\n}\n\n",
    "",
)

# Expensive spatial analysis stays unconditional on main so the release/deploy
# chain remains authoritative, but PR execution is limited to analysis-affecting files.
replace_once(
    ".github/workflows/routing-step2.yml",
    "on:\n  pull_request:\n    branches: [main]\n  push:\n    branches: [main]\n  workflow_dispatch:",
    "on:\n  pull_request:\n    branches: [main]\n    paths:\n      - 'data/processed_population_mesh.csv'\n      - 'data/processed_shelters.csv'\n      - 'data/tsunami_exposure.csv'\n      - 'scripts/build_walking_network.py'\n      - 'scripts/calculate_evacuation_routes_v2.py'\n      - 'scripts/aggregate_step2_routes.py'\n      - 'scripts/calculate_route_exposure_step3.py'\n      - 'scripts/calculate_step4_demand_capacity_risk.py'\n      - 'scripts/export_corrected_public_data.py'\n      - 'tests/test_evacuation_routes_v2.py'\n      - 'tests/test_step2_aggregate.py'\n      - 'tests/test_step3_route_exposure.py'\n      - 'tests/test_step4_demand_capacity_risk.py'\n      - 'tests/test_final_export_contract.py'\n      - 'requirements.txt'\n      - '.github/workflows/routing-step2.yml'\n  push:\n    branches: [main]\n  workflow_dispatch:",
)
replace_once(
    ".github/workflows/routing-foundation-step1.yml",
    "on:\n  pull_request:\n    branches: [main]\n  workflow_dispatch:",
    "on:\n  pull_request:\n    branches: [main]\n    paths:\n      - 'data/processed_population_mesh.csv'\n      - 'data/processed_shelters.csv'\n      - 'data/tsunami_exposure.csv'\n      - 'scripts/download_population.py'\n      - 'scripts/build_walking_network.py'\n      - 'scripts/routing_foundation_qa.py'\n      - 'scripts/select_mesh_origins.py'\n      - 'scripts/enforce_step1_gate.py'\n      - 'tests/test_routing_foundation.py'\n      - 'requirements.txt'\n      - '.github/workflows/routing-foundation-step1.yml'\n  workflow_dispatch:",
)

# Independent Python validator keeps its own implementation but must not invent a
# missing STEP 4 pressure or silently hide a negative intervention delta.
replace_once(
    "scripts/validate_policy_simulation.py",
    "        baseline_pressure = number(shelter.get(\"capacity_pressure_area_weighted\"))\n        if baseline_pressure is None:\n            baseline_pressure = demand / capacity",
    "        baseline_pressure = number(shelter.get(\"capacity_pressure_area_weighted\"))\n        if baseline_pressure is None:\n            continue",
)
replace_once(
    "scripts/validate_policy_simulation.py",
    "            reductions.append(max(0.0, canonical - simulated_score))",
    "            reduction = canonical - simulated_score\n            if reduction < -1e-8:\n                raise ValueError(f\"capacity augmentation increased score for mesh {row.get('mesh_id')}\")\n            reductions.append(max(0.0, reduction))",
)

print("STEP 7.5 exact-match refactor applied")
