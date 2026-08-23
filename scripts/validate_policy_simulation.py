#!/usr/bin/env python3
"""Validate STEP 7 capacity-augmentation policy simulation on production export.

This is a scenario QA tool, not a new Analysis Core calculation. It holds routes,
selected shelters, area-weighted demand and the four non-capacity score components
fixed. Only official known shelter capacity is increased by a hypothetical delta.
Canonical STEP 4 scores are read-only and must remain unchanged.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
from typing import Any

BASELINE_OVER_CAPACITY = 35
EXPECTED_COMPLETE = 813
EXPECTED_NONCOMPLETE = 277
EXPECTED_KNOWN_CAPACITY_SHELTERS = 272
DELTAS = (100, 500, 1000)


def number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def load_risk(root: pathlib.Path) -> list[dict[str, Any]]:
    index = json.loads((root / "risk" / "index.json").read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for item in index:
        rows.extend(json.loads((root / str(item["file"])).read_text(encoding="utf-8")))
    return rows


def score_with_capacity_component(row: dict[str, Any], capacity_component: float) -> float | None:
    if row.get("score_status") != "complete":
        return None
    fields = [
        ("tsunami_exposure_component", 25.0),
        ("vulnerable_population_component", 20.0),
        ("walking_accessibility_component", 25.0),
        ("route_inundation_exposure_component", 15.0),
    ]
    total = capacity_component * 15.0
    for key, weight in fields:
        value = number(row.get(key))
        if value is None:
            return None
        total += value * weight
    return total / 100.0


def simulate(
    risk: list[dict[str, Any]],
    shelters: list[dict[str, Any]],
    delta: float,
) -> dict[str, Any]:
    complete_by_shelter: dict[str, list[dict[str, Any]]] = {}
    for row in risk:
        if row.get("score_status") != "complete":
            continue
        key = row.get("selected_shelter_key")
        if key:
            complete_by_shelter.setdefault(str(key), []).append(row)

    candidates: list[dict[str, Any]] = []
    for shelter in shelters:
        key = shelter.get("selected_shelter_key")
        capacity = number(shelter.get("shelter_capacity"))
        demand = number(shelter.get("assigned_demand_area_weighted"))
        if not key or capacity is None or capacity <= 0 or demand is None or demand < 0:
            continue
        baseline_pressure = number(shelter.get("capacity_pressure_area_weighted"))
        if baseline_pressure is None:
            continue
        simulated_capacity = capacity + delta
        simulated_pressure = demand / simulated_capacity
        simulated_component = min(max(simulated_pressure * 100.0, 0.0), 100.0)
        reductions: list[float] = []
        for row in complete_by_shelter.get(str(key), []):
            canonical = number(row.get("evacuation_difficulty_score"))
            simulated_score = score_with_capacity_component(row, simulated_component)
            if canonical is None or simulated_score is None:
                continue
            reduction = canonical - simulated_score
            if reduction < -1e-8:
                raise ValueError(f"capacity augmentation increased score for mesh {row.get('mesh_id')}")
            reductions.append(max(0.0, reduction))
        candidates.append(
            {
                "shelter_key": str(key),
                "shelter_name": shelter.get("selected_shelter_name"),
                "baseline_capacity": capacity,
                "simulated_capacity": simulated_capacity,
                "assigned_demand": demand,
                "baseline_pressure": baseline_pressure,
                "simulated_pressure": simulated_pressure,
                "baseline_over_capacity": baseline_pressure > 1.0,
                "simulated_over_capacity": simulated_pressure > 1.0,
                "resolved_over_capacity": baseline_pressure > 1.0 and simulated_pressure <= 1.0,
                "affected_complete_meshes": len(reductions),
                "total_score_reduction": sum(reductions),
                "max_score_reduction": max(reductions) if reductions else 0.0,
            }
        )

    candidates.sort(
        key=lambda row: (
            -float(row["total_score_reduction"]),
            -int(bool(row["resolved_over_capacity"])),
            float(row["simulated_pressure"]),
            str(row["shelter_name"] or row["shelter_key"]),
        )
    )
    return {
        "capacity_delta": delta,
        "known_capacity_shelters": len(candidates),
        "baseline_over_capacity_shelters": sum(bool(row["baseline_over_capacity"]) for row in candidates),
        "simulated_over_capacity_shelters": sum(bool(row["simulated_over_capacity"]) for row in candidates),
        "resolved_over_capacity_shelters": sum(bool(row["resolved_over_capacity"]) for row in candidates),
        "affected_complete_meshes": sum(int(row["affected_complete_meshes"]) for row in candidates),
        "total_score_reduction": sum(float(row["total_score_reduction"]) for row in candidates),
        "top_candidates": candidates[:12],
    }


def validate(root: pathlib.Path) -> dict[str, Any]:
    risk = load_risk(root)
    shelters = json.loads((root / "shelters" / "capacity_pressure.json").read_text(encoding="utf-8"))
    failures: list[str] = []

    complete = [row for row in risk if row.get("score_status") == "complete"]
    noncomplete = [row for row in risk if row.get("score_status") != "complete"]
    if len(complete) != EXPECTED_COMPLETE:
        failures.append(f"complete={len(complete)} expected={EXPECTED_COMPLETE}")
    if len(noncomplete) != EXPECTED_NONCOMPLETE:
        failures.append(f"noncomplete={len(noncomplete)} expected={EXPECTED_NONCOMPLETE}")

    canonical_before = {
        str(row.get("mesh_id")): row.get("evacuation_difficulty_score") for row in risk
    }

    # The STEP 4 capacity component contract must still hold on complete rows.
    component_mismatch = 0
    for row in complete:
        pressure = number(row.get("capacity_pressure_area_weighted"))
        component = number(row.get("shelter_capacity_pressure_component_area_weighted"))
        canonical = number(row.get("evacuation_difficulty_score"))
        if pressure is None or component is None or canonical is None:
            component_mismatch += 1
            continue
        expected_component = min(max(pressure * 100.0, 0.0), 100.0)
        if abs(component - expected_component) > 1e-8:
            component_mismatch += 1
        recomputed = score_with_capacity_component(row, component)
        if recomputed is None or abs(recomputed - canonical) > 1e-8:
            component_mismatch += 1
    if component_mismatch:
        failures.append(f"canonical STEP 4 score/component mismatch rows={component_mismatch}")

    scenarios = [simulate(risk, shelters, float(delta)) for delta in DELTAS]
    for scenario in scenarios:
        if scenario["known_capacity_shelters"] != EXPECTED_KNOWN_CAPACITY_SHELTERS:
            failures.append(
                f"delta={scenario['capacity_delta']}: known capacity shelters={scenario['known_capacity_shelters']} "
                f"expected={EXPECTED_KNOWN_CAPACITY_SHELTERS}"
            )
        if scenario["baseline_over_capacity_shelters"] != BASELINE_OVER_CAPACITY:
            failures.append(
                f"delta={scenario['capacity_delta']}: baseline overload={scenario['baseline_over_capacity_shelters']} "
                f"expected={BASELINE_OVER_CAPACITY}"
            )
        if scenario["simulated_over_capacity_shelters"] > BASELINE_OVER_CAPACITY:
            failures.append(f"delta={scenario['capacity_delta']}: capacity increase created overload")
        if scenario["affected_complete_meshes"] != EXPECTED_COMPLETE:
            failures.append(
                f"delta={scenario['capacity_delta']}: affected complete={scenario['affected_complete_meshes']} "
                f"expected={EXPECTED_COMPLETE}"
            )
        if scenario["total_score_reduction"] < -1e-9:
            failures.append(f"delta={scenario['capacity_delta']}: negative total score improvement")

    # More capacity cannot increase overload or reduce total modeled improvement.
    for previous, current in zip(scenarios, scenarios[1:]):
        if current["simulated_over_capacity_shelters"] > previous["simulated_over_capacity_shelters"]:
            failures.append("larger capacity delta increased simulated overload count")
        if current["total_score_reduction"] + 1e-8 < previous["total_score_reduction"]:
            failures.append("larger capacity delta reduced modeled score improvement")

    canonical_after = {
        str(row.get("mesh_id")): row.get("evacuation_difficulty_score") for row in risk
    }
    if canonical_before != canonical_after:
        failures.append("canonical evacuation_difficulty_score mutated during simulation validation")

    return {
        "step": "STEP 7 - Policy Simulation / shelter capacity augmentation",
        "simulation_scope": {
            "changes": "hypothetical known shelter capacity only",
            "held_fixed": [
                "selected shelter",
                "route",
                "area-weighted demand",
                "tsunami component",
                "vulnerable-population component",
                "walking-accessibility component",
                "route-exposure component",
            ],
            "rerouting": False,
            "overflow_reassignment": False,
            "canonical_score_mutation": False,
            "missing_capacity_simulation": False,
        },
        "baseline": {
            "target_rows": len(risk),
            "complete_scores": len(complete),
            "noncomplete_rows": len(noncomplete),
            "over_capacity_shelters": BASELINE_OVER_CAPACITY,
        },
        "scenarios": scenarios,
        "release_gate": {"pass": not failures, "failures": failures},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-data-root", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    result = validate(args.public_data_root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["release_gate"]["pass"]:
        raise SystemExit("STEP 7 release gate failed: " + "; ".join(result["release_gate"]["failures"]))


if __name__ == "__main__":
    main()
