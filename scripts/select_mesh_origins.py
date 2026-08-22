#!/usr/bin/env python3
"""Select auditable routing origins for 500m population meshes.

A 500m mesh centroid is only a reference point. It can fall offshore or far
from an OSM graph node even when a walkable edge crosses the mesh. This module
therefore resolves origins in this order:

1. nearest walk-network node inside the mesh;
2. if no node is inside, nearest walk edge intersecting the mesh;
3. if no edge intersects, nearest walk edge within 500 m of the centroid;
4. otherwise classify the mesh as ``network_coverage_gap``.

For an edge-based origin, STEP 2 must create a virtual source connected to both
edge endpoints. The access cost is the centroid-to-edge projection distance
plus the along-edge distance to each endpoint. No >500 m network jump is
silently fabricated.
"""

from __future__ import annotations

import argparse
import json
import pathlib

import numpy as np
import osmnx as ox
import pandas as pd
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import transform
from shapely.strtree import STRtree

from mesh500 import mesh_centroid, mesh_polygon
from routing_foundation_qa import PROJECTED_CRS, as_walk_graph, snap_status

MAX_FALLBACK_CONNECTOR_M = 500.0


def metric_node_points(graph, to_metric: Transformer):
    node_ids = list(graph.nodes)
    xs = [float(graph.nodes[node]["x"]) for node in node_ids]
    ys = [float(graph.nodes[node]["y"]) for node in node_ids]
    mx, my = to_metric.transform(xs, ys)
    points = [Point(float(x), float(y)) for x, y in zip(mx, my)]
    return node_ids, points, STRtree(points)


def raw_edge_geometry(raw_graph, u, v, data):
    geometry = data.get("geometry")
    if geometry is not None:
        if isinstance(geometry, str):
            try:
                return wkt.loads(geometry)
            except Exception:  # noqa: BLE001 - fallback to endpoints
                pass
        elif getattr(geometry, "geom_type", None) == "LineString":
            return geometry
    first = raw_graph.nodes[u]
    second = raw_graph.nodes[v]
    return LineString(
        [
            (float(first["x"]), float(first["y"])),
            (float(second["x"]), float(second["y"])),
        ]
    )


def metric_edge_index(raw_graph, to_metric: Transformer):
    records: list[dict[str, object]] = []
    geometries = []
    seen: set[tuple[object, object, bytes]] = set()
    for u, v, _key, data in raw_graph.edges(keys=True, data=True):
        geometry = raw_edge_geometry(raw_graph, u, v, data)
        if geometry is None or geometry.is_empty:
            continue
        geometry_metric = transform(to_metric.transform, geometry)
        if geometry_metric.is_empty or geometry_metric.length <= 0:
            continue
        # Directed GraphML commonly contains the same physical edge twice.
        # De-duplicate by unordered endpoints plus metric geometry bytes.
        pair = tuple(sorted((str(u), str(v))))
        dedupe = (pair[0], pair[1], geometry_metric.wkb)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        records.append(
            {
                "u": u,
                "v": v,
                "geometry": geometry_metric,
                "graph_length_m": float(data.get("length", geometry_metric.length) or geometry_metric.length),
            }
        )
        geometries.append(geometry_metric)
    return records, STRtree(geometries)


def edge_origin_record(
    mesh_id: str,
    centroid_metric: Point,
    record: dict[str, object],
    raw_graph,
    to_metric: Transformer,
    method: str,
) -> dict[str, object]:
    edge = record["geometry"]
    projection_position = float(edge.project(centroid_metric))
    projection = edge.interpolate(projection_position)
    offnetwork = float(centroid_metric.distance(projection))
    u = record["u"]
    v = record["v"]
    u_data = raw_graph.nodes[u]
    v_data = raw_graph.nodes[v]
    ux, uy = to_metric.transform(float(u_data["x"]), float(u_data["y"]))
    vx, vy = to_metric.transform(float(v_data["x"]), float(v_data["y"]))
    u_position = float(edge.project(Point(float(ux), float(uy))))
    v_position = float(edge.project(Point(float(vx), float(vy))))
    return {
        "mesh_id": str(mesh_id),
        "representative_origin_node": None,
        "origin_connector_distance_m": offnetwork,
        "origin_method": method,
        "origin_node_within_mesh": False,
        "origin_status": snap_status(offnetwork),
        "origin_edge_u": u,
        "origin_edge_v": v,
        "origin_edge_projection_x_m": float(projection.x),
        "origin_edge_projection_y_m": float(projection.y),
        "origin_edge_u_along_m": abs(projection_position - u_position),
        "origin_edge_v_along_m": abs(projection_position - v_position),
        "origin_edge_geometry_length_m": float(edge.length),
        "origin_edge_graph_length_m": float(record["graph_length_m"]),
    }


def choose_origin(
    mesh_id: str,
    raw_node: object,
    raw_node_distance: float,
    node_ids: list[object],
    node_points: list[Point],
    node_tree: STRtree,
    edge_records: list[dict[str, object]],
    edge_tree: STRtree,
    raw_graph,
    to_metric: Transformer,
) -> dict[str, object]:
    lon, lat = mesh_centroid(mesh_id)
    centroid_metric = Point(*to_metric.transform(lon, lat))
    polygon_metric = transform(to_metric.transform, Polygon(mesh_polygon(mesh_id)))
    base = {
        "mesh_id": str(mesh_id),
        "centroid_lon": float(lon),
        "centroid_lat": float(lat),
        "raw_centroid_nearest_node": raw_node,
        "raw_centroid_snap_distance_m": float(raw_node_distance),
        "origin_edge_u": None,
        "origin_edge_v": None,
        "origin_edge_projection_x_m": None,
        "origin_edge_projection_y_m": None,
        "origin_edge_u_along_m": None,
        "origin_edge_v_along_m": None,
        "origin_edge_geometry_length_m": None,
        "origin_edge_graph_length_m": None,
    }

    inside_nodes = node_tree.query(polygon_metric, predicate="intersects")
    if len(inside_nodes):
        best_index = min(
            (int(index) for index in inside_nodes),
            key=lambda index: centroid_metric.distance(node_points[index]),
        )
        node = node_ids[best_index]
        connector = float(centroid_metric.distance(node_points[best_index]))
        base.update(
            {
                "representative_origin_node": node,
                "origin_connector_distance_m": connector,
                "origin_method": "walk_node_within_mesh",
                "origin_node_within_mesh": True,
                "origin_status": snap_status(connector),
            }
        )
        return base

    intersecting_edges = edge_tree.query(polygon_metric, predicate="intersects")
    if len(intersecting_edges):
        best_index = min(
            (int(index) for index in intersecting_edges),
            key=lambda index: centroid_metric.distance(edge_records[index]["geometry"]),
        )
        base.update(
            edge_origin_record(
                mesh_id,
                centroid_metric,
                edge_records[best_index],
                raw_graph,
                to_metric,
                "walk_edge_intersects_mesh",
            )
        )
        return base

    nearest_edge_index = int(edge_tree.nearest(centroid_metric))
    nearest_edge = edge_records[nearest_edge_index]
    nearest_edge_distance = float(centroid_metric.distance(nearest_edge["geometry"]))
    if nearest_edge_distance <= MAX_FALLBACK_CONNECTOR_M:
        base.update(
            edge_origin_record(
                mesh_id,
                centroid_metric,
                nearest_edge,
                raw_graph,
                to_metric,
                "nearest_walk_edge_fallback",
            )
        )
        return base

    base.update(
        {
            "representative_origin_node": None,
            "origin_connector_distance_m": nearest_edge_distance,
            "origin_method": "network_coverage_gap",
            "origin_node_within_mesh": False,
            "origin_status": "network_coverage_gap",
            "nearest_walk_edge_distance_m": nearest_edge_distance,
        }
    )
    return base


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
        raw_graph = ox.io.load_graphml(filepath=graph_path)
        graph = as_walk_graph(raw_graph)
        to_metric = Transformer.from_crs("EPSG:4326", PROJECTED_CRS, always_xy=True)
        node_ids, node_points, node_tree = metric_node_points(graph, to_metric)
        edge_records, edge_tree = metric_edge_index(raw_graph, to_metric)
        if not edge_records:
            raise RuntimeError(f"no walk edges available: {municipality} ({code})")

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
                node_tree,
                edge_records,
                edge_tree,
                raw_graph,
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
    outlier_mask = result["origin_status"].isin(["warning", "critical", "network_coverage_gap"])
    result[outlier_mask].to_csv(args.out_outliers, index=False, encoding="utf-8-sig")

    target = result[result["analysis_target"]].copy()
    target_gaps = target[target["origin_method"] == "network_coverage_gap"]
    unresolved = target[
        ~target["origin_method"].isin(
            [
                "walk_node_within_mesh",
                "walk_edge_intersects_mesh",
                "nearest_walk_edge_fallback",
                "network_coverage_gap",
            ]
        )
    ]
    summary = {
        "method": "node inside mesh; otherwise intersecting walk edge; otherwise nearest walk edge <=500 m; otherwise explicit network_coverage_gap",
        "distance_accounting": "STEP 2 uses virtual edge-origin costs (centroid-to-edge plus along-edge endpoint cost) or node connector cost; network_coverage_gap is never routed by a fabricated jump",
        "projected_crs": PROJECTED_CRS,
        "max_fallback_connector_m": MAX_FALLBACK_CONNECTOR_M,
        "mesh_count": int(len(result)),
        "analysis_target_definition": "tsunami_inundation_ratio > 0",
        "analysis_target_mesh_count": int(len(target)),
        "origin_method_counts": result["origin_method"].value_counts().to_dict(),
        "analysis_target_origin_method_counts": target["origin_method"].value_counts().to_dict(),
        "origin_status_counts": result["origin_status"].value_counts().to_dict(),
        "analysis_target_origin_status_counts": target["origin_status"].value_counts().to_dict(),
        "raw_centroid_node_snap_over_500_count": int((result["raw_centroid_snap_distance_m"] > 500).sum()),
        "analysis_target_network_coverage_gap_count": int(len(target_gaps)),
        "analysis_target_network_coverage_gap_mesh_ids": target_gaps["mesh_id"].astype(str).tolist(),
        "analysis_target_unresolved_count": int(len(unresolved)),
        "max_resolved_analysis_target_connector_distance_m": (
            float(target.loc[target["origin_method"] != "network_coverage_gap", "origin_connector_distance_m"].max())
            if len(target.loc[target["origin_method"] != "network_coverage_gap"])
            else None
        ),
        "release_gate": {
            "all_meshes_checked": int(len(result)) == int(len(meshes)),
            "all_analysis_targets_classified": int(len(unresolved)) == 0,
            "network_coverage_gaps_are_explicit": bool(
                (target_gaps["origin_status"] == "network_coverage_gap").all()
            ),
            "analysis_target_network_coverage_gap_count": int(len(target_gaps)),
            "analysis_target_unresolved_count": int(len(unresolved)),
            "blocking": int(len(unresolved)) > 0,
            "note": "Explicit network_coverage_gap meshes remain in STEP 2 as route failures/high-accessibility-risk evidence; they are not dropped and do not receive fabricated routes.",
        },
    }
    args.out_summary.parent.mkdir(parents=True, exist_ok=True)
    args.out_summary.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
