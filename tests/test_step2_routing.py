from __future__ import annotations

import pathlib
import sys

import networkx as nx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from calculate_evacuation_routes_v2 import (  # noqa: E402
    choose_origin_seed,
    dedupe_candidates_by_node,
    municipality_code_from_common_id,
    multisource_shelter_dijkstra,
    reconstruct_node_path,
)


def test_municipality_code_from_common_id() -> None:
    assert municipality_code_from_common_id("E3821400137201") == "38214"
    assert municipality_code_from_common_id("not-standard") is None


def test_same_network_node_keeps_shortest_connector_then_common_id() -> None:
    candidates = [
        {"common_id": "E3820100002", "shelter_node": "n", "shelter_connector_distance_m": 25.0},
        {"common_id": "E3820100001", "shelter_node": "n", "shelter_connector_distance_m": 25.0},
        {"common_id": "E3820100003", "shelter_node": "n", "shelter_connector_distance_m": 20.0},
        {"common_id": "E3820100004", "shelter_node": "m", "shelter_connector_distance_m": 30.0},
    ]
    deduped, duplicate_count = dedupe_candidates_by_node(candidates)
    by_node = {item["shelter_node"]: item for item in deduped}
    assert duplicate_count == 2
    assert by_node["n"]["common_id"] == "E3820100003"
    assert by_node["m"]["common_id"] == "E3820100004"


def test_multisource_dijkstra_includes_shelter_connector() -> None:
    graph = nx.Graph()
    graph.add_edge("a", "b", length=100.0)
    graph.add_edge("b", "c", length=100.0)
    candidates = [
        {"common_id": "S1", "shelter_node": "a", "shelter_connector_distance_m": 50.0},
        {"common_id": "S2", "shelter_node": "c", "shelter_connector_distance_m": 10.0},
    ]
    distances, owners, predecessors = multisource_shelter_dijkstra(graph, candidates)
    assert distances["b"] == 110.0
    assert owners["b"] == "S2"
    assert reconstruct_node_path("b", "S2", owners, predecessors) == ["b", "c"]


def test_node_origin_total_cost_accounts_for_origin_and_shelter_connectors() -> None:
    graph = nx.Graph()
    graph.add_edge("origin", "shelter", length=300.0)
    candidates = [
        {"common_id": "S", "shelter_node": "shelter", "shelter_connector_distance_m": 40.0}
    ]
    distances, owners, _ = multisource_shelter_dijkstra(graph, candidates)
    origin = {
        "origin_method": "walk_node_within_mesh",
        "representative_origin_node": "origin",
        "origin_connector_distance_m": 25.0,
    }
    choice = choose_origin_seed(origin, distances, owners)
    assert choice is not None
    node, origin_access, owner, total = choice
    assert node == "origin"
    assert owner == "S"
    assert origin_access == 25.0
    assert total == 365.0


def test_edge_origin_chooses_cheaper_endpoint_including_along_edge_cost() -> None:
    distances = {"u": 500.0, "v": 100.0}
    owners = {"u": "S1", "v": "S2"}
    origin = {
        "origin_method": "walk_edge_intersects_mesh",
        "origin_connector_distance_m": 10.0,
        "origin_edge_u": "u",
        "origin_edge_v": "v",
        "origin_edge_u_along_m": 20.0,
        "origin_edge_v_along_m": 70.0,
    }
    choice = choose_origin_seed(origin, distances, owners)
    assert choice == ("v", 80.0, "S2", 180.0)


def test_network_coverage_gap_has_no_origin_seed() -> None:
    origin = {
        "origin_method": "network_coverage_gap",
        "origin_connector_distance_m": 700.0,
    }
    assert choose_origin_seed(origin, {"x": 10.0}, {"x": "S"}) is None
