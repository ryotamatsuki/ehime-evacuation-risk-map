#!/usr/bin/env python3
"""Regression gate for the active Analysis Core v4 frontend contract.

The source gate prevents browser-side fallbacks from reappearing. During FINAL B,
after corrected public assets are generated in the same workflow run, the
real-data gate projects those assets through the same field/status semantics used
by the UI and locks the known production counts.
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


def number_or_none(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def ui_policy_score(row: dict[str, Any]) -> float | None:
    """Mirror the active UI: only STEP 4 complete rows expose a numeric score."""
    if str(row.get("score_status")) != "complete":
        return None
    return number_or_none(row, "evacuation_difficulty_score")


def ui_capacity_pressure(row: dict[str, Any]) -> float | None:
    """Mirror the active UI: area-weighted pressure only; no legacy fallback."""
    return number_or_none(row, "capacity_pressure_area_weighted")


def ui_area_weighted_demand_total(rows: list[dict[str, Any]]) -> float | None:
    """Mirror the active KPI: any missing v4 demand makes the aggregate unknown."""
    values = [number_or_none(row, "mesh_evacuation_demand_area_weighted") for row in rows]
    if not rows or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def ui_overloaded_shelter_keys(rows: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for row in rows:
        pressure = ui_capacity_pressure(row)
        if pressure is None or pressure <= 1:
            continue
        key = row.get("selected_shelter_key") or row.get("selected_shelter_common_id")
        require(bool(key), "over-capacity UI row must retain selected shelter identity")
        keys.add(str(key))
    return keys


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
        "properties.policy_score = canonicalPolicyScore(risk)",
        "toNumber(row, 'evacuation_difficulty_score')",
        "toText(row, 'score_status') !== 'complete'",
        "toNumber(row, 'capacity_pressure_area_weighted')",
        "const demandValues = filteredRows.map((row) => toNumber(row, 'mesh_evacuation_demand_area_weighted'))",
        "completeDemandValues.length === filteredRows.length",
        "pressure !== null && pressure > 1 && key",
        "selectedCapacityPressure === null ? '不明（収容人数未公表）'",
        "STEP 4の面積按分需要を避難場所単位に集約し、公表収容人数で除した収容負荷",
        "収容人数未公表は不明のままとし",
        "メッシュ避難需要は人口×津波浸水面積割合の代理値です",
        "route_inundated_segments",
        "id: 'route-inundated-line'",
        "'line-color': '#d92d20'",
        "赤線＝STEP 3で判定した津波浸水区間",
        "ブラウザ側で重み変更・再正規化は行いません",
    ]
    for token in required:
        require(token in app, f"required active-UI contract missing: {token}")

    forbidden = [
        "scoreForRow(",
        "setWeights(",
        "toNumber(row, 'capacity_pressure')",
        "mesh_evacuation_demand_area_weighted') ??",
        "selectedCapacityPressure ?? 0",
        "STEP 4再計算待ち",
        "再計算待ち",
        "STEP 4で再集計するまで参考表示",
        "浸水区間の部分着色はSTEP 3後",
        "避難需要は人口×浸水割合の代理値であり、避難者予測ではありません",
    ]
    for token in forbidden:
        require(token not in app, f"legacy/fallback active-UI behavior remains: {token}")

    # Synthetic guards prove that old values cannot be substituted for missing v4 data.
    require(
        ui_capacity_pressure({"capacity_pressure_area_weighted": None, "capacity_pressure": 9.9}) is None,
        "legacy capacity pressure must never fill a missing area-weighted value",
    )
    require(
        ui_policy_score({"score_status": "core_only_missing_capacity", "evacuation_difficulty_score": 88.8}) is None,
        "non-complete score must remain null even if a numeric value is accidentally present",
    )


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

    ui_scores = [ui_policy_score(row) for row in risk_rows]
    require(sum(score is not None for score in ui_scores) == 813, "UI complete numeric scores must be exactly 813")
    require(
        all(ui_policy_score(row) is None for row in risk_rows if row.get("score_status") != "complete"),
        "UI must never synthesize a score for incomplete rows",
    )

    capacity_missing = [row for row in risk_rows if row.get("score_status") == "core_only_missing_capacity"]
    require(len(capacity_missing) == 128, f"capacity-missing score rows must be 128, got {len(capacity_missing)}")
    require(
        all(ui_capacity_pressure(row) is None for row in capacity_missing),
        "all 128 capacity-missing rows must remain UI-null rather than zero/fallback",
    )

    route_unavailable = [row for row in route_rows if row.get("route_status") != "complete"]
    require(len(route_unavailable) == 28, f"UI route-unavailable rows must be 28, got {len(route_unavailable)}")

    cross_border = [row for row in route_rows if is_true(row.get("cross_border"))]
    require(len(cross_border) == 13, f"UI cross-border routes must be 13, got {len(cross_border)}")

    overloaded_keys = ui_overloaded_shelter_keys(risk_rows)
    require(len(overloaded_keys) == 35, f"UI over-capacity selected shelters must be 35, got {len(overloaded_keys)}")

    require(
        ui_area_weighted_demand_total(risk_rows) is not None,
        "production v4 area-weighted demand must be complete; UI must show unknown instead of legacy fallback if it is not",
    )

    require(metadata.get("cross_border_routes") == 13, "metadata cross-border count must be 13")
    require(metadata.get("complete_routes") == 1062, "metadata complete routes must be 1062")
    require(metadata.get("route_unavailable") == 28, "metadata route unavailable must be 28")

    print("frontend-v4 UI projection PASS: 35 over-capacity / 813 complete / 128 capacity-missing / 28 unavailable / 13 cross-border")


def main() -> None:
    verify_source_contract()
    verify_real_export_contract()
    print("frontend-v4 contract PASS")


if __name__ == "__main__":
    main()
