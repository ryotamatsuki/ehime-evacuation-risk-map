from __future__ import annotations
import networkx as nx
import pandas as pd
import pytest
from calculate_step8_candidates import candidate_choices_for_origin, multisource_k_shelter_dijkstra
from aggregate_step8_candidates import aggregate_step8_candidates

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


def test_double_parity_anchors_only_rank1_final_export_metadata(monkeypatch):
    candidates = pd.DataFrame([
        {"mesh_id":"1","candidate_rank":1,"shelter_key":"A||Alpha","shelter_common_id":"A","shelter_name":"Alpha","shelter_municipality_code":"100","cross_border":False,"total_walking_distance_m":100.0},
        {"mesh_id":"1","candidate_rank":2,"shelter_key":"B||Beta","shelter_common_id":"B","shelter_name":"Beta","shelter_municipality_code":"200","cross_border":True,"total_walking_distance_m":150.0},
    ])
    status = pd.DataFrame([
        {"mesh_id":"1","candidate_status":"complete","candidate_count":2},
        {"mesh_id":"2","candidate_status":"no_network_path","candidate_count":0},
    ])
    baseline = pd.DataFrame([
        {"mesh_id":"1","route_status":"complete","selected_shelter_common_id":"A","selected_shelter_name":"Alpha","total_walking_distance_m":100.0,"cross_border":False},
        {"mesh_id":"2","route_status":"no_network_path","selected_shelter_common_id":None,"selected_shelter_name":None,"total_walking_distance_m":None,"cross_border":False},
    ])
    canonical = pd.DataFrame([
        {"mesh_id":"1","route_status":"complete","selected_shelter_common_id":"A","selected_shelter_name":"Alpha","shelter_municipality_code":"200","cross_border":True,"total_walking_distance_m":100.0},
        {"mesh_id":"2","route_status":"no_network_path","selected_shelter_common_id":None,"selected_shelter_name":None,"shelter_municipality_code":None,"cross_border":False,"total_walking_distance_m":None},
    ])
    metadata = {"analysis_version":"analysis-core-v4-corrected-public","target_meshes":2,"complete_routes":1,"route_unavailable":1,"cross_border_routes":1,"cross_border_metadata_corrections_by_shelter_address":1,"analysis_source_sha":"abc","source_workflow_run_id":"42"}
    monkeypatch.setattr("aggregate_step8_candidates.EXPECTED_CANONICAL_CROSS_BORDER", 1)
    out, _, qa, failures = aggregate_step8_candidates(candidates,status,baseline,canonical,metadata,expected_rows=2,expected_complete=1,candidate_limit=2)
    assert failures == []
    rank1=out[out["candidate_rank"].eq(1)].iloc[0]; rank2=out[out["candidate_rank"].eq(2)].iloc[0]
    assert bool(rank1["cross_border"]) is True and rank1["shelter_municipality_code"]=="200"
    assert bool(rank2["cross_border"]) is True and rank2["shelter_municipality_code"]=="200"
    assert qa["same_graph_rank1_identity_matches"]==1
    assert qa["canonical_rank1_identity_matches"]==1
    assert qa["canonical_cross_border_metadata_corrections"]==1
    assert qa["canonical_anchor_applied"] is True
