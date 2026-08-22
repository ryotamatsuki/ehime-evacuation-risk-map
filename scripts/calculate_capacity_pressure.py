#!/usr/bin/env python3
"""Calculate the hypothetical nearest-shelter capacity pressure component."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import unicodedata

import pandas as pd


def parse_capacity(value: object) -> tuple[float | None, str]:
    """Parse the official numeric prefix without treating missing as zero."""
    if pd.isna(value):
        return None, "missing"
    text = unicodedata.normalize("NFKC", str(value)).strip()
    match = re.match(r"^([0-9][0-9,]*(?:\.[0-9]+)?)", text)
    if not match:
        return None, "unparseable"
    number = float(match.group(1).replace(",", ""))
    if number <= 0:
        return None, "nonpositive"
    return number, "numeric_prefix" if text != match.group(1) else "numeric"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--population-csv", type=pathlib.Path, required=True)
    parser.add_argument("--tsunami-csv", type=pathlib.Path, required=True)
    parser.add_argument("--route-exposure-csv", type=pathlib.Path, required=True)
    parser.add_argument("--shelters-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-csv", type=pathlib.Path, required=True)
    parser.add_argument("--out-qa", type=pathlib.Path, required=True)
    args = parser.parse_args()

    population = pd.read_csv(args.population_csv, encoding="utf-8-sig", dtype={"mesh_id": str})
    tsunami = pd.read_csv(args.tsunami_csv, encoding="utf-8-sig", dtype={"mesh_id": str})
    routes = pd.read_csv(args.route_exposure_csv, encoding="utf-8-sig", dtype={"mesh_id": str})
    shelters = pd.read_csv(args.shelters_csv, encoding="utf-8-sig", dtype={"common_id": str})
    shelters["tsunami_flag"] = pd.to_numeric(shelters["tsunami"], errors="coerce")
    capacities = shelters.loc[shelters["tsunami_flag"].eq(1), ["common_id", "capacity"]].copy()
    parsed = capacities["capacity"].map(parse_capacity)
    capacities["shelter_capacity"] = parsed.map(lambda item: item[0])
    capacities["capacity_parse_status"] = parsed.map(lambda item: item[1])
    capacities = capacities.drop(columns=["capacity"])
    # A few source rows share a common ID and coordinate. Aggregate only the
    # tsunami-compatible rows; a non-tsunami duplicate must not add capacity.
    capacities["capacity_record_count"] = 1
    capacities["capacity_has_value"] = capacities["shelter_capacity"].notna()
    capacities["capacity_parse_status"] = capacities["capacity_parse_status"].astype(str)
    capacities = capacities.groupby("common_id", as_index=False).agg(
        shelter_capacity=("shelter_capacity", lambda values: values.sum(min_count=1)),
        capacity_record_count=("capacity_record_count", "sum"),
        capacity_has_value=("capacity_has_value", "any"),
        capacity_parse_status=("capacity_parse_status", lambda values: ";".join(sorted(set(values)))),
    )

    result = population.merge(tsunami, on="mesh_id", how="left", validate="one_to_one", suffixes=("", "_tsunami"))
    result = result.merge(routes, on="mesh_id", how="left", validate="one_to_one", suffixes=("", "_route"))
    result = result.merge(
        capacities,
        left_on="nearest_shelter_id",
        right_on="common_id",
        how="left",
        validate="many_to_one",
    ).drop(columns=["common_id"], errors="ignore")
    result["total_population"] = pd.to_numeric(result["total_population"], errors="coerce")
    result["network_distance_m"] = pd.to_numeric(result["network_distance_m"], errors="coerce")
    result["shelter_capacity"] = pd.to_numeric(result["shelter_capacity"], errors="coerce")

    route_ready = result["route_status"].eq("complete") & result["nearest_shelter_id"].notna()
    result["assigned_population"] = result["total_population"].where(route_ready)
    pressure_ready = result["assigned_population"].notna() & result["shelter_capacity"].gt(0)
    result["capacity_pressure"] = (
        result["assigned_population"] / result["shelter_capacity"]
    ).where(pressure_ready)
    result["capacity_component_status"] = "missing"
    result.loc[route_ready & result["shelter_capacity"].isna(), "capacity_component_status"] = "missing_capacity"
    result.loc[route_ready & result["shelter_capacity"].notna() & ~pressure_ready, "capacity_component_status"] = "invalid_capacity"
    result.loc[pressure_ready, "capacity_component_status"] = "complete"
    result["assignment_scenario_note"] = (
        "assigned_population is the mesh population under a hypothetical nearest-shelter assignment; it is not an actual evacuee forecast"
    )

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.out_csv, index=False, encoding="utf-8-sig")
    qa = {
        "mesh_count": len(result),
        "route_ready_meshes": int(route_ready.sum()),
        "assigned_population_nonnull": int(result["assigned_population"].notna().sum()),
        "capacity_pressure_nonnull": int(result["capacity_pressure"].notna().sum()),
        "missing_capacity_among_route_ready": int((route_ready & result["shelter_capacity"].isna()).sum()),
        "capacity_parse_status": capacities["capacity_parse_status"].value_counts(dropna=False).to_dict(),
        "missing_capacity_is_not_zero": True,
        "assignment_scenario": "nearest tsunami-compatible shelter by pedestrian-network distance",
    }
    args.out_qa.parent.mkdir(parents=True, exist_ok=True)
    args.out_qa.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False))


if __name__ == "__main__":
    main()
