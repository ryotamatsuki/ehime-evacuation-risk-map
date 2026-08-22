from __future__ import annotations

import json
import pathlib
import sys
import zipfile

import networkx as nx
import osmnx as ox
from pyproj import Transformer
from shapely.geometry import LineString, MultiPolygon, Polygon

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from build_walking_network import PROJECTED_CRS, buffered_aoi, load_boundaries  # noqa: E402
from mesh500 import mesh_centroid, mesh_polygon  # noqa: E402
from routing_foundation_qa import as_walk_graph, snap_status  # noqa: E402
from select_mesh_origins import choose_origin, metric_edge_index, metric_node_points  # noqa: E402


def test_snap_status_thresholds() -> None:
    assert snap_status(0) == "normal"
    assert snap_status(100) == "normal"
    assert snap_status(100.1) == "review"
    assert snap_status(250) == "review"
    assert snap_status(250.1) == "warning"
    assert snap_status(500) == "warning"
    assert snap_status(500.1) == "critical"
    assert snap_status(None) == "missing"


def test_metric_buffer_expands_boundary() -> None:
    polygon = Polygon([(132.7, 33.8), (132.71, 33.8), (132.71, 33.81), (132.7, 33.81)])
    expanded = buffered_aoi(polygon, 3000)
    assert expanded.area > polygon.area
    assert PROJECTED_CRS == "EPSG:32653"


def test_load_boundaries_preserves_disconnected_parts(tmp_path: pathlib.Path) -> None:
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"N03_007": "38356"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[133.2, 34.2], [133.21, 34.2], [133.21, 34.21], [133.2, 34.21], [133.2, 34.2]]],
                },
            },
            {
                "type": "Feature",
                "properties": {"N03_007": "38356"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[133.3, 34.2], [133.31, 34.2], [133.31, 34.21], [133.3, 34.21], [133.3, 34.2]]],
                },
            },
        ],
    }
    archive_path = tmp_path / "boundary.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("N03.geojson", json.dumps(payload))

    boundary = load_boundaries(archive_path)["38356"]
    assert isinstance(boundary, MultiPolygon)
    assert len(boundary.geoms) == 2


def _crossing_graph(mesh_id: str) -> nx.MultiDiGraph:
    ring = mesh_polygon(mesh_id)
    lon_min = min(x for x, _ in ring)
    lon_max = max(x for x, _ in ring)
    lat = mesh_centroid(mesh_id)[1]
    margin = (lon_max - lon_min) * 0.5
    graph = nx.MultiDiGraph()
    graph.graph["crs"] = "EPSG:4326"
    graph.add_node(1, x=lon_min - margin, y=lat)
    graph.add_node(2, x=lon_max + margin, y=lat)
    graph.add_edge(
        1,
        2,
        length=1500.0,
        geometry=LineString([(lon_min - margin, lat), (lon_max + margin, lat)]),
    )
    return graph


def _far_graph(mesh_id: str) -> nx.MultiDiGraph:
    lon, lat = mesh_centroid(mesh_id)
    far_lon = lon + 0.03
    graph = nx.MultiDiGraph()
    graph.graph["crs"] = "EPSG:4326"
    graph.add_node(1, x=far_lon, y=lat)
    graph.add_node(2, x=far_lon + 0.005, y=lat)
    graph.add_edge(1, 2, length=500.0, geometry=LineString([(far_lon, lat), (far_lon + 0.005, lat)]))
    return graph


def _resolve_origin(graph: nx.MultiDiGraph, mesh_id: str):
    walk_graph = as_walk_graph(graph)
    transformer = Transformer.from_crs("EPSG:4326", PROJECTED_CRS, always_xy=True)
    node_ids, node_points, node_tree = metric_node_points(walk_graph, transformer)
    edge_records, edge_tree = metric_edge_index(graph, transformer)
    lon, lat = mesh_centroid(mesh_id)
    raw_node, raw_distance = ox.distance.nearest_nodes(walk_graph, X=lon, Y=lat, return_dist=True)
    return choose_origin(
        mesh_id,
        raw_node,
        float(raw_distance),
        node_ids,
        node_points,
        node_tree,
        edge_records,
        edge_tree,
        graph,
        transformer,
    )


def test_edge_crossing_mesh_is_not_false_network_gap() -> None:
    result = _resolve_origin(_crossing_graph("493252842"), "493252842")
    assert result["origin_method"] == "walk_edge_intersects_mesh"
    assert result["origin_edge_u"] is not None
    assert result["origin_edge_v"] is not None
    assert result["origin_connector_distance_m"] < 1.0


def test_true_far_network_is_explicit_gap() -> None:
    result = _resolve_origin(_far_graph("493262112"), "493262112")
    assert result["origin_method"] == "network_coverage_gap"
    assert result["origin_status"] == "network_coverage_gap"
    assert result["representative_origin_node"] is None
    assert result["origin_connector_distance_m"] > 500.0
