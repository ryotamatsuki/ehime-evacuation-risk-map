#!/usr/bin/env python3
"""Join the prefectural shelter attributes to GSI coordinates.

The prefectural workbook is the attribute authority.  GSI contributes only
coordinates and common IDs.  Unmatched records remain unmatched.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import unicodedata

import pandas as pd


def norm(value: object) -> str:
    if pd.isna(value):
        return ""
    value = unicodedata.normalize("NFKC", str(value))
    return re.sub(r"[\s　,，、。．・()（）「」『』\-‐‑‒–—ー_/／]+", "", value).lower()


def read_prefecture(path: pathlib.Path) -> pd.DataFrame:
    raw = pd.read_excel(path, header=None, sheet_name=0)
    rows = raw.iloc[4:].copy()
    # Keep the facility whose common ID is blank; it must be reported as an
    # unresolved attribute row rather than silently dropped.
    rows = rows[rows.iloc[:, 1].notna()].copy()
    rows.columns = [
        "common_id", "name", "address_pref", "address_city", "address_detail", "contact",
        "flood", "landslide", "storm_surge", "earthquake", "tsunami", "large_fire",
        "inland_flood", "volcano", "shelter_overlap", "capacity", "capacity_basis",
        "notes", "changed", "new", "extra",
    ]
    rows["address"] = rows[["address_pref", "address_city", "address_detail"]].fillna("").astype(str).agg("".join, axis=1)
    rows["common_id"] = rows["common_id"].astype(str).str.strip()
    return rows.drop(columns=["extra"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pref", type=pathlib.Path, required=True)
    parser.add_argument("--gsi", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--report", type=pathlib.Path, required=True)
    args = parser.parse_args()

    pref = read_prefecture(args.pref)
    gsi = pd.read_csv(args.gsi, encoding="utf-8-sig", dtype={"共通ID": str})
    gsi = gsi.rename(columns={"共通ID": "gsi_common_id", "施設・場所名": "gsi_name", "住所": "gsi_address", "緯度": "latitude", "経度": "longitude"})
    gsi["gsi_common_id"] = gsi["gsi_common_id"].fillna("").astype(str).str.strip()

    by_id = gsi[gsi["gsi_common_id"].ne("")].drop_duplicates("gsi_common_id", keep=False).set_index("gsi_common_id", drop=False)
    pref["name_address_key"] = pref["name"].map(norm) + "|" + pref["address"].map(norm)
    gsi["name_address_key"] = gsi["gsi_name"].map(norm) + "|" + gsi["gsi_address"].map(norm)
    by_key = gsi[gsi["name_address_key"].ne("")].drop_duplicates("name_address_key", keep=False).set_index("name_address_key", drop=False)

    method = []
    latitudes = []
    longitudes = []
    gsi_ids = []
    for row in pref.itertuples(index=False):
        match = by_id.loc[row.common_id] if row.common_id in by_id.index else None
        how = "matched_by_id" if match is not None else "unmatched"
        if match is None and row.name_address_key in by_key.index:
            match = by_key.loc[row.name_address_key]
            how = "matched_by_name_address"
        method.append(how)
        if match is None:
            gsi_ids.append("")
            latitudes.append(float("nan"))
            longitudes.append(float("nan"))
        else:
            gsi_ids.append(match["gsi_common_id"])
            latitudes.append(match["latitude"])
            longitudes.append(match["longitude"])

    pref["gsi_common_id"] = gsi_ids
    pref["latitude"] = latitudes
    pref["longitude"] = longitudes
    pref["coordinate_join_method"] = method
    pref = pref.drop(columns=["name_address_key"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pref.to_csv(args.out, index=False, encoding="utf-8-sig")

    duplicate_ids = gsi.loc[gsi["gsi_common_id"].ne(""), "gsi_common_id"]
    duplicate_count = int(duplicate_ids.duplicated(keep=False).sum())
    report = pd.DataFrame([
        {"metric": "total", "value": len(pref), "status": "computed", "notes": "prefectural workbook rows"},
        {"metric": "matched_by_id", "value": method.count("matched_by_id"), "status": "computed", "notes": "common_id"},
        {"metric": "matched_by_name_address", "value": method.count("matched_by_name_address"), "status": "computed", "notes": "normalized name + address fallback"},
        {"metric": "unmatched", "value": method.count("unmatched"), "status": "computed", "notes": "no coordinate imputation"},
        {"metric": "duplicate", "value": duplicate_count, "status": "computed", "notes": "GSI rows whose nonblank common_id occurs more than once"},
    ])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(args.report, index=False, encoding="utf-8-sig")
    print(report.to_string(index=False))


if __name__ == "__main__":
    main()
