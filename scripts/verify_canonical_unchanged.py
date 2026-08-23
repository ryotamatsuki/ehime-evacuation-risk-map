#!/usr/bin/env python3
"""Verify canonical byte identity and freeze STEP 8/9 production KPIs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from verify_step89_production_regression import compare, load

ALLOWED_PREFIX = "capacity-planning/"
DEFAULT_BASELINE = Path("tests/step89_production_baseline.json")
DEFAULT_STEP8 = Path("analysis/qa/step8_summary.json")
DEFAULT_STEP9 = Path("analysis/qa/step9_summary.json")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(root: Path, *, exclude_allowed: bool) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if exclude_allowed and relative.startswith(ALLOWED_PREFIX):
            continue
        result[relative] = digest(path)
    return result


def verify_production_baseline(canonical_qa: dict[str, object]) -> None:
    required = [DEFAULT_BASELINE, DEFAULT_STEP8, DEFAULT_STEP9]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit("STEP 8/9 production baseline inputs missing: " + ", ".join(missing))
    expected = load(DEFAULT_BASELINE)
    actual = {
        "step8": load(DEFAULT_STEP8),
        "step9": load(DEFAULT_STEP9),
        "canonical_unchanged": canonical_qa,
    }
    failures: list[str] = []
    compare(expected, actual, "", failures)
    if failures:
        print("STEP 8/9 production regression FAIL")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("STEP 8/9 production regression PASS")
    print("area_weighted served=139333.2 unserved=4923.9; full_mesh served=290411 unserved=15809")
    print("full-mesh shortage reduction: +100=100; +500/+1000/+2000/+5000=226")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before-root", type=Path, required=True)
    parser.add_argument("--after-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    before = manifest(args.before_root, exclude_allowed=True)
    after = manifest(args.after_root, exclude_allowed=True)
    missing = sorted(set(before) - set(after))
    unexpected = sorted(set(after) - set(before))
    changed = sorted(key for key in before.keys() & after.keys() if before[key] != after[key])
    additions = sorted(
        path.relative_to(args.after_root).as_posix()
        for path in args.after_root.rglob("*")
        if path.is_file() and path.relative_to(args.after_root).as_posix().startswith(ALLOWED_PREFIX)
    )
    qa = {
        "check": "canonical_public_data_byte_identity",
        "canonical_file_count": len(before),
        "missing_files": missing,
        "unexpected_non_capacity_files": unexpected,
        "changed_files": changed,
        "allowed_capacity_planning_additions": len(additions),
        "pass": not missing and not unexpected and not changed,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qa, ensure_ascii=False, indent=2))
    if not qa["pass"]:
        raise SystemExit("canonical public data changed outside capacity-planning/")
    verify_production_baseline(qa)


if __name__ == "__main__":
    main()
