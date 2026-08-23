#!/usr/bin/env python3
"""Stage 1 / Action 3: normalize source JSON and add test-case shells.

This command writes review candidates to ``--out-dir`` without overwriting
source files. It reconciles goals and test cases, normalizes accepted input
keys, and keeps physical topologies as human-review inputs until they are
converted into parameterized templates.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from scripts.pipeline.common import slugify, write_json


def split_goals(goals: str) -> list[str]:
    return [goal.strip() for goal in goals.split("•") if goal.strip()]


def read_commands(path: Path) -> list[str]:
    return [
        line.rstrip()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
        and not line.lstrip().startswith(("!", "Building configuration", "Current configuration"))
    ]


def read_startup_config_files(
    paths: list[str], topology: dict[str, Any]
) -> dict[str, list[str]]:
    configs: dict[str, list[str]] = {}
    available = [Path(raw_path) for raw_path in paths if Path(raw_path).exists()]
    for node in topology.get("nodes", []):
        if node.get("type") == "EthernetSwitch":
            continue
        node_name = node.get("label", {}).get("text", "")
        configured_path = str(node.get("properties", {}).get("startup_config", ""))
        configured_name = re.split(r"[\\/]", configured_path)[-1]
        match = next(
            (path for path in available if configured_name and configured_name in path.name),
            None,
        )
        if match is not None and node_name:
            configs[node_name] = read_commands(match)
    return configs


def read_groundtruth_config_files(
    paths: list[str], topology: dict[str, Any]
) -> dict[str, list[str]]:
    node_names = {
        str(node.get("label", {}).get("text", "")).lower():
        str(node.get("label", {}).get("text", ""))
        for node in topology.get("nodes", [])
    }
    configs: dict[str, list[str]] = {}
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            continue
        node_name = node_names.get(path.stem.lower(), path.stem)
        configs[node_name] = read_commands(path)
    return configs


def add_testcase_shells(task: dict[str, Any]) -> None:
    testcases = task.get("testcases", [])
    existing = {item.get("goal") for item in testcases if isinstance(item, dict)}
    for goal in split_goals(str(task.get("goals", ""))):
        if goal not in existing:
            testcases.append({"goal": goal, "testcase": []})
    task["testcases"] = testcases


def normalize_task(source: dict[str, Any], *, materialize_configs: bool) -> dict[str, Any]:
    task = dict(source)
    task["scenario"] = task.get("scenario") or task.get("task_name", "Untitled task")
    task["goals"] = task.get("goals", task.get("intents", ""))
    task["startup_configs"] = task.get("startup_configs", task.get("initial_configs", {}))
    task["groundtruth_configs"] = task.get(
        "groundtruth_configs", task.get("ground_truth_configs", {})
    )
    topology = task.get("topology", {}) or {}
    if materialize_configs:
        if isinstance(task["startup_configs"], list):
            task["startup_configs"] = read_startup_config_files(
                task["startup_configs"], topology
            )
        if isinstance(task["groundtruth_configs"], list):
            task["groundtruth_configs"] = read_groundtruth_config_files(
                task["groundtruth_configs"], topology
            )
    add_testcase_shells(task)
    task.pop("intents", None)
    task.pop("initial_configs", None)
    task.pop("ground_truth_configs", None)
    task["construction_status"] = {
        "stage": "collection_and_normalization",
        "human_review_required": True,
        "parameterization_required": not {
            "params", "topology_model", "topology_attributes"
        }.issubset(task),
        "executable_tests_required": not task["testcases"] or any(
            not item.get("testcase") for item in task["testcases"]
        ),
    }
    return task


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", action="append", type=Path, default=[])
    parser.add_argument("--task-list", type=Path, help="JSON list produced by Step 2")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--materialize-configs", action="store_true",
        help="Read referenced .cfg files into command lists",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sources = list(args.task)
    if args.task_list:
        sources.extend(Path(path) for path in json.loads(args.task_list.read_text(encoding="utf-8")))
    if not sources:
        raise SystemExit("provide --task and/or --task-list")
    manifest = []
    for source_path in sources:
        source = json.loads(source_path.read_text(encoding="utf-8"))
        normalized = normalize_task(source, materialize_configs=args.materialize_configs)
        name = slugify(str(normalized["scenario"]))
        destination = args.out_dir / name / f"{name}.json"
        write_json(destination, normalized)
        manifest.append({"source": str(source_path), "candidate": str(destination)})
    write_json(args.out_dir / "normalization_manifest.json", manifest)
    print(f"Normalized {len(manifest)} candidates under {args.out_dir}")
    print("Human review and conversion to the parameterized schema are still required.")


if __name__ == "__main__":
    main()
