#!/usr/bin/env python3
"""Assign each 500m mesh to the nearest tsunami-compatible shelter by walk distance."""

from __future__ import annotations

import argparse
import json
import pathlib
from collections import defaultdict

import networkx as nx
import osmnx as ox
import pandas as pd

from mesh500 import mesh_centroid


COASTAL = {
    "38201": "松山市", "38202": "今治市", "38203": "宇和島市", "38204": "八幡浜市",
    "38205": "新居浜市", "38206": "西条市", "38207": "大洲市", "38210": "伊予市",
    "38213": "四国中央市", "38214": "西予市", "38356": "上島町", "38401": "松前町",
    "38442": "伊方町", "38506": "愛南町",
}


def as_walk_graph(graph: nx.MultiDiGraph) -> nx.Graph:
    """Use the shortest parallel edge as an undirected pedestrian graph."""
    result = nx.Graph()
    # OSMnx's nearest-node helper reads the CRS from graph metadata. Preserve
    # it when converting the directed GraphML network to an undirected graph.
    result.graph.update(graph.graph)
    result.graph.setdefault("crs", "EPSG:4326")
    result.add_nodes_from(graph.nodes(data=True))
    for u, v, data in graph.edges(data=True):
        try:
            length = float(data.get("length", 0.0))
        except (TypeError, ValueError):
            continue
        if not result.has_edge(u, v) or length < result[u][v].get("length", float("inf")):
            result.add_edge(u, v, length=length)
    return result


def shelter_municipality(row: pd.Series) -> str | None:
    # The prefectural XLSX address field omits the municipality for a small
    # number of facilities (notably Shikoku-Chuo). The common ID's first five
    # digits are the stable municipality key, so use it before text matching.
    common_id = str(row.get("common_id") or "")
    if common_id.startswith("E") and common_id[1:6] in COASTAL:
        return COASTAL[common_id[1:6]]
    text = " ".join(str(row.get(column) or "") for column in ["address", "address_city", "address_pref"])
    for name in COASTAL.values():
        if name in text:
            return name
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mesh-csv", type=pathlib.Path, required=True)
    parser.add_argument("--shelters-csv", type=pathlib.Path, required=True)
    parser.add_argument("--network-dir", type=pathlib.Path, required=True)
    parser.add_argument("--out-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-qa", type=pathlib.Path, required=True)
    args = parser.parse_args()

    meshes = pd.read_csv(args.mesh_csv, encoding="utf-8-sig", dtype={"mesh_id": str, "municipality_code": str})
    shelters = pd.read_csv(args.shelters_csv, encoding="utf-8-sig")
    shelters["shelter_municipality"] = shelters.apply(shelter_municipality, axis=1)
    shelters["tsunami_flag"] = pd.to_numeric(shelters["tsunami"], errors="coerce")
    shelters = shelters[(shelters["tsunami_flag"] == 1) & shelters["latitude"].notna() & shelters["longitude"].notna()].copy()

    rows: list[dict[str, object]] = []
    qa: list[dict[str, object]] = []
    for municipality, mesh_part in meshes.groupby("municipality", sort=True):
        code = str(mesh_part["municipality_code"].iloc[0])
        graph_path = args.network_dir / f"{code}.graphml"
        shelter_part = shelters[shelters["shelter_municipality"] == municipality].copy()
        status = "complete"
        failure = None
        try:
            raw_graph = ox.io.load_graphml(filepath=graph_path)
            graph = as_walk_graph(raw_graph)
            if graph.number_of_nodes() == 0 or shelter_part.empty:
                raise RuntimeError("network or tsunami-compatible shelter candidates are empty")
            shelter_nodes = ox.distance.nearest_nodes(
                graph,
                X=shelter_part["longitude"].tolist(),
                Y=shelter_part["latitude"].tolist(),
            )
            if not isinstance(shelter_nodes, list):
                shelter_nodes = list(shelter_nodes)
            node_to_shelters: dict[object, list[str]] = defaultdict(list)
            for shelter_id, node in zip(shelter_part["common_id"].astype(str), shelter_nodes):
                node_to_shelters[node].append(shelter_id)
            distances, paths = nx.multi_source_dijkstra(graph, list(node_to_shelters), weight="length")
            mesh_nodes = ox.distance.nearest_nodes(
                graph,
                X=[mesh_centroid(mesh_id)[0] for mesh_id in mesh_part["mesh_id"]],
                Y=[mesh_centroid(mesh_id)[1] for mesh_id in mesh_part["mesh_id"]],
            )
            if not isinstance(mesh_nodes, list):
                mesh_nodes = list(mesh_nodes)
            for mesh_row, mesh_node in zip(mesh_part.itertuples(index=False), mesh_nodes):
                route_status = "complete"
                distance = distances.get(mesh_node)
                route_nodes = paths.get(mesh_node)
                shelter_id = None
                route_coordinates: list[list[float]] = []
                if distance is None or not route_nodes:
                    route_status = "no_network_path"
                else:
                    source_node = route_nodes[0]
                    shelter_id = node_to_shelters[source_node][0]
                    forward_nodes = list(reversed(route_nodes))
                    route_coordinates = [[float(graph.nodes[node]["x"]), float(graph.nodes[node]["y"])] for node in forward_nodes]
                rows.append({
                    "mesh_id": mesh_row.mesh_id,
                    "municipality_code": code,
                    "municipality": municipality,
                    "nearest_shelter_id": shelter_id,
                    "network_distance_m": float(distance) if distance is not None else None,
                    "walking_time_1_0_s": float(distance) / 1.0 if distance is not None else None,
                    "walking_time_0_62_s": float(distance) / 0.62 if distance is not None else None,
                    "walking_time_0_5_s": float(distance) / 0.5 if distance is not None else None,
                    "route_node_count": len(route_coordinates),
                    "route_coordinates": json.dumps(route_coordinates, ensure_ascii=False, separators=(",", ":")) if route_coordinates else None,
                    "route_status": route_status,
                })
        except Exception as exc:  # noqa: BLE001 - write null route results and continue
            status = "failed"
            failure = f"{type(exc).__name__}: {exc}"
            for mesh_row in mesh_part.itertuples(index=False):
                rows.append({
                    "mesh_id": mesh_row.mesh_id, "municipality_code": code, "municipality": municipality,
                    "nearest_shelter_id": None, "network_distance_m": None,
                    "walking_time_1_0_s": None, "walking_time_0_62_s": None, "walking_time_0_5_s": None,
                    "route_node_count": 0, "route_coordinates": None, "route_status": "network_failed",
                })
        qa.append({
            "municipality": municipality, "municipality_code": code, "mesh_count": len(mesh_part),
            "tsunami_candidate_shelters": len(shelter_part), "network_status": status, "error": failure,
        })
        print(f"{municipality}: meshes={len(mesh_part)} shelters={len(shelter_part)} status={status}", flush=True)

    result = pd.DataFrame(rows)
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.out_csv, index=False, encoding="utf-8-sig")
    args.out_qa.parent.mkdir(parents=True, exist_ok=True)
    args.out_qa.write_text(json.dumps({"municipalities": qa, "route_count": len(result), "complete_routes": int((result.route_status == "complete").sum()), "failed_routes": int((result.route_status != "complete").sum()), "speed_scenarios": {"standard": 1.0, "observed_reference": 0.62, "mobility_constrained": 0.5}, "speed_note": "0.5 m/s is a mobility-constrained scenario, not the walking speed of all people aged 65+"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"route_count": len(result), "complete_routes": int((result.route_status == "complete").sum()), "failed_routes": int((result.route_status != "complete").sum())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
