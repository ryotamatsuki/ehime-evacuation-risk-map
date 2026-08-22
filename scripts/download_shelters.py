#!/usr/bin/env python3
"""Download the fixed shelter sources used by the P0 analysis.

Raw source files are deliberately kept outside the repository by default.  The
repository stores only reproducible code, metadata and derived web data.
"""

from __future__ import annotations

import argparse
import pathlib
import urllib.request


PREF_XLSX_URL = (
    "https://www.pref.ehime.jp/opendata-catalog/fs/1/7/1/5/7/_/"
    "______________0727.xlsx"
)
GSI_EMERGENCY_CSV_URL = (
    "https://hinanmap.gsi.go.jp/hinanjocp/defaultFtpData/csv/38000_2.csv"
)
GSI_SOURCE_PAGE = "https://hinanmap.gsi.go.jp/hinanjocp/hinanbasho/koukaidate.html"
PREF_SOURCE_PAGE = "https://www.pref.ehime.jp/opendata-catalog/dataset/pref-2.html"


def download(url: str, path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "ehime-evacuation-risk-map/0.1"})
    with urllib.request.urlopen(request, timeout=120) as response, path.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            out.write(chunk)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=pathlib.Path, default=pathlib.Path("data/raw"))
    args = parser.parse_args()
    download(PREF_XLSX_URL, args.out / "ehime_shelters_20260727.xlsx")
    download(GSI_EMERGENCY_CSV_URL, args.out / "gsi_shelters_38000_2.csv")
    (args.out / "source_urls.txt").write_text(
        "愛媛県属性正本: " + PREF_SOURCE_PAGE + "\n"
        "国土地理院座標: " + GSI_SOURCE_PAGE + "\n"
        "県XLSX URL: " + PREF_XLSX_URL + "\n"
        "GSI CSV URL: " + GSI_EMERGENCY_CSV_URL + "\n",
        encoding="utf-8",
    )
    print(f"downloaded shelter sources to {args.out}")


if __name__ == "__main__":
    main()
