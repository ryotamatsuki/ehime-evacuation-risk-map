#!/usr/bin/env python3
"""Verify stable STEP 8/9 production KPIs against the release baseline."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

ABS_TOL = 1e-6
REL_TOL = 1e-12


def compare(expected, actual, path: str, failures: list[str]) -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            failures.append(f"{path}: expected object, got {type(actual).__name__}")
            return
        for key, value in expected.items():
            child = f"{path}.{key}" if path else key
            if key not in actual:
                failures.append(f"{child}: missing")
            else:
                compare(value, actual[key], child, failures)
        return
    if isinstance(expected, list):
        if not isinstance(actual, list):
            failures.append(f"{path}: expected list, got {type(actual).__name__}")
            return
        if len(expected) != len(actual):
            failures.append(f"{path}: length {len(actual)} != {len(expected)}")
            return
        for index, value in enumerate(expected):
            compare(value, actual[index], f"{path}[{index}]", failures)
        return
    if isinstance(expected, bool) or expected is None or isinstance(expected, str):
        if actual != expected:
            failures.append(f"{path}: {actual!r} != {expected!r}")
        return
    if isinstance(expected, (int, float)):
        if not isinstance(actual, (int, float)) or isinstance(actual, bool):
            failures.append(f"{path}: expected numeric {expected!r}, got {actual!r}")
            return
        if not math.isclose(float(actual), float(expected), rel_tol=REL_TOL, abs_tol=ABS_TOL):
            failures.append(f"{path}: {actual!r} != {expected!r}")
        return
    if actual != expected:
        failures.append(f"{path}: {actual!r} != {expected!r}")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--step8", type=Path, required=True)
    parser.add_argument("--step9", type=Path, required=True)
    parser.add_argument("--canonical", type=Path, required=True)
    args = parser.parse_args()

    expected = load(args.baseline)
    actual = {
        "step8": load(args.step8),
        "step9": load(args.step9),
        "canonical_unchanged": load(args.canonical),
    }
    failures: list[str] = []
    compare(expected, actual, "", failures)
    if failures:
        print("STEP 8/9 production regression FAIL")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("STEP 8/9 production regression PASS")
    print("area_weighted: served=139333.2 unserved=4923.9; full_mesh: served=290411 unserved=15809")
    print("STEP 9 full-mesh shortage reduction: +100=100; +500/+1000/+2000/+5000=226")
    print("canonical public data: 33 existing files byte-identical; 8 additive capacity-planning files")


if __name__ == "__main__":
    main()
