#!/usr/bin/env python3
"""Download OSM pedestrian networks for the populated analysis footprints.

The network is an ETL intermediate and is intentionally kept outside
``public/data`` and excluded from version control. Only route metrics and
selected route geometries are exported to the web application.
"""

from __future__ import annotations

import argparse
import json
import pathlib

def footprint(points: list[tuple[float, float]], buffer_m: float):
    from pyproj import Transformer
    from shapely.geometry import MultiPoint
    from shapely.ops import transform

    wgs84_to_web = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform
    web_to_wgs84 = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True).transform
    hull = MultiPoint(points).convex_hull
    return transform(web_to_wgs84, transform(wgs84_to_web, hull).buffer(buffer_m))


def build_one(municipality: str, points: list[tuple[float, float]], args: argparse.Namespace) -> dict[str, object]:
    import networkx as nx
    import osmnx as ox
    from shapely.geometry import mapping

    polygon = footprint(points, args.buffer_m)
    ox.settings.use_cache = True
    ox.settings.requests_timeout = args.timeout
    ox.settings.overpass_rate_limit = False
    graph_path = args.out_dir / f"{args.code_by_name[municipality]}.graphml"
    qa_path = args.qa_dir / f"{args.code_by_name[municipality]}_network.json"
    result: dict[str, object] = {
        "municipality": municipality,
        "municipality_code": args.code_by_name[municipality],
        "network_type": "walk",
        "osm_attribution": "© OpenStreetMap contributors; ODbL",
        "footprint": mapping(polygon),
        "buffer_m": args.buffer_m,
        "status": "failed",
    }
    try:
        graph = ox.graph.graph_from_polygon(polygon, network_type="walk", retain_all=True, simplify=True)
        graph.graph["municipality"] = municipality
        graph.graph["network_type"] = "walk"
        graph.graph["source_attribution"] = "© OpenStreetMap contributors; ODbL"
        ox.io.save_graphml(graph, filepath=graph_path)
        undirected = nx.Graph(graph)
        result.update({
            "status": "complete",
            "nodes": graph.number_of_nodes(),
            "edges": graph.number_of_edges(),
            "weakly_connected_components": nx.number_weakly_connected_components(graph),
            "largest_component_nodes": max((len(c) for c in nx.weakly_connected_components(graph)), default=0),
            "graphml": str(graph_path),
        })
    except Exception as exc:  # noqa: BLE001 - preserve failure in QA and continue other municipalities
        result.update({"error_type": type(exc).__name__, "error": str(exc)})
    args.qa_dir.mkdir(parents=True, exist_ok=True)
    qa_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def write_summary(qa_dir: pathlib.Path) -> list[dict[str, object]]:
    """Rebuild the cumulative QA summary from all municipality-level results.

    A single-municipality retry must not erase QA evidence from earlier runs.
    """
    records: list[dict[str, object]] = []
    for qa_path in sorted(qa_dir.glob("*_network.json")):
        records.append(json.loads(qa_path.read_text(encoding="utf-8")))
    summary_path = qa_dir / "walking_network_summary.json"
    summary_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return records


def summary_metadata(records: list[dict[str, object]]) -> dict[str, object]:
    return {
        "qa_records": len(records),
        "complete_municipalities": [
            record["municipality"] for record in records if record.get("status") == "complete"
        ],
        "failed_municipalities": [
            record["municipality"] for record in records if record.get("status") != "complete"
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mesh-csv", type=pathlib.Path)
    parser.add_argument("--out-dir", type=pathlib.Path)
    parser.add_argument("--qa-dir", type=pathlib.Path, required=True)
    parser.add_argument("--municipality")
    parser.add_argument("--buffer-m", type=float, default=1000.0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()

    args.qa_dir.mkdir(parents=True, exist_ok=True)
    if args.summarize_only:
        print(json.dumps(summary_metadata(write_summary(args.qa_dir)), ensure_ascii=False, indent=2))
        return
    if args.mesh_csv is None or args.out_dir is None:
        parser.error("--mesh-csv and --out-dir are required unless --summarize-only is used")

    import pandas as pd
    from mesh500 import mesh_centroid

    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.code_by_name = dict(pd.read_csv(args.mesh_csv, dtype={"municipality_code": str})[["municipality", "municipality_code"]].drop_duplicates().itertuples(index=False, name=None))
    mesh = pd.read_csv(args.mesh_csv, dtype={"mesh_id": str})
    grouped: dict[str, list[tuple[float, float]]] = {}
    for row in mesh.itertuples(index=False):
        grouped.setdefault(row.municipality, []).append(mesh_centroid(row.mesh_id))
    names = [args.municipality] if args.municipality else sorted(grouped)
    missing = [name for name in names if name not in grouped]
    if missing:
        raise SystemExit(f"unknown municipality: {', '.join(missing)}")
    results = []
    for name in names:
        print(f"starting {name}", flush=True)
        result = build_one(name, grouped[name], args)
        results.append(result)
        print(f"finished {name}: {result.get('status')}", flush=True)
    print(json.dumps(summary_metadata(write_summary(args.qa_dir)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
