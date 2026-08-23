#!/usr/bin/env python3
"""Capacity-constrained evacuation allocation core used by STEP 8 and STEP 9.

The canonical Analysis Core v4 remains unchanged.  This module adds a separate
counterfactual allocation layer.  It never interprets missing shelter capacity
as zero.  Strict scenarios route demand only to shelters with a known positive
capacity; candidates whose capacity is unknown are retained in diagnostics.

The solver is a deterministic integer min-cost flow:
- demand and capacity are scaled to tenths of a person-equivalent;
- every target mesh sends all scaled demand either to a known-capacity shelter
  or to an explicit unserved sink;
- the unserved penalty is deliberately much larger than any walking-distance
  cost so the objective is lexicographic in practice: maximise served demand,
  then minimise walking distance;
- STEP 9 can expose a global added-capacity budget through a shared augmentation
  pool.  Flow through that pool is the globally optimal placement of the
  available added capacity for the stated candidate graph and demand scenario.

The model is a planning scenario, not an evacuation forecast or an engineering
feasibility/cost estimate.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import networkx as nx
import numpy as np
import pandas as pd

DEMAND_SCALE = 10
DISTANCE_SCALE = 10
COST_TIE_MULTIPLIER = 100
UNSERVED_PENALTY_M = 1_000_000.0
DEFAULT_CANDIDATE_LIMIT = 10

REQUIRED_CANDIDATE_COLUMNS = {
    "mesh_id", "candidate_rank", "shelter_key", "shelter_common_id",
    "shelter_name", "total_walking_distance_m",
}
REQUIRED_MESH_COLUMNS = {
    "mesh_id", "route_status", "selected_shelter_common_id", "selected_shelter_name",
}
REQUIRED_CAPACITY_COLUMNS = {"shelter_key", "shelter_capacity"}


@dataclass(frozen=True)
class AllocationResult:
    mesh: pd.DataFrame
    flow: pd.DataFrame
    shelters: pd.DataFrame
    summary: dict[str, object]


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} missing required columns: {', '.join(missing)}")


def _to_units(value: float) -> int:
    return max(0, int(round(float(value) * DEMAND_SCALE)))


def _capacity_to_units(value: float) -> int:
    return max(0, int(math.floor(float(value) * DEMAND_SCALE + 1e-9))


def _from_units(value: int) -> float:
    return float(value) / DEMAND_SCALE


def _distance_cost(distance_m: float, candidate_rank: int) -> int:
    base = int(round(float(distance_m) * DISTANCE_SCALE))
    tie = max(0, min(int(candidate_rank), COST_TIE_MULTIPLIER - 1))
    return base * COST_TIE_MULTIPLIER + tie


def _unserved_cost() -> int:
    return int(round(UNSERVED_PENALTY_M * DISTANCE_SCALE)) * COST_TIE_MULTIPLIER


def prepare_candidate_table(candidates: pd.DataFrame, capacities: pd.DataFrame, candidate_limit: int = DEFAULT_CANDIDATE_LIMIT) -> pd.DataFrame:
    _require_columns(candidates, REQUIRED_CANDIDATE_COLUMNS, "candidate routes")
    _require_columns(capacities, REQUIRED_CAPACITY_COLUMNS, "capacity table")
    if candidate_limit <= 0:
        raise ValueError("candidate_limit must be positive")
    frame = candidates.copy()
    frame["mesh_id"] = frame["mesh_id"].astype(str)
    frame["shelter_key"] = frame["shelter_key"].astype(str)
    frame["candidate_rank"] = pd.to_numeric(frame["candidate_rank"], errors="coerce")
    frame["total_walking_distance_m"] = pd.to_numeric(frame["total_walking_distance_m"], errors="coerce")
    frame = frame.loc[
        frame["candidate_rank"].between(1, candidate_limit)
        & frame["total_walking_distance_m"].notna()
        & frame["total_walking_distance_m"].ge(0)
        & frame["shelter_key"].ne("")
    ].copy()
    if frame.duplicated(["mesh_id", "shelter_key"]).any():
        raise ValueError("duplicate mesh_id/shelter_key candidate rows")
    ranks = frame.groupby("mesh_id")["candidate_rank"].apply(lambda s: sorted(int(v) for v in s))
    for mesh_id, values in ranks.items():
        if values != list(range(1, len(values) + 1)):
            raise ValueError(f"non-consecutive candidate ranks for mesh {mesh_id}: {values}")
    cap = capacities.copy()
    cap["shelter_key"] = cap["shelter_key"].astype(str)
    cap["shelter_capacity"] = pd.to_numeric(cap["shelter_capacity"], errors="coerce")
    if cap.duplicated("shelter_key").any():
        raise ValueError("capacity table contains duplicate shelter_key")
    keep = [c for c in ("shelter_key", "shelter_capacity", "capacity_status", "common_id", "shelter_name", "shelter_city") if c in cap.columns]
    frame = frame.merge(cap[keep], on="shelter_key", how="left", validate="many_to_one")
    known = frame["shelter_capacity"].notna() & frame["shelter_capacity"].gt(0)
    frame["capacity_known"] = known
    frame["capacity_status"] = np.where(known, "available", frame.get("capacity_status", pd.Series("missing", index=frame.index)).fillna("missing"))
    return frame.sort_values(["mesh_id", "candidate_rank", "shelter_key"]).reset_index(drop=True)


def solve_capacity_allocation(candidates: pd.DataFrame, mesh_analysis: pd.DataFrame, capacities: pd.DataFrame, *, demand_column: str, candidate_limit: int = DEFAULT_CANDIDATE_LIMIT, added_capacity_budget: float = 0.0, per_shelter_added_capacity_max: float | None = None, scenario_name: str = "area_weighted") -> AllocationResult:
    _require_columns(mesh_analysis, REQUIRED_MESH_COLUMNS | {demand_column}, "mesh analysis")
    prepared = prepare_candidate_table(candidates, capacities, candidate_limit)
    mesh = mesh_analysis.copy()
    mesh["mesh_id"] = mesh["mesh_id"].astype(str)
    if mesh.duplicated("mesh_id").any():
        raise ValueError("mesh analysis contains duplicate mesh_id")
    mesh[demand_column] = pd.to_numeric(mesh[demand_column], errors="coerce")
    if mesh[demand_column].isna().any() or mesh[demand_column].lt(0).any():
        raise ValueError(f"{demand_column} must be non-negative and complete")
    demand_units_by_mesh = {row.mesh_id: _to_units(getattr(row, demand_column)) for row in mesh[["mesh_id", demand_column]].itertuples(index=False)}
    total_units = int(sum(demand_units_by_mesh.values()))
    raw_total = float(mesh[demand_column].sum())
    scaled_total = _from_units(total_units)
    rounding_error = scaled_total - raw_total
    candidate_groups = {mid: group for mid, group in prepared.groupby("mesh_id", sort=False)}
    known_edges = prepared.loc[prepared["capacity_known"]].copy()
    capacity_frame = capacities.copy()
    capacity_frame["shelter_key"] = capacity_frame["shelter_key"].astype(str)
    capacity_frame["shelter_capacity"] = pd.to_numeric(capacity_frame["shelter_capacity"], errors="coerce")
    known_capacity = capacity_frame.loc[capacity_frame["shelter_capacity"].notna() & capacity_frame["shelter_capacity"].gt(0)].copy()
    capacity_map = {str(row.shelter_key): float(row.shelter_capacity) for row in known_capacity[["shelter_key", "shelter_capacity"]].itertuples(index=False)}

    source, sink, unserved, augmentation = "__source__", "__sink__", "__unserved__", "__augmentation_pool__"
    graph = nx.DiGraph()
    graph.add_node(source, demand=-total_units)
    graph.add_node(sink, demand=total_units)
    graph.add_node(unserved, demand=0)
    graph.add_edge(unserved, sink, capacity=total_units, weight=0)
    for mesh_id, units in demand_units_by_mesh.items():
        mesh_node = f"m:{mesh_id}"
        graph.add_node(mesh_node, demand=0)
        if units <= 0:
            continue
        graph.add_edge(source, mesh_node, capacity=units, weight=0)
        graph.add_edge(mesh_node, unserved, capacity=units, weight=_unserved_cost())

    used_shelter_keys = sorted(set(known_edges["shelter_key"].astype(str)))
    budget_units = _to_units(max(0.0, float(added_capacity_budget)))
    if budget_units > 0:
        graph.add_node(augmentation, demand=0)
        graph.add_edge(augmentation, sink, capacity=budget_units, weight=0)
    for shelter_key in used_shelter_keys:
        capacity = capacity_map.get(shelter_key)
        if capacity is None:
            continue
        shelter_node = f"s:{shelter_key}"
        graph.add_node(shelter_node, demand=0)
        graph.add_edge(shelter_node, sink, capacity=_capacity_to_units(capacity), weight=0)
        if budget_units > 0:
            local_max = float(added_capacity_budget) if per_shelter_added_capacity_max is None else max(0.0, float(per_shelter_added_capacity_max))
            graph.add_edge(shelter_node, augmentation, capacity=min(budget_units, _to_units(local_max)), weight=0)
    for row in known_edges.itertuples(index=False):
        units = demand_units_by_mesh.get(str(row.mesh_id), 0)
        if units <= 0 or str(row.shelter_key) not in capacity_map:
            continue
        graph.add_edge(f"m:{row.mesh_id}", f"s:{row.shelter_key}", capacity=units, weight=_distance_cost(float(row.total_walking_distance_m), int(row.candidate_rank)))
    objective_cost, flow_dict = nx.network_simplex(graph) if total_units else (0, {})
    meta = {(str(row.mesh_id), str(row.shelter_key)): row for row in prepared.itertuples(index=False)}

    flow_rows, mesh_rows = [], []
    shelter_alloc_units = {key: 0 for key in used_shelter_keys}
    for mesh_row in mesh.itertuples(index=False):
        mesh_id = str(mesh_row.mesh_id)
        demand_units = demand_units_by_mesh[mesh_id]
        mesh_flow = flow_dict.get(f"m:{mesh_id}", {})
        unserved_units = int(mesh_flow.get(unserved, 0))
        allocated_units = 0
        weighted_distance_units = 0.0
        allocated_shelters = []
        for target, units_raw in mesh_flow.items():
            if not target.startswith("s:"):
                continue
            units = int(units_raw)
            if units <= 0:
                continue
            shelter_key = target[2:]
            row = meta[(mesh_id, shelter_key)]
            distance = float(row.total_walking_distance_m)
            allocated_units += units
            weighted_distance_units += units * distance
            shelter_alloc_units[shelter_key] = shelter_alloc_units.get(shelter_key, 0) + units
            allocated_shelters.append((shelter_key, units, distance, row))
            flow_rows.append({"mesh_id": mesh_id, "shelter_key": shelter_key, "shelter_common_id": row.shelter_common_id, "shelter_name": row.shelter_name, "candidate_rank": int(row.candidate_rank), "cross_border": bool(getattr(row, "cross_border", False)), "total_walking_distance_m": distance, "allocated_demand": _from_units(units), "allocation_share_of_mesh_demand": float(units) / demand_units if demand_units > 0 else 0.0})
        group = candidate_groups.get(mesh_id)
        candidate_count = 0 if group is None else int(len(group))
        known_count = 0 if group is None else int(group["capacity_known"].sum())
        unknown_count = candidate_count - known_count
        nearest_distance = None
        nearest_key = None
        if group is not None and len(group):
            nearest = group.iloc[0]
            nearest_distance = float(nearest["total_walking_distance_m"])
            nearest_key = str(nearest["shelter_key"])
        mean_distance = weighted_distance_units / allocated_units if allocated_units > 0 else None
        added_distance = None if mean_distance is None or nearest_distance is None else mean_distance - nearest_distance
        dominant = min(allocated_shelters, key=lambda item: (-item[1], item[2], item[0]))[0] if allocated_shelters else None
        if demand_units == 0:
            status = "zero_demand"
        elif candidate_count == 0:
            status = "route_unavailable_or_no_candidate"
        elif known_count == 0:
            status = "unknown_capacity_only"
        elif unserved_units > 0 and allocated_units > 0:
            status = "capacity_constrained_partial"
        elif unserved_units > 0:
            status = "capacity_constrained_unserved"
        elif len(allocated_shelters) > 1:
            status = "capacity_constrained_split"
        elif dominant != nearest_key:
            status = "capacity_constrained_rerouted"
        else:
            status = "nearest_known_capacity"
        baseline_key = f"{getattr(mesh_row, 'selected_shelter_common_id', '')}||{getattr(mesh_row, 'selected_shelter_name', '')}"
        mesh_rows.append({"mesh_id": mesh_id, "route_status": str(mesh_row.route_status), "demand": _from_units(demand_units), "allocated_demand": _from_units(allocated_units), "unserved_demand": _from_units(unserved_units), "candidate_count": candidate_count, "known_capacity_candidate_count": known_count, "unknown_capacity_candidate_count": unknown_count, "nearest_candidate_shelter_key": nearest_key, "baseline_selected_shelter_key": baseline_key, "dominant_allocated_shelter_key": dominant, "allocated_shelter_count": len(allocated_shelters), "mean_allocated_walking_distance_m": mean_distance, "nearest_candidate_walking_distance_m": nearest_distance, "additional_walking_distance_m": added_distance, "allocation_status": status})

    shelter_rows, total_added_units = [], 0
    for shelter_key in used_shelter_keys:
        node_flow = flow_dict.get(f"s:{shelter_key}", {})
        added_units = int(node_flow.get(augmentation, 0)) if budget_units > 0 else 0
        total_added_units += added_units
        base_capacity = capacity_map[shelter_key]
        allocated_units = shelter_alloc_units.get(shelter_key, 0)
        total_available = _capacity_to_units(base_capacity) + added_units
        shelter_rows.append({"shelter_key": shelter_key, "shelter_capacity": base_capacity, "added_capacity_used": _from_units(added_units), "effective_capacity": _from_units(total_available), "allocated_demand": _from_units(allocated_units), "utilization": float(allocated_units) / total_available if total_available > 0 else None, "saturated": bool(total_available > 0 and allocated_units >= total_available)})

    mesh_result = pd.DataFrame(mesh_rows).sort_values("mesh_id").reset_index(drop=True)
    flow_result = pd.DataFrame(flow_rows)
    if len(flow_result):
        flow_result = flow_result.sort_values(["mesh_id", "candidate_rank", "shelter_key"]).reset_index(drop=True)
    shelter_result = pd.DataFrame(shelter_rows).sort_values("shelter_key").reset_index(drop=True)
    served = float(mesh_result["allocated_demand"].sum())
    unserved_total = scaled_total - served
    total_person_m = float((flow_result["allocated_demand"] * flow_result["total_walking_distance_m"]).sum()) if len(flow_result) else 0.0
    cross_border_demand = float(flow_result.loc[flow_result["cross_border"], "allocated_demand"].sum()) if len(flow_result) else 0.0
    summary = {"model": "capacity_constrained_min_cost_flow_v1", "scenario": scenario_name, "demand_column": demand_column, "candidate_limit": int(candidate_limit), "target_meshes": int(len(mesh_result)), "raw_total_demand": raw_total, "scaled_total_demand": scaled_total, "demand_scaling_rounding_error": rounding_error, "served_demand": served, "unserved_demand": unserved_total, "served_share": served / scaled_total if scaled_total else 1.0, "known_capacity_shelters_in_candidate_graph": int(len(used_shelter_keys)), "meshes_with_no_candidate": int(mesh_result["candidate_count"].eq(0).sum()), "meshes_with_unknown_capacity_only": int(mesh_result["allocation_status"].eq("unknown_capacity_only").sum()), "meshes_partially_unserved": int(mesh_result["allocation_status"].eq("capacity_constrained_partial").sum()), "meshes_fully_unserved": int(mesh_result["allocation_status"].isin(["capacity_constrained_unserved", "unknown_capacity_only", "route_unavailable_or_no_candidate"]).sum()), "meshes_split_across_shelters": int(mesh_result["allocated_shelter_count"].gt(1).sum()), "meshes_dominant_destination_changed": int((mesh_result["dominant_allocated_shelter_key"].notna() & mesh_result["baseline_selected_shelter_key"].ne(mesh_result["dominant_allocated_shelter_key"])).sum()), "cross_border_allocated_demand": cross_border_demand, "total_allocated_person_metres": total_person_m, "added_capacity_budget": float(max(0.0, added_capacity_budget)), "added_capacity_used": _from_units(total_added_units), "objective_integer_cost": int(objective_cost), "unserved_penalty_m": UNSERVED_PENALTY_M, "capacity_unknown_treatment": "excluded_from_strict_capacity_constraint; retained in diagnostics; never coerced to zero", "interpretation": "planning scenario; not an evacuation forecast, engineering feasibility study, or monetary cost-benefit analysis"}
    return AllocationResult(mesh_result, flow_result, shelter_result, summary)


def investment_plan_from_result(result: AllocationResult) -> pd.DataFrame:
    if result.shelters.empty:
        return result.shelters.copy()
    plan = result.shelters.loc[result.shelters["added_capacity_used"].gt(0)].copy()
    if plan.empty:
        return plan
    return plan.sort_values(["added_capacity_used", "allocated_demand", "shelter_key"], ascending=[False, False, True]).reset_index(drop=True)


def compare_investment_plans(left: pd.DataFrame, right: pd.DataFrame, budget: float) -> dict[str, object]:
    def as_map(frame: pd.DataFrame) -> dict[str, float]:
        if frame.empty:
            return {}
        return {str(row.shelter_key): float(row.added_capacity_used) for row in frame[["shelter_key", "added_capacity_used"]].itertuples(index=False) if float(row.added_capacity_used) > 0}
    a, b = as_map(left), as_map(right)
    keys_a, keys_b = set(a), set(b)
    union, intersection = keys_a | keys_b, keys_a & keys_b
    shared_capacity = sum(min(a.get(key, 0.0), b.get(key, 0.0)) for key in union)
    denominator = max(float(budget), 1e-9)
    return {"left_shelters": len(keys_a), "right_shelters": len(keys_b), "shared_shelters": len(intersection), "shelter_jaccard": len(intersection) / len(union) if union else 1.0, "shared_capacity": shared_capacity, "shared_capacity_share_of_budget": min(1.0, shared_capacity / denominator)}
