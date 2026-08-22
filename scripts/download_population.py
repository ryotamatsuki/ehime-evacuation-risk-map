#!/usr/bin/env python3
"""Download 2020 census 500m mesh tables and Ehime municipal boundaries."""

from __future__ import annotations

import argparse
import pathlib
import urllib.request


ESTAT_CODES = ["4932", "5032", "5033", "5132", "5133"]
ESTAT_TABLES = {
    "population_households": "T001141",
    "age_5year": "T001192",
}
ESTAT_URL = "https://www.e-stat.go.jp/gis/statmap-search/data?statsId={stats_id}&code={code}&downloadType=2"
BOUNDARY_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/N03/N03-2024/N03-20240101_38_GML.zip"


def download(url: str, path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "ehime-evacuation-risk-map/0.1"})
    with urllib.request.urlopen(request, timeout=180) as response, path.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            out.write(chunk)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("data/raw"))
    args = parser.parse_args()
    for table_name, stats_id in ESTAT_TABLES.items():
        for code in ESTAT_CODES:
            download(ESTAT_URL.format(stats_id=stats_id, code=code), args.out / f"{table_name}_{code}.zip")
    download(BOUNDARY_URL, args.out / "N03-20240101_38_GML.zip")
    (args.out / "population_source_urls.txt").write_text(
        "e-Stat 2020 500m JGD2011 population and households: T001141\n"
        "e-Stat 2020 500m JGD2011 5-year age population: T001192\n"
        "e-Stat codes: " + ", ".join(ESTAT_CODES) + "\n"
        "MLIT N03 2024 Ehime municipal boundaries: " + BOUNDARY_URL + "\n",
        encoding="utf-8",
    )
    print(f"downloaded population and boundary sources to {args.out}")


if __name__ == "__main__":
    main()
