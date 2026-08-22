from __future__ import annotations

import json
import pathlib
import sys
import zipfile

from shapely.geometry import MultiPolygon, Polygon

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from build_walking_network import PROJECTED_CRS, buffered_aoi, load_boundaries  # noqa: E402
from routing_foundation_qa import snap_status  # noqa: E402


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
