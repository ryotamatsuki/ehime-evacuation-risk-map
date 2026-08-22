#!/usr/bin/env python3
"""STEP 1 QA for routing AOIs, snapping, borders and island components.

This script intentionally does *not* generate the production mesh-to-shelter
routing table.  It is a release gate for the routing foundation used by STEP 2.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import zipfile
from collections import Counter, defaultdict

import networkx as nx
import osmnx as ox
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform, unary_union

PROJECTED_CRS = "EPSG:32653"
COASTAL = {
    "38201": "松山市", "38202": "今治市", "38203": "宇和島市", "38204": "八幡浜市",
    "38205": "新居浜市", "38206": "西条市", "38207": "大洲市", "38210": "伊予市",
    "38213": "四国中央市", "38214": "西予市", "38356": "上島町", "38401": "松前町",
    "38442": "伊方町", "38506": "愛南町",
}


def snap_status(distance_m: float | None) -> str:
    if distance_m is None:
        return "missing"
    if distance_m <= 100:
        return "normal"
    if distance_m <= 250:
        return "review"
    if distance_m <= 500:
        return "warning"
    return "critical"


def load_boundaries(boundary_zip: pathlib.Path) -> dict[str, object]:
    with zipfile.ZipFile(boundary_zip) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".geojson")]
        if not names:
            raise RuntimeError(f"GeoJSON not found in {boundary_zip}")
        payload = json.loads(archive.read(names[0]))
    grouped: dict[str, list[object]] = defaultdict(list)
    for feature in payload.get("features", []):
        code = str((feature.get("properties") or {}).get("N03_007") or "").strip()
        if code in COASTAL and feature.get("geometry"):
            grouped[code].append(shape(feature["geometry"]))
    return {code: unary_union(parts) for code, parts in grouped.items()}


def metric(geometry: object) -> object:
    transformer = Transformer.from_crs("EPSG:4326", PROJECTED_CRS, always_xy=True).transform
    return transform(transformer, geometry)


def wgs84(geometry: object) -> object:
    transformer = Transformer.from_crs(PROJECTED_CRS, "EPSG:4326", always_xy=True).transform
    return transform(transformer, geometry)


def buffered_aoi(boundary: object, buffer_m: float) -> object:
    return wgs84(metric(boundary).buffer(buffer_m))


def as_walk_graph(graph: nx.MultiDiGraph) -> nx.Graph:
    result = nx.Graph()
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
    common_id = str(row.get("common_id") or "")
    if common_id.startswith("E") and common_id[1:6] in COASTAL:
        return COASTAL[common_id[1:6]]
    text = " ".join(str(row.get(column) or "") for column in ["address", "address_city", "address_pref"])
    for name in COASTAL.values():
        if name in text:
            return name
    return None


def nearest_nodes_with_distance(graph: nx.Graph, xs: list[float], ys: list[float]) -> tuple[list[object], list[float]]:
    nodes, distances = ox.distance.nearest_nodes(graph, X=xs, Y=ys, return_dist=True)
    if not hasattr(nodes, "__iter__") or isinstance(nodes, (str, bytes)):
        nodes = [nodes]
        distances = [distances]
    return list(nodes), [float(value) for value in distances]


def source_lookup(rows: pd.DataFrame, nodes: list[object]) -> dict[object, dict[str, object]]:
    lookup: dict[object, dict[str, object]] = {}
    for (_, row), node in zip(rows.iterrows(), nodes):
        if node in lookup:
            continue
        lookup[node] = {
            "common_id": str(row.get("common_id") or ""),
            "name": str(row.get("name") or ""),
            "municipality": row.get("shelter_municipality"),
        }
    return lookup


def ferry_edge_count(raw_graph: nx.MultiDiGraph) -> int:
    count = 0
    for _, _, data in raw_graph.edges(data=True):
        values = [data.get("route"), data.get("highway"), data.get("ferry")]
        flattened = " ".join(str(value).lower() for value in values if value is not None)
        if "ferry" in flattened:
            count += 1
    return count


def geometry_part_count(geometry: object) -> int:
    if getattr(geometry, "geom_type", "") == "MultiPolygon":
        return len(getattr(geometry, "geoms", []))
    return 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mesh-csv", type=pathlib.Path, required=True)
    parser.add_argument("--shelters-csv", type=pathlib.Path, required=True)
    parser.add_argument("--boundary-zip", type=pathlib.Path, required=True)
    parser.add_argument("--network-dir", type=pathlib.Path, required=True)
    parser.add_argument("--out-dir", type=pathlib.Path, required=True)
    parser.add_argument("--buffer-m", type=float, default=3000.0)
    parser.add_argument("--border-distance-m", type=float, default=3000.0)
    args = parser.parse_args()

    from mesh500 import mesh_centroid

    args.out_dir.mkdir(parents=True, exist_ok=True)
    meshes = pd.read_csv(args.mesh_csv, encoding="utf-8-sig", dtype={"mesh_id": str, "municipality_code": str})
    shelters = pd.read_csv(args.shelters_csv, encoding="utf-8-sig", dtype={"common_id": str})
    shelters["tsunami_flag"] = pd.to_numeric(shelters["tsunami"], errors="coerce")
    shelters["latitude"] = pd.to_numeric(shelters["latitude"], errors="coerce")
    shelters["longitude"] = pd.to_numeric(shelters["longitude"], errors="coerce")
    shelters["shelter_municipality"] = shelters.apply(shelter_municipality, axis=1)
    shelters = shelters[
        shelters["tsunami_flag"].eq(1)
        & shelters["latitude"].notna()
        & shelters["longitude"].notna()
    ].copy()

    boundaries = load_boundaries(args.boundary_zip)
    origin_rows: list[dict[str, object]] = []
    shelter_rows: list[dict[str, object]] = []
    cross_border_rows: list[dict[str, object]] = []
    island_rows: list[dict[str, object]] = []
    municipality_summary: list[dict[str, object]] = []

    for municipality, mesh_part in meshes.groupby("municipality", sort=True):
        code = str(mesh_part["municipality_code"].iloc[0])
        boundary = boundaries.get(code)
        if boundary is None:
            raise RuntimeError(f"boundary missing: {municipality} ({code})")
        graph_path = args.network_dir / f"{code}.graphml"
        if not graph_path.exists():
            raise RuntimeError(f"GraphML missing: {graph_path}")

        raw_graph = ox.io.load_graphml(filepath=graph_path)
        graph = as_walk_graph(raw_graph)
        aoi = buffered_aoi(boundary, args.buffer_m)

        mesh_xy = [mesh_centroid(mesh_id) for mesh_id in mesh_part["mesh_id"]]
        mesh_nodes, mesh_snap = nearest_nodes_with_distance(
            graph,
            [point[0] for point in mesh_xy],
            [point[1] for point in mesh_xy],
        )
        for mesh_row, node, distance in zip(mesh_part.itertuples(index=False), mesh_nodes, mesh_snap):
            origin_rows.append(
                {
                    "mesh_id": mesh_row.mesh_id,
                    "municipality_code": code,
                    "municipality": municipality,
                    "nearest_node": node,
                    "origin_snap_distance_m": distance,
                    "snap_status": snap_status(distance),
                }
            )

        candidate_mask = shelters.apply(
            lambda row: aoi.covers(Point(float(row["longitude"]), float(row["latitude"]))), axis=1
        )
        candidate_shelters = shelters[candidate_mask].copy()
        same_shelters = candidate_shelters[candidate_shelters["shelter_municipality"] == municipality].copy()
        external_shelters = candidate_shelters[candidate_shelters["shelter_municipality"] != municipality].copy()

        candidate_nodes: list[object] = []
        if not candidate_shelters.empty:
            candidate_nodes, candidate_snap = nearest_nodes_with_distance(
                graph,
                candidate_shelters["longitude"].astype(float).tolist(),
                candidate_shelters["latitude"].astype(float).tolist(),
            )
            for (_, row), node, distance in zip(candidate_shelters.iterrows(), candidate_nodes, candidate_snap):
                shelter_rows.append(
                    {
                        "context_municipality_code": code,
                        "context_municipality": municipality,
                        "common_id": row.get("common_id"),
                        "name": row.get("name"),
                        "shelter_municipality": row.get("shelter_municipality"),
                        "nearest_node": node,
                        "shelter_snap_distance_m": distance,
                        "snap_status": snap_status(distance),
                    }
                )

        # Cross-border gate: compare same-municipality and all-AOI candidates for
        # meshes within border_distance_m of the administrative boundary.
        if not same_shelters.empty and not candidate_shelters.empty:
            same_nodes, _ = nearest_nodes_with_distance(
                graph,
                same_shelters["longitude"].astype(float).tolist(),
                same_shelters["latitude"].astype(float).tolist(),
            )
            all_lookup = source_lookup(candidate_shelters, candidate_nodes)
            same_lookup = source_lookup(same_shelters, same_nodes)
            all_dist, all_paths = nx.multi_source_dijkstra(graph, list(all_lookup), weight="length")
            same_dist, same_paths = nx.multi_source_dijkstra(graph, list(same_lookup), weight="length")
            boundary_metric = metric(boundary)
            to_metric = Transformer.from_crs("EPSG:4326", PROJECTED_CRS, always_xy=True).transform

            for mesh_row, mesh_node, (lon, lat) in zip(mesh_part.itertuples(index=False), mesh_nodes, mesh_xy):
                point_metric = transform(to_metric, Point(lon, lat))
                boundary_distance = float(point_metric.distance(boundary_metric.boundary))
                if boundary_distance > args.border_distance_m:
                    continue
                distance_all = all_dist.get(mesh_node)
                distance_same = same_dist.get(mesh_node)
                path_all = all_paths.get(mesh_node)
                if distance_all is None or path_all is None:
                    continue
                source_all = path_all[0]
                selected_all = all_lookup.get(source_all)
                if not selected_all:
                    continue
                if selected_all.get("municipality") == municipality:
                    continue
                if distance_same is not None and float(distance_all) >= float(distance_same) - 1.0:
                    continue
                cross_border_rows.append(
                    {
                        "mesh_id": mesh_row.mesh_id,
                        "municipality": municipality,
                        "municipality_code": code,
                        "distance_to_municipal_boundary_m": boundary_distance,
                        "same_municipality_distance_m": float(distance_same) if distance_same is not None else None,
                        "all_candidate_distance_m": float(distance_all),
                        "distance_improvement_m": (
                            float(distance_same) - float(distance_all) if distance_same is not None else None
                        ),
                        "external_shelter_id": selected_all.get("common_id"),
                        "external_shelter_name": selected_all.get("name"),
                        "external_shelter_municipality": selected_all.get("municipality"),
                    }
                )

        component_sizes = sorted((len(c) for c in nx.connected_components(graph)), reverse=True)
        municipality_summary.append(
            {
                "municipality": municipality,
                "municipality_code": code,
                "mesh_count": len(mesh_part),
                "candidate_shelters_in_aoi": len(candidate_shelters),
                "same_municipality_shelters": len(same_shelters),
                "external_municipality_shelters": len(external_shelters),
                "components": len(component_sizes),
                "largest_component_ratio": (
                    component_sizes[0] / graph.number_of_nodes() if component_sizes and graph.number_of_nodes() else None
                ),
                "aoi_method": raw_graph.graph.get("aoi_method"),
                "aoi_buffer_m": raw_graph.graph.get("aoi_buffer_m"),
            }
        )
        if municipality in {"今治市", "上島町"}:
            island_rows.append(
                {
                    "municipality": municipality,
                    "municipality_code": code,
                    "boundary_geometry_type": boundary.geom_type,
                    "boundary_polygon_parts": geometry_part_count(boundary),
                    "network_components": len(component_sizes),
                    "largest_component_nodes": component_sizes[0] if component_sizes else 0,
                    "largest_component_ratio": (
                        component_sizes[0] / graph.number_of_nodes() if component_sizes and graph.number_of_nodes() else None
                    ),
                    "component_sizes_top20": component_sizes[:20],
                    "ferry_tagged_edges": ferry_edge_count(raw_graph),
                    "review_note": "Disconnected island components are expected; no synthetic inter-island edge is created by this QA.",
                }
            )

    origins = pd.DataFrame(origin_rows)
    shelter_snaps = pd.DataFrame(shelter_rows)
    cross_border = pd.DataFrame(cross_border_rows)

    origins.to_csv(args.out_dir / "origin_snap_all.csv", index=False, encoding="utf-8-sig")
    origins[origins["snap_status"].isin(["warning", "critical"])].to_csv(
        args.out_dir / "origin_snap_outliers.csv", index=False, encoding="utf-8-sig"
    )
    shelter_snaps.to_csv(args.out_dir / "shelter_snap_all.csv", index=False, encoding="utf-8-sig")
    shelter_snaps[shelter_snaps["snap_status"].isin(["warning", "critical"])].to_csv(
        args.out_dir / "shelter_snap_outliers.csv", index=False, encoding="utf-8-sig"
    )
    cross_border.to_csv(args.out_dir / "cross_border_candidates.csv", index=False, encoding="utf-8-sig")

    origin_counts = Counter(origins["snap_status"])
    shelter_counts = Counter(shelter_snaps["snap_status"])
    origin_summary = {
        "mesh_count": len(origins),
        "status_counts": dict(origin_counts),
        "max_snap_distance_m": float(origins["origin_snap_distance_m"].max()) if len(origins) else None,
        "thresholds_m": {"normal": 100, "review": 250, "warning": 500, "critical": ">500"},
    }
    shelter_summary = {
        "context_shelter_snap_records": len(shelter_snaps),
        "status_counts": dict(shelter_counts),
        "max_snap_distance_m": float(shelter_snaps["shelter_snap_distance_m"].max()) if len(shelter_snaps) else None,
        "note": "A shelter can appear in multiple municipal AOI contexts; this is intentional for cross-border QA.",
    }
    (args.out_dir / "origin_snap_summary.json").write_text(
        json.dumps(origin_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.out_dir / "shelter_snap_summary.json").write_text(
        json.dumps(shelter_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.out_dir / "island_component_review.json").write_text(
        json.dumps(island_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    critical_origins = int((origins["snap_status"] == "critical").sum())
    critical_shelters = int((shelter_snaps["snap_status"] == "critical").sum())
    summary = {
        "step": "STEP 1 - Routing Foundation & Spatial QA",
        "projected_crs": PROJECTED_CRS,
        "buffer_m": args.buffer_m,
        "border_distance_m": args.border_distance_m,
        "municipalities": municipality_summary,
        "origin_snap": origin_summary,
        "shelter_snap": shelter_summary,
        "cross_border_preferable_cases": len(cross_border),
        "island_review": island_rows,
        "release_gate": {
            "network_aoi_is_boundary_based": all(
                item.get("aoi_method") == "municipality_multipolygon_metric_buffer"
                for item in municipality_summary
            ),
            "all_5821_meshes_snap_checked": len(origins) == len(meshes),
            "critical_origin_snap_count": critical_origins,
            "critical_shelter_snap_context_count": critical_shelters,
            "blocking": critical_origins > 0,
            "note": "Critical snap records require explicit review before STEP 2; they are not silently dropped.",
        },
    }
    (args.out_dir / "routing_foundation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
