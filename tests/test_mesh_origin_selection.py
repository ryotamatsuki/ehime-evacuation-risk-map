from __future__ import annotations

import networkx as nx
from pyproj import Transformer
from shapely.geometry import LineString

from scripts.mesh500 import mesh_centroid, mesh_polygon
from scripts.routing_foundation_qa import as_walk_graph
from scripts.select_mesh_origins import (
    choose_origin,
    metric_edge_index,
    metric_node_points,
)


def make_crossing_graph(mesh_id: str) -> nx.MultiDiGraph:
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


def make_far_graph(mesh_id: str) -> nx.MultiDiGraph:
    lon, lat = mesh_centroid(mesh_id)
    # Roughly >1 km away at Ehime latitude.
    far_lon = lon + 0.03
    graph = nx.MultiDiGraph()
    graph.graph["crs"] = "EPSG:4326"
    graph.add_node(1, x=far_lon, y=lat)
    graph.add_node(2, x=far_lon + 0.005, y=lat)
    graph.add_edge(1, 2, length=500.0, geometry=LineString([(far_lon, lat), (far_lon + 0.005, lat)]))
    return graph


def resolve(graph: nx.MultiDiGraph, mesh_id: str):
    walk_graph = as_walk_graph(graph)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:32653", always_xy=True)
    node_ids, node_points, node_tree = metric_node_points(walk_graph, transformer)
    edge_records, edge_tree = metric_edge_index(graph, transformer)
    lon, lat = mesh_centroid(mesh_id)
    # The raw nearest-node fields are diagnostics only for these tests.
    import osmnx as ox
    raw_node, raw_distance = ox.distance.nearest_nodes(
        walk_graph, X=lon, Y=lat, return_dist=True
    )
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


def test_edge_crossing_mesh_is_not_false_network_gap():
    mesh_id = "493252842"
    result = resolve(make_crossing_graph(mesh_id), mesh_id)
    assert result["origin_method"] == "walk_edge_intersects_mesh"
    assert result["origin_edge_u"] is not None
    assert result["origin_edge_v"] is not None
    assert result["origin_connector_distance_m"] < 1.0


def test_true_far_network_is_explicit_gap():
    mesh_id = "493262112"
    result = resolve(make_far_graph(mesh_id), mesh_id)
    assert result["origin_method"] == "network_coverage_gap"
    assert result["origin_status"] == "network_coverage_gap"
    assert result["representative_origin_node"] is None
    assert result["origin_connector_distance_m"] > 500.0
