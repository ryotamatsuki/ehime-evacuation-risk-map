#!/usr/bin/env python3
"""STEP 2: route tsunami-exposed 500m meshes to reachable tsunami shelters.

This version deliberately removes the legacy same-municipality shelter
restriction.  For each home municipality, the walking graph covers the N03
municipal MultiPolygon plus a 3 km metric buffer.  Every tsunami-compatible
shelter with valid coordinates inside that AOI is a candidate, including
shelters across municipal borders.

Distance accounting is explicit:

    total_walking_distance_m
      = origin_access_distance_m
      + network_path_distance_m
      + shelter_connector_distance_m

No connector greater than 500 m is fabricated.  STEP 1 network coverage gaps
remain explicit failed routes and stay in the 1,090-mesh analysis denominator.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import pathlib
import re
from collections import Counter
from typing import Iterable

import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import LineString, Point

from build_walking_network import DEFAULT_BUFFER_M, buffered_aoi, load_boundaries
from mesh500 import mesh_centroid
from routing_foundation_qa import PROJECTED_CRS, as_walk_graph
from select_mesh_origins import choose_origin, metric_edge_index, metric_node_points

MAX_SHELTER_CONNECTOR_M = 500.0
WALKING_SPEEDS_MPS = (1.0, 0.62, 0.5)
ALLOWED_STATUSES = {
    "complete",
    "network_coverage_gap",
    "no_candidate_shelter_in_aoi",
    "all_candidate_shelters_snap_excluded",
    "no_network_path",
}


def municipality_code_from_common_id(common_id: object) -> str | None:
    match = re.match(r"^E(\d{5})", str(common_id or ""))
    return match.group(1) if match else None


def candidate_is_better(new: dict[str, object], old: dict[str, object]) -> bool:
    new_key = (float(new["shelter_connector_distance_m"]), str(new["common_id"]))
    old_key = (float(old["shelter_connector_distance_m"]), str(old["common_id"]))
    return new_key < old_key


def dedupe_candidates_by_node(candidates: Iterable[dict[str, object]]) -> tuple[list[dict[str, object]], int]:
    chosen: dict[object, dict[str, object]] = {}
    duplicate_count = 0
    for candidate in candidates:
        node = candidate["shelter_node"]
        if node not in chosen:
            chosen[node] = candidate
        else:
            duplicate_count += 1
            if candidate_is_better(candidate, chosen[node]):
                chosen[node] = candidate
    return list(chosen.values()), duplicate_count


def multisource_shelter_dijkstra(
    graph: nx.Graph,
    candidates: list[dict[str, object]],
) -> tuple[dict[object, float], dict[object, str], dict[object, object | None]]:
    """Label each reachable node with its least-cost shelter and next hop.

    Seeds use the shelter-to-network connector distance.  The heap tuple also
    carries common_id so equal-distance ties are deterministic.
    ``next_toward_shelter[node]`` points one graph step toward the selected
    shelter seed and is sufficient to reconstruct origin->shelter paths.
    """
    distances: dict[object, float] = {}
    owners: dict[object, str] = {}
    next_toward_shelter: dict[object, object | None] = {}
    heap: list[tuple[float, str, object]] = []

    for candidate in candidates:
        node = candidate["shelter_node"]
        common_id = str(candidate["common_id"])
        cost = float(candidate["shelter_connector_distance_m"])
        current = (distances.get(node, math.inf), owners.get(node, "\uffff"))
        if (cost, common_id) < current:
            distances[node] = cost
            owners[node] = common_id
            next_toward_shelter[node] = None
            heapq.heappush(heap, (cost, common_id, node))

    while heap:
        cost, common_id, node = heapq.heappop(heap)
        if cost != distances.get(node) or common_id != owners.get(node):
            continue
        for neighbor, data in graph[node].items():
            try:
                length = float(data.get("length", 0.0))
            except (TypeError, ValueError):
                continue
            if length < 0 or not math.isfinite(length):
                continue
            new_cost = cost + length
            old_cost = distances.get(neighbor, math.inf)
            old_owner = owners.get(neighbor, "\uffff")
            if new_cost < old_cost - 1e-9 or (
                abs(new_cost - old_cost) <= 1e-9 and common_id < old_owner
            ):
                distances[neighbor] = new_cost
                owners[neighbor] = common_id
                next_toward_shelter[neighbor] = node
                heapq.heappush(heap, (new_cost, common_id, neighbor))
    return distances, owners, next_toward_shelter


def origin_seed_options(origin: dict[str, object]) -> list[tuple[object, float]]:
    method = origin.get("origin_method")
    connector = float(origin.get("origin_connector_distance_m") or 0.0)
    if method == "walk_node_within_mesh":
        return [(origin["representative_origin_node"], connector)]
    if method in {"walk_edge_intersects_mesh", "nearest_walk_edge_fallback"}:
        return [
            (origin["origin_edge_u"], connector + float(origin["origin_edge_u_along_m"])),
            (origin["origin_edge_v"], connector + float(origin["origin_edge_v_along_m"])),
        ]
    return []


def choose_origin_seed(
    origin: dict[str, object],
    distances: dict[object, float],
    owners: dict[object, str],
) -> tuple[object, float, str, float] | None:
    choices: list[tuple[float, str, str, object, float]] = []
    for node, access_cost in origin_seed_options(origin):
        if node not in distances:
            continue
        owner = owners[node]
        total = float(access_cost) + float(distances[node])
        choices.append((total, owner, str(node), node, float(access_cost)))
    if not choices:
        return None
    total, owner, _node_sort, node, access_cost = min(choices)
    return node, access_cost, owner, total


def reconstruct_node_path(
    start_node: object,
    owner_common_id: str,
    owners: dict[object, str],
    next_toward_shelter: dict[object, object | None],
) -> list[object]:
    path = [start_node]
    seen = {start_node}
    node = start_node
    while next_toward_shelter.get(node) is not None:
        nxt = next_toward_shelter[node]
        if nxt in seen:
            raise RuntimeError("routing predecessor cycle detected")
        if owners.get(nxt) != owner_common_id:
            raise RuntimeError("routing owner changed along predecessor chain")
        path.append(nxt)
        seen.add(nxt)
        node = nxt
    return path


def parse_linestring(value: object) -> LineString | None:
    if value is None:
        return None
    if getattr(value, "geom_type", None) == "LineString":
        return value
    if isinstance(value, str):
        try:
            geometry = wkt.loads(value)
            return geometry if geometry.geom_type == "LineString" else None
        except Exception:  # noqa: BLE001
            return None
    return None


def best_raw_edge(raw_graph: nx.MultiDiGraph, u: object, v: object) -> dict[str, object] | None:
    candidates: list[dict[str, object]] = []
    for first, second in ((u, v), (v, u)):
        data_by_key = raw_graph.get_edge_data(first, second, default={})
        for data in data_by_key.values():
            try:
                length = float(data.get("length", math.inf))
            except (TypeError, ValueError):
                continue
            candidates.append({"from": first, "to": second, "length": length, "data": data})
    return min(candidates, key=lambda item: item["length"]) if candidates else None


def oriented_edge_coordinates(raw_graph: nx.MultiDiGraph, u: object, v: object) -> list[list[float]]:
    edge = best_raw_edge(raw_graph, u, v)
    u_point = [float(raw_graph.nodes[u]["x"]), float(raw_graph.nodes[u]["y"])]
    v_point = [float(raw_graph.nodes[v]["x"]), float(raw_graph.nodes[v]["y"])]
    if edge is None:
        return [u_point, v_point]
    geometry = parse_linestring(edge["data"].get("geometry"))
    if geometry is None:
        return [u_point, v_point]
    coords = [[float(x), float(y)] for x, y in geometry.coords]
    if len(coords) < 2:
        return [u_point, v_point]
    direct = (coords[0][0] - u_point[0]) ** 2 + (coords[0][1] - u_point[1]) ** 2
    reverse = (coords[-1][0] - u_point[0]) ** 2 + (coords[-1][1] - u_point[1]) ** 2
    if reverse < direct:
        coords.reverse()
    return coords


def node_path_coordinates(raw_graph: nx.MultiDiGraph, nodes: list[object]) -> list[list[float]]:
    if not nodes:
        return []
    if len(nodes) == 1:
        node = nodes[0]
        return [[float(raw_graph.nodes[node]["x"]), float(raw_graph.nodes[node]["y"])]]
    coordinates: list[list[float]] = []
    for u, v in zip(nodes, nodes[1:]):
        segment = oriented_edge_coordinates(raw_graph, u, v)
        if coordinates and segment and coordinates[-1] == segment[0]:
            coordinates.extend(segment[1:])
        else:
            coordinates.extend(segment)
    return coordinates


def route_network_coordinates(
    raw_graph: nx.MultiDiGraph,
    origin: dict[str, object],
    node_path: list[object],
) -> list[list[float]]:
    coordinates = node_path_coordinates(raw_graph, node_path)
    if origin.get("origin_method") not in {"walk_edge_intersects_mesh", "nearest_walk_edge_fallback"}:
        return coordinates
    x = origin.get("origin_edge_projection_x_m")
    y = origin.get("origin_edge_projection_y_m")
    if x is None or y is None:
        return coordinates
    inverse = Transformer.from_crs(PROJECTED_CRS, "EPSG:4326", always_xy=True)
    lon, lat = inverse.transform(float(x), float(y))
    projection = [float(lon), float(lat)]
    if not coordinates or coordinates[0] != projection:
        coordinates.insert(0, projection)
    return coordinates


def walking_time_fields(distance_m: float | None) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for speed in WALKING_SPEEDS_MPS:
        suffix = str(speed).replace(".", "p")
        minutes = None if distance_m is None else float(distance_m) / speed / 60.0
        result[f"walking_time_min_{suffix}mps"] = minutes
    return result


def tsunami_candidate_shelters(
    shelters: pd.DataFrame,
    aoi: object,
) -> pd.DataFrame:
    tsunami = pd.to_numeric(shelters["tsunami"], errors="coerce").eq(1)
    latitude = pd.to_numeric(shelters["latitude"], errors="coerce")
    longitude = pd.to_numeric(shelters["longitude"], errors="coerce")
    valid = tsunami & latitude.notna() & longitude.notna()
    subset = shelters.loc[valid].copy()
    subset["latitude"] = latitude.loc[valid]
    subset["longitude"] = longitude.loc[valid]
    inside = [
        bool(aoi.covers(Point(float(row.longitude), float(row.latitude))))
        for row in subset.itertuples(index=False)
    ]
    return subset.loc[inside].copy()


def snap_shelters(
    graph: nx.Graph,
    candidates: pd.DataFrame,
    home_code: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]], int]:
    if candidates.empty:
        return [], [], 0
    nodes, distances = ox.distance.nearest_nodes(
        graph,
        X=candidates["longitude"].astype(float).tolist(),
        Y=candidates["latitude"].astype(float).tolist(),
        return_dist=True,
    )
    if not hasattr(nodes, "__iter__") or isinstance(nodes, (str, bytes)):
        nodes, distances = [nodes], [distances]
    accepted: list[dict[str, object]] = []
    excluded: list[dict[str, object]] = []
    for (_, row), node, connector in zip(candidates.iterrows(), nodes, distances):
        shelter_code = municipality_code_from_common_id(row.get("common_id"))
        record = {
            "common_id": str(row.get("common_id") or ""),
            "name": str(row.get("name") or ""),
            "shelter_municipality_code": shelter_code,
            "home_municipality_code": home_code,
            "cross_border": None if shelter_code is None else shelter_code != home_code,
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "capacity": None if pd.isna(row.get("capacity")) else float(row.get("capacity")),
            "shelter_node": node,
            "shelter_connector_distance_m": float(connector),
        }
        if float(connector) > MAX_SHELTER_CONNECTOR_M:
            record["exclusion_reason"] = "shelter_connector_over_500m"
            excluded.append(record)
        else:
            accepted.append(record)
    deduped, duplicate_nodes = dedupe_candidates_by_node(accepted)
    return deduped, excluded, duplicate_nodes


def select_origins_for_targets(
    raw_graph: nx.MultiDiGraph,
    graph: nx.Graph,
    targets: pd.DataFrame,
) -> dict[str, dict[str, object]]:
    to_metric = Transformer.from_crs("EPSG:4326", PROJECTED_CRS, always_xy=True)
    node_ids, node_points, node_tree = metric_node_points(graph, to_metric)
    edge_records, edge_tree = metric_edge_index(raw_graph, to_metric)
    mesh_ids = targets["mesh_id"].astype(str).tolist()
    centroids = [mesh_centroid(mesh_id) for mesh_id in mesh_ids]
    raw_nodes, raw_distances = ox.distance.nearest_nodes(
        graph,
        X=[point[0] for point in centroids],
        Y=[point[1] for point in centroids],
        return_dist=True,
    )
    if not hasattr(raw_nodes, "__iter__") or isinstance(raw_nodes, (str, bytes)):
        raw_nodes, raw_distances = [raw_nodes], [raw_distances]
    result: dict[str, dict[str, object]] = {}
    for mesh_id, raw_node, raw_distance in zip(mesh_ids, raw_nodes, raw_distances):
        result[mesh_id] = choose_origin(
            mesh_id,
            raw_node,
            float(raw_distance),
            node_ids,
            node_points,
            node_tree,
            edge_records,
            edge_tree,
            raw_graph,
            to_metric,
        )
    return result


def base_failure_row(target: pd.Series, origin: dict[str, object] | None, status: str) -> dict[str, object]:
    row = {
        "mesh_id": str(target["mesh_id"]),
        "municipality_code": str(target["municipality_code"]),
        "municipality": str(target["municipality"]),
        "tsunami_inundation_ratio": float(target["tsunami_inundation_ratio"]),
        "route_status": status,
        "selected_shelter_common_id": None,
        "selected_shelter_name": None,
        "shelter_municipality_code": None,
        "cross_border": None,
        "origin_method": None if origin is None else origin.get("origin_method"),
        "origin_access_distance_m": None,
        "network_path_distance_m": None,
        "shelter_connector_distance_m": None,
        "total_walking_distance_m": None,
        "route_network_coordinates": None,
    }
    row.update(walking_time_fields(None))
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--municipality-code", required=True)
    parser.add_argument("--mesh-csv", type=pathlib.Path, required=True)
    parser.add_argument("--tsunami-exposure-csv", type=pathlib.Path, required=True)
    parser.add_argument("--shelters-csv", type=pathlib.Path, required=True)
    parser.add_argument("--boundary-zip", type=pathlib.Path, required=True)
    parser.add_argument("--graphml", type=pathlib.Path, required=True)
    parser.add_argument("--out-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-qa", type=pathlib.Path, required=True)
    parser.add_argument("--out-exclusions", type=pathlib.Path, required=True)
    parser.add_argument("--buffer-m", type=float, default=DEFAULT_BUFFER_M)
    args = parser.parse_args()

    home_code = str(args.municipality_code)
    population = pd.read_csv(
        args.mesh_csv, encoding="utf-8-sig", dtype={"mesh_id": str, "municipality_code": str}
    )
    exposure = pd.read_csv(args.tsunami_exposure_csv, encoding="utf-8-sig", dtype={"mesh_id": str})
    exposure["tsunami_inundation_ratio"] = pd.to_numeric(
        exposure["tsunami_inundation_ratio"], errors="coerce"
    )
    targets = population.merge(
        exposure[["mesh_id", "tsunami_inundation_ratio"]], on="mesh_id", how="inner", validate="one_to_one"
    )
    targets = targets[
        targets["municipality_code"].eq(home_code)
        & targets["tsunami_inundation_ratio"].gt(0)
    ].copy()
    targets = targets.sort_values("mesh_id")

    raw_graph = ox.io.load_graphml(filepath=args.graphml)
    graph = as_walk_graph(raw_graph)
    boundaries = load_boundaries(args.boundary_zip)
    if home_code not in boundaries:
        raise SystemExit(f"N03 boundary missing for municipality code {home_code}")
    aoi = buffered_aoi(boundaries[home_code], args.buffer_m)

    shelters = pd.read_csv(args.shelters_csv, encoding="utf-8-sig", dtype={"common_id": str})
    candidate_frame = tsunami_candidate_shelters(shelters, aoi)
    candidates, exclusions, duplicate_nodes = snap_shelters(graph, candidate_frame, home_code)
    candidate_by_id = {str(candidate["common_id"]): candidate for candidate in candidates}
    origins = select_origins_for_targets(raw_graph, graph, targets)

    if candidates:
        distances, owners, predecessors = multisource_shelter_dijkstra(graph, candidates)
    else:
        distances, owners, predecessors = {}, {}, {}

    rows: list[dict[str, object]] = []
    for _, target in targets.iterrows():
        mesh_id = str(target["mesh_id"])
        origin = origins[mesh_id]
        if origin.get("origin_method") == "network_coverage_gap":
            rows.append(base_failure_row(target, origin, "network_coverage_gap"))
            continue
        if candidate_frame.empty:
            rows.append(base_failure_row(target, origin, "no_candidate_shelter_in_aoi"))
            continue
        if not candidates:
            rows.append(base_failure_row(target, origin, "all_candidate_shelters_snap_excluded"))
            continue
        choice = choose_origin_seed(origin, distances, owners)
        if choice is None:
            rows.append(base_failure_row(target, origin, "no_network_path"))
            continue

        start_node, origin_access, owner_id, total = choice
        shelter = candidate_by_id[owner_id]
        node_path = reconstruct_node_path(start_node, owner_id, owners, predecessors)
        network_path = float(distances[start_node]) - float(shelter["shelter_connector_distance_m"])
        network_path = max(0.0, network_path)
        total_formula = origin_access + network_path + float(shelter["shelter_connector_distance_m"])
        if abs(total_formula - total) > 1e-6:
            raise RuntimeError(f"distance accounting mismatch for mesh {mesh_id}")
        coordinates = route_network_coordinates(raw_graph, origin, node_path)
        row = {
            "mesh_id": mesh_id,
            "municipality_code": home_code,
            "municipality": str(target["municipality"]),
            "tsunami_inundation_ratio": float(target["tsunami_inundation_ratio"]),
            "route_status": "complete",
            "selected_shelter_common_id": owner_id,
            "selected_shelter_name": shelter["name"],
            "shelter_municipality_code": shelter["shelter_municipality_code"],
            "cross_border": shelter["cross_border"],
            "origin_method": origin["origin_method"],
            "origin_access_distance_m": float(origin_access),
            "network_path_distance_m": network_path,
            "shelter_connector_distance_m": float(shelter["shelter_connector_distance_m"]),
            "total_walking_distance_m": total_formula,
            "route_network_coordinates": json.dumps(coordinates, ensure_ascii=False, separators=(",", ":")),
        }
        row.update(walking_time_fields(total_formula))
        rows.append(row)

    result = pd.DataFrame(rows)
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.out_csv, index=False, encoding="utf-8-sig")
    exclusions_frame = pd.DataFrame(exclusions)
    args.out_exclusions.parent.mkdir(parents=True, exist_ok=True)
    exclusions_frame.to_csv(args.out_exclusions, index=False, encoding="utf-8-sig")

    complete = result[result["route_status"].eq("complete")]
    status_counts = result["route_status"].value_counts().to_dict() if len(result) else {}
    qa = {
        "step": "STEP 2 - Cross-border mesh-to-shelter routing",
        "municipality_code": home_code,
        "municipality": None if targets.empty else str(targets["municipality"].iloc[0]),
        "target_definition": "tsunami_inundation_ratio > 0",
        "target_mesh_count": int(len(targets)),
        "route_row_count": int(len(result)),
        "route_status_counts": status_counts,
        "same_municipality_restriction": False,
        "aoi_buffer_m": float(args.buffer_m),
        "candidate_shelters_before_snap": int(len(candidate_frame)),
        "candidate_shelters_after_connector_filter_and_node_dedupe": int(len(candidates)),
        "candidate_shelter_node_duplicates_removed": int(duplicate_nodes),
        "excluded_shelter_connector_over_500m": int(len(exclusions)),
        "max_shelter_connector_m": MAX_SHELTER_CONNECTOR_M,
        "complete_routes": int(len(complete)),
        "cross_border_complete_routes": int(complete["cross_border"].eq(True).sum()) if len(complete) else 0,
        "distance_formula": "origin_access_distance_m + network_path_distance_m + shelter_connector_distance_m",
        "connectors_accounted": True,
        "walking_speeds_mps": list(WALKING_SPEEDS_MPS),
        "network_geometry_excludes_offnetwork_connector": True,
        "allowed_statuses": sorted(ALLOWED_STATUSES),
    }
    args.out_qa.parent.mkdir(parents=True, exist_ok=True)
    args.out_qa.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
