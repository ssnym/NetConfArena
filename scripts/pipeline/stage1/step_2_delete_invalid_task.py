#!/usr/bin/env python3
"""Stage 1 / Action 2: filter raw candidates with exclusion sets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_list(path: Path, *, optional: bool = False) -> list[str]:
    if optional and not path.exists():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{path} must contain a JSON list of paths")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate-dir", type=Path,
        default=Path("pipeline_work/gns3vault/raw_candidates"),
    )
    parser.add_argument(
        "--manual-exclusions", type=Path,
        help="Optional JSON list (for example unsupported Frame Relay labs)",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.candidate_dir
    all_tasks = load_list(root / "all_tasks.json")
    exclusion_files = (
        root / "no_topology_tasks.json",
        root / "no_startup_tasks.json",
        root / "no_ground_truth_tasks.json",
    )
    excluded = {item for path in exclusion_files for item in load_list(path)}
    if args.manual_exclusions:
        excluded.update(load_list(args.manual_exclusions, optional=True))
    valid = [item for item in all_tasks if item not in excluded]
    output = args.output or root / "valid_tasks.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(valid, indent=2) + "\n", encoding="utf-8")
    print(f"Kept {len(valid)} of {len(all_tasks)} candidates; wrote {output}")


if __name__ == "__main__":
    main()
