from __future__ import annotations
import networkx as nx
import pytest
from calculate_step8_candidates import candidate_choices_for_origin, multisource_k_shelter_dijkstra

def test_k_label_dijkstra_returns_two_nearest_shelters_deterministically():
    graph=nx.Graph(); graph.add_edge("o","x",length=100.0); graph.add_edge("x","a",length=100.0); graph.add_edge("x","b",length=200.0); graph.add_edge("x","c",length=300.0)
    candidates=[
        {"shelter_key":"1||A","common_id":"1","name":"A","shelter_node":"a","shelter_connector_distance_m":10.0,"shelter_municipality_code":"1","cross_border":False},
        {"shelter_key":"2||B","common_id":"2","name":"B","shelter_node":"b","shelter_connector_distance_m":10.0,"shelter_municipality_code":"2","cross_border":True},
        {"shelter_key":"3||C","common_id":"3","name":"C","shelter_node":"c","shelter_connector_distance_m":10.0,"shelter_municipality_code":"3","cross_border":True},
    ]
    labels=multisource_k_shelter_dijkstra(graph,candidates,2)
    assert set(labels["o"])=={"1||A","2||B"}; assert labels["o"]["1||A"]==pytest.approx(210.0); assert labels["o"]["2||B"]==pytest.approx(310.0)

def test_origin_access_is_included_and_candidates_are_ranked():
    graph=nx.Graph(); graph.add_edge("o","a",length=100.0); graph.add_edge("o","b",length=150.0)
    candidates=[
        {"shelter_key":"1||A","common_id":"1","name":"A","shelter_node":"a","shelter_connector_distance_m":10.0,"shelter_municipality_code":"1","cross_border":False},
        {"shelter_key":"2||B","common_id":"2","name":"B","shelter_node":"b","shelter_connector_distance_m":5.0,"shelter_municipality_code":"2","cross_border":True},
    ]
    labels=multisource_k_shelter_dijkstra(graph,candidates,2); origin={"origin_method":"walk_node_within_mesh","representative_origin_node":"o","origin_connector_distance_m":20.0}; by_key={r["shelter_key"]:r for r in candidates}; rows=candidate_choices_for_origin(origin,labels,by_key,2)
    assert [r["shelter_key"] for r in rows]==["1||A","2||B"]
    assert rows[0]["total_walking_distance_m"]==pytest.approx(130.0); assert rows[0]["origin_access_distance_m"]==pytest.approx(20.0); assert rows[0]["network_path_distance_m"]==pytest.approx(100.0); assert rows[0]["shelter_connector_distance_m"]==pytest.approx(10.0)
