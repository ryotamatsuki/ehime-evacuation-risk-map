#!/usr/bin/env python3
"""Verify STEP 8/9 adds only capacity-planning files to canonical public data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ALLOWED_PREFIX = "capacity-planning/"


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


if __name__ == "__main__":
    main()
