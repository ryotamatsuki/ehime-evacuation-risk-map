from __future__ import annotations

import json

import pandas as pd

from scripts.export_corrected_public_data import (
    build_shelter_resolver,
    jsonable,
    prepare_mesh_for_public,
    resolve_shelter_municipality,
)


def test_resolve_short_common_id_from_official_address_city() -> None:
    source = pd.DataFrame(
        [
            {
                "common_id": "03201",
                "name": "ウェルピア伊予",
                "address_city": "伊予市",
                "address": "愛媛県伊予市下三谷1761-1",
            }
        ]
    )
    by_pair, by_id = build_shelter_resolver(source)
    code, resolution = resolve_shelter_municipality(
        "03201", "ウェルピア伊予", None, by_pair, by_id
    )
    assert code == "38210"
    assert resolution == "shelter_source_common_id_name"


def test_existing_route_municipality_code_has_priority() -> None:
    code, resolution = resolve_shelter_municipality(
        "E3850600001201", "test", "38506", {}, {}
    )
    assert code == "38506"
    assert resolution == "route_output"


def test_prepare_mesh_reclassifies_matsumae_to_iyo_as_cross_border() -> None:
    source = pd.DataFrame(
        [
            {
                "common_id": "03201",
                "name": "ウェルピア伊予",
                "address_city": "伊予市",
                "address": "愛媛県伊予市",
            }
        ]
    )
    by_pair, by_id = build_shelter_resolver(source)
    mesh = pd.DataFrame(
        [
            {
                "mesh_id": "503255272",
                "municipality_code": "38401",
                "route_status": "complete",
                "selected_shelter_common_id": "03201",
                "selected_shelter_name": "ウェルピア伊予",
                "shelter_municipality_code": None,
                "cross_border": None,
            },
            {
                "mesh_id": "503255273",
                "municipality_code": "38210",
                "route_status": "complete",
                "selected_shelter_common_id": "03201",
                "selected_shelter_name": "ウェルピア伊予",
                "shelter_municipality_code": None,
                "cross_border": None,
            },
        ]
    )
    prepared, correction = prepare_mesh_for_public(mesh, by_pair, by_id)
    assert prepared.loc[0, "shelter_municipality_code"] == "38210"
    assert bool(prepared.loc[0, "cross_border"]) is True
    assert bool(prepared.loc[1, "cross_border"]) is False
    assert correction == {
        "source_cross_border_true": 0,
        "resolved_cross_border_true": 1,
        "resolved_missing_shelter_municipality_rows": 2,
        "additional_cross_border_detected": 1,
    }


def test_route_failure_keeps_cross_border_unknown() -> None:
    mesh = pd.DataFrame(
        [
            {
                "mesh_id": "493253842",
                "municipality_code": "38203",
                "route_status": "network_coverage_gap",
                "selected_shelter_common_id": None,
                "selected_shelter_name": None,
                "shelter_municipality_code": None,
                "cross_border": None,
            }
        ]
    )
    prepared, correction = prepare_mesh_for_public(mesh, {}, {})
    assert pd.isna(prepared.loc[0, "cross_border"])
    assert correction["resolved_cross_border_true"] == 0


def test_jsonable_parses_geometry_segments_and_risk_reasons() -> None:
    coordinates = jsonable("[[132.1,33.9],[132.2,34.0]]", "route_network_coordinates")
    segments = jsonable(
        '[{"depth_class":3,"coordinates":[[132.1,33.9],[132.2,34.0]]}]',
        "route_inundated_segments",
    )
    reasons = jsonable(
        '[{"key":"walking_accessibility","component_score":72.3}]', "risk_reasons"
    )
    assert coordinates == [[132.1, 33.9], [132.2, 34.0]]
    assert segments[0]["depth_class"] == 3
    assert reasons[0]["key"] == "walking_accessibility"


def test_jsonable_never_turns_missing_boolean_into_false() -> None:
    # Cross-border unknown must remain null for route failures; callers only pass
    # concrete booleans for complete routes after municipality resolution.
    assert jsonable(None, "cross_border") is None
