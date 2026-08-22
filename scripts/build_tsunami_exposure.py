#!/usr/bin/env python3
"""Estimate 500m mesh tsunami exposure from the official GSI raster tiles.

The tiles are sampled at z=12 for the offline mesh statistic.  The official
tile is still used directly by the web map at display zooms.  No source tiles
are copied into GitHub.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import io
import pathlib
import urllib.error
import urllib.request

import pandas as pd
from PIL import Image

from mesh500 import mesh_polygon


TILE_TEMPLATE = "https://disaportaldata.gsi.go.jp/raster/04_tsunami_newlegend_pref_data/38/{z}/{x}/{y}.png"
Z = 12
PALETTE = {
    (220, 122, 220): 8,
    (242, 133, 201): 7,
    (255, 145, 145): 6,
    (255, 183, 183): 5,
    (255, 216, 192): 4,
    (248, 225, 166): 3,
    (247, 245, 169): 2,
    (255, 255, 179): 1,
}


def tile_xy(lon: float, lat: float, z: int = Z) -> tuple[int, int, int, int]:
    import math
    n = 2**z
    xf = (lon + 180.0) / 360.0 * n
    yf = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return int(xf), int(yf), int((xf % 1) * 256), int((yf % 1) * 256)


def sample_points(mesh_id: str) -> list[tuple[float, float]]:
    ring = mesh_polygon(mesh_id)
    west, south = ring[0]
    east, north = ring[2]
    # 13x13 regular samples are approximately 40m apart at Ehime latitudes.
    points = []
    for j in range(13):
        lat = south + (north - south) * (j + 0.5) / 13
        for i in range(13):
            lon = west + (east - west) * (i + 0.5) / 13
            points.append((lon, lat))
    return points


def fetch_tile(key: tuple[int, int], cache: pathlib.Path) -> Image.Image:
    x, y = key
    path = cache / f"{Z}_{x}_{y}.png"
    missing = cache / f"{Z}_{x}_{y}.missing"
    if path.exists():
        return Image.open(path).convert("RGBA")
    if missing.exists():
        return Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    url = TILE_TEMPLATE.format(z=Z, x=x, y=y)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "ehime-evacuation-risk-map/0.1"})
        data = urllib.request.urlopen(request, timeout=30).read()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            cache.mkdir(parents=True, exist_ok=True)
            missing.touch()
            return Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        raise


def nearest_depth(rgb: tuple[int, int, int]) -> int | None:
    if rgb in PALETTE:
        return PALETTE[rgb]
    distances = [(sum((rgb[i] - color[i]) ** 2 for i in range(3)), value) for color, value in PALETTE.items()]
    distance, value = min(distances)
    return value if distance <= 900 else None


def calculate(mesh_id: str, tiles: dict[tuple[int, int], Image.Image]) -> dict[str, object]:
    inundated = 0
    depths: list[int] = []
    for lon, lat in sample_points(mesh_id):
        x, y, px, py = tile_xy(lon, lat)
        rgba = tiles[(x, y)].getpixel((px, py))
        if rgba[3] < 128:
            continue
        depth = nearest_depth(rgba[:3])
        if depth is not None:
            inundated += 1
            depths.append(depth)
    total = len(sample_points(mesh_id))
    ratio = inundated / total if total else None
    return {
        "mesh_id": mesh_id,
        "tsunami_inundation_ratio": ratio,
        "tsunami_max_depth_class": max(depths) if depths else None,
        "tsunami_exposure_score": ratio * 100 if ratio is not None else None,
        "tsunami_sample_count": total,
        "tsunami_inundated_sample_count": inundated,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mesh-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--cache", type=pathlib.Path, required=True)
    args = parser.parse_args()

    meshes = pd.read_csv(args.mesh_csv, encoding="utf-8-sig", dtype={"mesh_id": str})
    keys: set[tuple[int, int]] = set()
    for mesh_id in meshes["mesh_id"]:
        for lon, lat in sample_points(mesh_id):
            x, y, _, _ = tile_xy(lon, lat)
            keys.add((x, y))
    print(f"unique z={Z} tiles: {len(keys)}", flush=True)
    tiles: dict[tuple[int, int], Image.Image] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(fetch_tile, key, args.cache): key for key in keys}
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            tiles[futures[future]] = future.result()
            if index % 100 == 0:
                print(f"downloaded {index}/{len(futures)} tiles", flush=True)
    rows = [calculate(mesh_id, tiles) for mesh_id in meshes["mesh_id"]]
    result = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(result.describe(include="all").to_string())


if __name__ == "__main__":
    main()
