"""Pure-Python JGD2011 500m mesh geometry helpers."""

from __future__ import annotations


def mesh_polygon(mesh_id: str) -> list[tuple[float, float]]:
    """Return a 500m mesh ring as (lon, lat), using the standard mesh code."""
    code = str(mesh_id).strip()
    if len(code) != 9 or not code.isdigit():
        raise ValueError(f"not a 500m mesh code: {mesh_id!r}")
    p, q = int(code[0:2]), int(code[2:4])
    r, s = int(code[4]), int(code[5])
    t, u = int(code[6]), int(code[7])
    quadrant = int(code[8])
    if quadrant not in (1, 2, 3, 4):
        raise ValueError(f"invalid 500m mesh quadrant: {mesh_id!r}")
    base_lat = p / 1.5 + r / 12 + t / 120
    base_lon = 100 + q + s / 8 + u / 80
    one_km_dlat = 1 / 120
    one_km_dlon = 1 / 80
    north = quadrant in (2, 4)
    east = quadrant in (3, 4)
    lat0 = base_lat + (one_km_dlat / 2 if north else 0)
    lon0 = base_lon + (one_km_dlon / 2 if east else 0)
    dlat = one_km_dlat / 2
    dlon = one_km_dlon / 2
    return [(lon0, lat0), (lon0 + dlon, lat0), (lon0 + dlon, lat0 + dlat), (lon0, lat0 + dlat), (lon0, lat0)]


def mesh_centroid(mesh_id: str) -> tuple[float, float]:
    ring = mesh_polygon(mesh_id)
    return (sum(x for x, _ in ring[:-1]) / 4, sum(y for _, y in ring[:-1]) / 4)
