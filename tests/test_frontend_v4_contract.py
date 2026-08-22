#!/usr/bin/env python3
"""Regression gate for the active Analysis Core v4 frontend contract.

This script always validates the active React source. When it runs after FINAL B
has generated corrected public assets, it also validates the real 1,090-row
public dataset. The normal PR frontend build has no committed analysis.json, so
its real-data assertions are intentionally deferred to the same-run final export
build where the production assets actually exist.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "AppCorrected.tsx"
MAIN = ROOT / "src" / "main.tsx"
PUBLIC = ROOT / "public" / "data"
METADATA = PUBLIC / "metadata" / "analysis.json"

EXPECTED_SCORE_COUNTS = {
    "complete": 813,
    "core_only_missing_capacity": 128,
    "core_data_incomplete": 121,
    "route_unavailable": 28,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def load_index_rows(index_path: Path) -> list[dict[str, Any]]:
    index = json.loads(index_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for item in index:
        file_path = PUBLIC / item["file"]
        document = json.loads(file_path.read_text(encoding="utf-8"))
        require(isinstance(document, list), f"expected list in {file_path}")
        rows.extend(document)
    return rows


def verify_source_contract() -> None:
    app = APP.read_text(encoding="utf-8")
    main = MAIN.read_text(encoding="utf-8")

    require("import App from './AppCorrected'" in main, "main.tsx must use AppCorrected")

    required = [
        "canonicalPolicyScore",
        "toNumber(row, 'evacuation_difficulty_score')",
        "toText(row, 'score_status') !== 'complete'",
        "toNumber(row, 'capacity_pressure_area_weighted')",
        "route_inundated_segments",
        "id: 'route-inundated-line'",
        "'line-color': '#d92d20'",
        "赤線＝STEP 3で判定した津波浸水区間",
        "不明（収容人数未公表）",
        "ブラウザ側で重み変更・再正規化は行いません",
    ]
    for token in required:
        require(token in app, f"required active-UI contract missing: {token}")

    forbidden = [
        "scoreForRow(",
        "setWeights(",
        "toNumber(row, 'capacity_pressure')",
        "STEP 4再計算待ち",
        "再計算待ち",
        "浸水区間の部分着色はSTEP 3後",
    ]
    for token in forbidden:
        require(token not in app, f"legacy active-UI behavior remains: {token}")


def verify_real_export_contract() -> None:
    if not METADATA.exists():
        print("frontend-v4 real-data gate deferred: corrected analysis.json is generated in FINAL B")
        return

    metadata = json.loads(METADATA.read_text(encoding="utf-8"))
    if metadata.get("analysis_version") != "analysis-core-v4-corrected-public":
        print("frontend-v4 real-data gate deferred: source tree contains non-final public assets")
        return

    risk_rows = load_index_rows(PUBLIC / "risk" / "index.json")
    route_rows = load_index_rows(PUBLIC / "routes" / "index.json")

    require(len(risk_rows) == 1090, f"risk rows must be 1090, got {len(risk_rows)}")
    require(len(route_rows) == 1090, f"route rows must be 1090, got {len(route_rows)}")

    score_counts = Counter(str(row.get("score_status")) for row in risk_rows)
    require(dict(score_counts) == EXPECTED_SCORE_COUNTS, f"score status drift: {dict(score_counts)}")

    complete_scores = [row for row in risk_rows if row.get("score_status") == "complete"]
    incomplete_scores = [row for row in risk_rows if row.get("score_status") != "complete"]
    require(len(complete_scores) == 813, f"complete score rows must be 813, got {len(complete_scores)}")
    require(all(row.get("evacuation_difficulty_score") is not None for row in complete_scores), "complete rows must retain canonical score")
    require(all(row.get("evacuation_difficulty_score") is None for row in incomplete_scores), "non-complete rows must never receive a fallback score")

    capacity_missing = [row for row in risk_rows if row.get("score_status") == "core_only_missing_capacity"]
    require(len(capacity_missing) == 128, f"capacity-missing score rows must be 128, got {len(capacity_missing)}")
    require(all(row.get("capacity_pressure_area_weighted") is None for row in capacity_missing), "capacity-missing rows must remain null")

    route_unavailable = [row for row in route_rows if row.get("route_status") != "complete"]
    require(len(route_unavailable) == 28, f"route unavailable rows must be 28, got {len(route_unavailable)}")

    cross_border = [row for row in route_rows if is_true(row.get("cross_border"))]
    require(len(cross_border) == 13, f"cross-border routes must be 13, got {len(cross_border)}")

    overloaded_keys: set[str] = set()
    for row in risk_rows:
        pressure = row.get("capacity_pressure_area_weighted")
        if pressure is None or float(pressure) <= 1:
            continue
        key = row.get("selected_shelter_key") or row.get("selected_shelter_common_id")
        require(bool(key), "over-capacity row must retain selected shelter identity")
        overloaded_keys.add(str(key))
    require(len(overloaded_keys) == 35, f"over-capacity selected shelters must be 35, got {len(overloaded_keys)}")

    require(metadata.get("cross_border_routes") == 13, "metadata cross-border count must be 13")
    require(metadata.get("complete_routes") == 1062, "metadata complete routes must be 1062")
    require(metadata.get("route_unavailable") == 28, "metadata route unavailable must be 28")

    print("frontend-v4 real-data gate PASS: 35 over-capacity / 813 complete / 128 capacity-missing / 28 unavailable / 13 cross-border")


def main() -> None:
    verify_source_contract()
    verify_real_export_contract()
    print("frontend-v4 contract PASS")


if __name__ == "__main__":
    main()
