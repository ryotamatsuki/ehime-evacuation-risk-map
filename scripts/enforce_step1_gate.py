#!/usr/bin/env python3
"""Enforce the canonical STEP 1 routing-foundation release gate."""

from __future__ import annotations

import argparse
import json
import pathlib


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--foundation", type=pathlib.Path, required=True)
    parser.add_argument("--origins", type=pathlib.Path, required=True)
    args = parser.parse_args()

    foundation = json.loads(args.foundation.read_text(encoding="utf-8"))
    origins = json.loads(args.origins.read_text(encoding="utf-8"))
    foundation_gate = foundation["release_gate"]
    origin_gate = origins["release_gate"]
    print("foundation gate:", json.dumps(foundation_gate, ensure_ascii=False, indent=2))
    print("origin gate:", json.dumps(origin_gate, ensure_ascii=False, indent=2))

    failures: list[str] = []
    if not foundation_gate.get("network_aoi_is_boundary_based"):
        failures.append("network AOI is not N03 boundary based")
    if not foundation_gate.get("all_5821_meshes_snap_checked"):
        failures.append("not all 5,821 population meshes were checked")
    if not origin_gate.get("all_meshes_checked"):
        failures.append("origin classification is incomplete")
    if not origin_gate.get("all_analysis_targets_classified"):
        failures.append("a tsunami-target origin remains unclassified")
    if not origin_gate.get("network_coverage_gaps_are_explicit"):
        failures.append("network coverage gaps are not explicit")
    if origin_gate.get("blocking"):
        failures.append(
            f"unresolved tsunami-target origins={origin_gate.get('analysis_target_unresolved_count')}"
        )
    if failures:
        raise SystemExit("STEP 1 release gate failed: " + "; ".join(failures))
    print("STEP 1 release gate: PASS")


if __name__ == "__main__":
    main()
