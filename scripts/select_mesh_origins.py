#!/usr/bin/env python3
"""Select auditable routing origins for 500m population meshes.

A geometric mesh centroid is a useful reference point but can fall offshore or
far from an OSM walkable edge. For routing, prefer the walk-network node inside
the 500m mesh that is closest to the centroid. If the mesh contains no network
node, fall back to the nearest network node and keep the connector distance and
status explicitly.

The off-network connector is *not* silently discarded. STEP 2 must add
``origin_connector_distance_m`` to the network shortest-path distance when
reporting walking distance/time.
"""

from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np
import osmnx as ox
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point, Polygon
from shapely.ops import transform
from shapely.strtree import STRtree

from mesh500 import mesh_centroid, mesh_polygon
from routing_foundation_qa import PROJECTED_CRS, as_walk_graph, snap_status


def build_node_index(graph):
    node_ids = list(graph.nodes)
    xs = [float(graph.nodes[node]["x"]) for node in node_ids]
    ys = [float(graph.nodes[node]["y"]) for node in node_ids]
    to_metric = Transformer.from_crs("EPSG:4326", PROJECTED_CRS, always_xy=True)
    mx, my = to_metric.transform(xs, ys)
    points = [Point(float(x), float(y)) for x, y in zip(mx, my)]
    return node_ids, points, STRtree(points), to_metric


def choose_origin(
    mesh_id: str,
    raw_node: object,
    raw_distance: float,
    node_ids: list[object],
    node_points: list[Point],
    tree: STRtree,
    to_metric: Transformer,
) -> dict[str, object]:
    lon, lat = mesh_centroid(mesh_id)
    centroid_metric = Point(*to_metric.transform(lon, lat))
    polygon_metric = transform(to_metric.transform, Polygon(mesh_polygon(mesh_id)))

    inside_indices = tree.query(polygon_metric, predicate="intersects")
    if len(inside_indices):
        best_index = min(
            (int(index) for index in inside_indices),
            key=lambda index: centroid_metric.distance(node_points[index]),
        )
        node = node_ids[best_index]
        connector_distance = float(centroid_metric.distance(node_points[best_index]))
        method = "walk_node_within_mesh"
        node_within_mesh = True
    else:
        node = raw_node
        connector_distance = float(raw_distance)
        method = "nearest_walk_node_fallback"
        node_within_mesh = False

    return {
        "mesh_id": str(mesh_id),
        "centroid_lon": float(lon),
        "centroid_lat": float(lat),
        "raw_centroid_nearest_node": raw_node,
        "raw_centroid_snap_distance_m": float(raw_distance),
        "representative_origin_node": node,
        "origin_connector_distance_m": connector_distance,
        "origin_method": method,
        "origin_node_within_mesh": node_within_mesh,
        "origin_status": snap_status(connector_distance),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mesh-csv", type=pathlib.Path, required=True)
    parser.add_argument("--tsunami-exposure-csv", type=pathlib.Path, required=True)
    parser.add_argument("--network-dir", type=pathlib.Path, required=True)
    parser.add_argument("--out-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-summary", type=pathlib.Path, required=True)
    parser.add_argument("--out-outliers", type=pathlib.Path, required=True)
    args = parser.parse_args()

    meshes = pd.read_csv(
        args.mesh_csv,
        encoding="utf-8-sig",
        dtype={"mesh_id": str, "municipality_code": str},
    )
    tsunami = pd.read_csv(
        args.tsunami_exposure_csv,
        encoding="utf-8-sig",
        dtype={"mesh_id": str},
    )
    tsunami["tsunami_inundation_ratio"] = pd.to_numeric(
        tsunami["tsunami_inundation_ratio"], errors="coerce"
    )
    tsunami_lookup = tsunami.set_index("mesh_id")["tsunami_inundation_ratio"]

    rows: list[dict[str, object]] = []
    for municipality, part in meshes.groupby("municipality", sort=True):
        code = str(part["municipality_code"].iloc[0])
        graph_path = args.network_dir / f"{code}.graphml"
        if not graph_path.exists():
            raise RuntimeError(f"GraphML missing: {graph_path}")
        graph = as_walk_graph(ox.io.load_graphml(filepath=graph_path))
        node_ids, node_points, tree, to_metric = build_node_index(graph)

        mesh_ids = part["mesh_id"].astype(str).tolist()
        centroids = [mesh_centroid(mesh_id) for mesh_id in mesh_ids]
        raw_nodes, raw_distances = ox.distance.nearest_nodes(
            graph,
            X=[point[0] for point in centroids],
            Y=[point[1] for point in centroids],
            return_dist=True,
        )
        raw_nodes = list(raw_nodes) if hasattr(raw_nodes, "__iter__") and not isinstance(raw_nodes, (str, bytes)) else [raw_nodes]
        raw_distances = list(raw_distances) if hasattr(raw_distances, "__iter__") and not isinstance(raw_distances, (str, bytes)) else [raw_distances]

        for mesh_row, raw_node, raw_distance in zip(part.itertuples(index=False), raw_nodes, raw_distances):
            selected = choose_origin(
                str(mesh_row.mesh_id),
                raw_node,
                float(raw_distance),
                node_ids,
                node_points,
                tree,
                to_metric,
            )
            ratio = tsunami_lookup.get(str(mesh_row.mesh_id), np.nan)
            selected.update(
                {
                    "municipality_code": code,
                    "municipality": municipality,
                    "tsunami_inundation_ratio": None if pd.isna(ratio) else float(ratio),
                    "analysis_target": bool(pd.notna(ratio) and float(ratio) > 0.0),
                }
            )
            rows.append(selected)
        print(f"{municipality}: selected origins={len(part)}", flush=True)

    result = pd.DataFrame(rows)
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.out_csv, index=False, encoding="utf-8-sig")
    result[result["origin_status"].isin(["warning", "critical"])].to_csv(
        args.out_outliers, index=False, encoding="utf-8-sig"
    )

    target = result[result["analysis_target"]].copy()
    target_critical = target[target["origin_status"] == "critical"]
    all_critical = result[result["origin_status"] == "critical"]
    summary = {
        "method": "nearest walk-network node inside each 500m mesh; nearest-node fallback only if the mesh contains no network node",
        "distance_accounting": "origin_connector_distance_m must be added to STEP 2 network path distance/time; it is not discarded",
        "projected_crs": PROJECTED_CRS,
        "mesh_count": int(len(result)),
        "analysis_target_definition": "tsunami_inundation_ratio > 0",
        "analysis_target_mesh_count": int(len(target)),
        "origin_method_counts": result["origin_method"].value_counts().to_dict(),
        "origin_status_counts": result["origin_status"].value_counts().to_dict(),
        "analysis_target_origin_status_counts": target["origin_status"].value_counts().to_dict(),
        "raw_centroid_critical_count": int((result["raw_centroid_snap_distance_m"] > 500).sum()),
        "representative_origin_critical_count": int(len(all_critical)),
        "analysis_target_critical_count": int(len(target_critical)),
        "analysis_target_fallback_count": int((target["origin_method"] == "nearest_walk_node_fallback").sum()),
        "max_analysis_target_connector_distance_m": (
            float(target["origin_connector_distance_m"].max()) if len(target) else None
        ),
        "release_gate": {
            "all_meshes_have_representative_origin": bool(result["representative_origin_node"].notna().all()),
            "all_meshes_checked": int(len(result)) == int(len(meshes)),
            "analysis_target_critical_count": int(len(target_critical)),
            "blocking": int(len(target_critical)) > 0,
            "note": "Non-target critical records remain QA evidence. Any critical tsunami-exposed mesh blocks STEP 2 until explicitly resolved.",
        },
    }
    args.out_summary.parent.mkdir(parents=True, exist_ok=True)
    args.out_summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
