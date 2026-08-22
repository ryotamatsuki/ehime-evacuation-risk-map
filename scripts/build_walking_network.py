#!/usr/bin/env python3
"""Download OSM pedestrian networks using real municipal boundary geometry.

The routing AOI is derived from the MLIT N03 municipal MultiPolygon and a
metric buffer in UTM zone 53N.  This replaces the legacy population-centroid
convex hull, which could span water and truncate cross-border shelter access.

GraphML is an ETL intermediate and remains outside ``public/data`` and Git.
Only QA metadata and downstream route metrics are publishable outputs.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import zipfile
from collections import defaultdict

PROJECTED_CRS = "EPSG:32653"  # WGS84 / UTM zone 53N; Ehime lies within zone 53.
DEFAULT_BUFFER_M = 3000.0


def load_boundaries(boundary_zip: pathlib.Path) -> dict[str, object]:
    """Load Ehime municipal boundaries keyed by N03 municipality code."""
    from shapely.geometry import shape
    from shapely.ops import unary_union

    with zipfile.ZipFile(boundary_zip) as archive:
        candidates = [name for name in archive.namelist() if name.lower().endswith(".geojson")]
        if not candidates:
            raise RuntimeError(f"GeoJSON not found in {boundary_zip}")
        payload = json.loads(archive.read(candidates[0]))

    grouped: dict[str, list[object]] = defaultdict(list)
    for feature in payload.get("features", []):
        properties = feature.get("properties") or {}
        code = str(properties.get("N03_007") or "").strip()
        geometry = feature.get("geometry")
        if code and geometry:
            grouped[code].append(shape(geometry))
    return {code: unary_union(parts) for code, parts in grouped.items()}


def buffered_aoi(boundary: object, buffer_m: float) -> object:
    """Buffer an administrative geometry by metres without Web Mercator."""
    from pyproj import Transformer
    from shapely.ops import transform

    to_metric = Transformer.from_crs("EPSG:4326", PROJECTED_CRS, always_xy=True).transform
    to_wgs84 = Transformer.from_crs(PROJECTED_CRS, "EPSG:4326", always_xy=True).transform
    metric = transform(to_metric, boundary)
    return transform(to_wgs84, metric.buffer(buffer_m))


def area_km2(geometry: object) -> float:
    from pyproj import Transformer
    from shapely.ops import transform

    to_metric = Transformer.from_crs("EPSG:4326", PROJECTED_CRS, always_xy=True).transform
    return float(transform(to_metric, geometry).area / 1_000_000.0)


def build_one(
    municipality: str,
    municipality_code: str,
    boundary: object,
    args: argparse.Namespace,
) -> dict[str, object]:
    import networkx as nx
    import osmnx as ox
    from shapely.geometry import mapping

    aoi = buffered_aoi(boundary, args.buffer_m)
    ox.settings.use_cache = True
    ox.settings.requests_timeout = args.timeout
    ox.settings.overpass_rate_limit = True

    graph_path = args.out_dir / f"{municipality_code}.graphml"
    qa_path = args.qa_dir / f"{municipality_code}_network.json"
    result: dict[str, object] = {
        "municipality": municipality,
        "municipality_code": municipality_code,
        "network_type": "walk",
        "osm_attribution": "© OpenStreetMap contributors; ODbL",
        "boundary_source": str(args.boundary_zip),
        "aoi_method": "municipality_multipolygon_metric_buffer",
        "projected_crs": PROJECTED_CRS,
        "boundary_geometry_type": getattr(boundary, "geom_type", None),
        "boundary_area_km2": area_km2(boundary),
        "aoi_area_km2": area_km2(aoi),
        "aoi_bounds": list(map(float, aoi.bounds)),
        "buffer_m": args.buffer_m,
        "aoi": mapping(aoi),
        "status": "failed",
    }
    try:
        graph = ox.graph.graph_from_polygon(
            aoi,
            network_type="walk",
            retain_all=True,
            simplify=True,
        )
        graph.graph["municipality"] = municipality
        graph.graph["municipality_code"] = municipality_code
        graph.graph["network_type"] = "walk"
        graph.graph["source_attribution"] = "© OpenStreetMap contributors; ODbL"
        graph.graph["aoi_method"] = result["aoi_method"]
        graph.graph["aoi_buffer_m"] = args.buffer_m
        graph.graph["aoi_projected_crs"] = PROJECTED_CRS
        ox.io.save_graphml(graph, filepath=graph_path)

        component_sizes = sorted((len(c) for c in nx.weakly_connected_components(graph)), reverse=True)
        largest = component_sizes[0] if component_sizes else 0
        result.update(
            {
                "status": "complete",
                "nodes": graph.number_of_nodes(),
                "edges": graph.number_of_edges(),
                "weakly_connected_components": len(component_sizes),
                "largest_component_nodes": largest,
                "largest_component_ratio": (
                    largest / graph.number_of_nodes() if graph.number_of_nodes() else None
                ),
                "component_sizes_top10": component_sizes[:10],
                "graphml": str(graph_path),
            }
        )
    except Exception as exc:  # noqa: BLE001 - preserve the failure and continue.
        result.update({"error_type": type(exc).__name__, "error": str(exc)})

    args.qa_dir.mkdir(parents=True, exist_ok=True)
    qa_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def write_summary(qa_dir: pathlib.Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for qa_path in sorted(qa_dir.glob("*_network.json")):
        records.append(json.loads(qa_path.read_text(encoding="utf-8")))
    (qa_dir / "walking_network_summary.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return records


def summary_metadata(records: list[dict[str, object]]) -> dict[str, object]:
    return {
        "qa_records": len(records),
        "aoi_method": "municipality_multipolygon_metric_buffer",
        "projected_crs": PROJECTED_CRS,
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
    parser.add_argument("--boundary-zip", type=pathlib.Path)
    parser.add_argument("--out-dir", type=pathlib.Path)
    parser.add_argument("--qa-dir", type=pathlib.Path, required=True)
    parser.add_argument("--municipality")
    parser.add_argument("--buffer-m", type=float, default=DEFAULT_BUFFER_M)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--summarize-only", action="store_true")
    args = parser.parse_args()

    args.qa_dir.mkdir(parents=True, exist_ok=True)
    if args.summarize_only:
        print(json.dumps(summary_metadata(write_summary(args.qa_dir)), ensure_ascii=False, indent=2))
        return
    if args.mesh_csv is None or args.boundary_zip is None or args.out_dir is None:
        parser.error("--mesh-csv, --boundary-zip and --out-dir are required unless --summarize-only is used")

    import pandas as pd

    args.out_dir.mkdir(parents=True, exist_ok=True)
    mesh = pd.read_csv(args.mesh_csv, dtype={"municipality_code": str})
    municipality_rows = (
        mesh[["municipality", "municipality_code"]]
        .drop_duplicates()
        .sort_values("municipality_code")
    )
    code_by_name = dict(municipality_rows.itertuples(index=False, name=None))
    boundaries = load_boundaries(args.boundary_zip)

    names = [args.municipality] if args.municipality else sorted(code_by_name)
    unknown = [name for name in names if name not in code_by_name]
    if unknown:
        raise SystemExit(f"unknown municipality: {', '.join(unknown)}")

    results = []
    for name in names:
        code = str(code_by_name[name])
        boundary = boundaries.get(code)
        if boundary is None:
            raise SystemExit(f"N03 boundary missing for {name} ({code})")
        print(f"starting {name} ({code})", flush=True)
        result = build_one(name, code, boundary, args)
        results.append(result)
        print(f"finished {name}: {result.get('status')}", flush=True)

    print(json.dumps(summary_metadata(write_summary(args.qa_dir)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
