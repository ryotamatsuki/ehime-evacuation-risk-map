from __future__ import annotations

import json
import pathlib
import sys

import pandas as pd
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from build_tsunami_exposure import PALETTE  # noqa: E402
from calculate_route_exposure import (  # noqa: E402
    calculate_route,
    route_coordinates_for_row,
    validate_result,
)
from calculate_route_exposure_step3 import build_modeled_route  # noqa: E402


class FakeTileStore:
    def __init__(self, image, status="present"):
        self.image = image
        self.status = status

    def get(self, _key):
        return self.image, self.status


def test_corrected_step2_geometry_is_preferred():
    row = pd.Series(
        {
            "route_network_coordinates": json.dumps([[132.7, 33.8], [132.701, 33.801]]),
            "route_coordinates": json.dumps([[1, 1], [2, 2]]),
        }
    )
    assert route_coordinates_for_row(row) == [[132.7, 33.8], [132.701, 33.801]]


def test_zero_network_path_is_expanded_to_modeled_total_route():
    row = pd.Series(
        {
            "mesh_id": "503265471",
            "route_network_coordinates": json.dumps([[132.7160365, 33.8695777]]),
            "selected_shelter_common_id": "S1",
        }
    )
    points = build_modeled_route(row, {"S1": (33.8700, 132.7170)})
    assert len(points) == 3
    assert points[1] == [132.7160365, 33.8695777]
    assert points[-1] == [132.7170, 33.8700]


def test_inundated_route_returns_full_exposure_and_segments():
    color, depth = next(iter(PALETTE.items()))
    image = Image.new("RGBA", (256, 256), (*color, 255))
    result = calculate_route(
        [[132.75, 33.84], [132.7502, 33.84]],
        FakeTileStore(image),
        sample_spacing_m=5.0,
    )
    assert result["route_exposure_status"] == "complete"
    assert result["route_inundation_ratio"] == 1.0
    assert result["route_inundation_ratio_classified"] == 1.0
    assert result["route_unknown_ratio"] == 0.0
    assert result["route_max_depth_class"] == depth
    assert result["route_inundated_segment_count"] >= 1
    assert json.loads(result["route_inundated_segments"])


def test_absent_tile_is_recorded_as_unknown_not_dry():
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    result = calculate_route(
        [[132.75, 33.84], [132.7502, 33.84]],
        FakeTileStore(image, "absent"),
        sample_spacing_m=5.0,
    )
    assert result["route_inundation_ratio"] == 0.0
    assert result["route_unknown_ratio"] == 1.0
    assert result["route_classified_coverage_ratio"] == 0.0
    assert result["route_tile_absent_sample_count"] == result["route_tsunami_sample_count"]
    assert result["route_inundated_segment_count"] == 0


def test_transparent_pixel_in_present_tile_is_known_non_inundated():
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    result = calculate_route(
        [[132.75, 33.84], [132.7502, 33.84]],
        FakeTileStore(image, "present"),
        sample_spacing_m=5.0,
    )
    assert result["route_inundation_ratio"] == 0.0
    assert result["route_unknown_ratio"] == 0.0
    assert result["route_classified_coverage_ratio"] == 1.0


def test_release_gate_preserves_unavailable_routes():
    result = pd.DataFrame(
        [
            {
                "mesh_id": "a",
                "route_status": "complete",
                "route_exposure_status": "complete",
                "route_network_geometry_distance_m": 100.0,
                "route_inundation_distance_m": 25.0,
                "route_inundation_ratio": 0.25,
                "route_tsunami_sample_count": 4,
                "route_unknown_ratio": 0.0,
                "route_classified_coverage_ratio": 1.0,
                "route_tile_absent_sample_count": 0,
                "route_unclassified_sample_count": 0,
            },
            {
                "mesh_id": "b",
                "route_status": "no_network_path",
                "route_exposure_status": "route_unavailable",
                "route_network_geometry_distance_m": None,
                "route_inundation_distance_m": None,
                "route_inundation_ratio": None,
                "route_tsunami_sample_count": 0,
                "route_unknown_ratio": None,
                "route_classified_coverage_ratio": None,
                "route_tile_absent_sample_count": 0,
                "route_unclassified_sample_count": 0,
            },
        ]
    )
    failures, qa = validate_result(result, expected_rows=2, expected_complete_routes=1)
    assert failures == []
    assert qa["release_gate"]["pass"] is True
    assert qa["unavailable_routes"] == 1
