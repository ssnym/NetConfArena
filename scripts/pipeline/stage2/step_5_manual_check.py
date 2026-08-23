#!/usr/bin/env python3
"""Stage 2 / Step 5: run manual/emulator checks against the current API.

The default ``emulate`` mode deploys one rendered candidate, applies its
reference configuration, runs the deterministic evaluator, and writes a report.
``batfish`` preserves the optional notebook workflow by creating a temporary
snapshot from rendered reference configs and querying init issues and routes.
Neither mode edits the task.  A human must inspect the resulting report.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.pipeline.common import write_json
from task.task_loader import render_task_file


def run_emulator(task_data: dict[str, Any]) -> dict[str, Any]:
    from environment.task_environment import TaskEnvironment
    from evaluator.evaluate_task import Evaluator

    environment = TaskEnvironment(task_data)
    try:
        environment.apply_config(task_data["groundtruth_configs"])
        evaluator = Evaluator()
        evaluator.evaluate_task(environment.evaluator_interface, task_data)
        return {
            "evaluation": evaluator.task_evaluation_result,
            "observed_running_configs": environment.get_all_node_current_configs(),
        }
    finally:
        environment.close()


def run_batfish(task_data: dict[str, Any], host: str) -> dict[str, Any]:
    try:
        from pybatfish.client.session import Session
    except ImportError as exc:
        raise SystemExit("Batfish mode requires the optional pybatfish package") from exc

    with tempfile.TemporaryDirectory(prefix="netconfarena-batfish-") as temp_dir:
        snapshot = Path(temp_dir)
        configs_dir = snapshot / "configs"
        configs_dir.mkdir()
        for node, commands in task_data["groundtruth_configs"].items():
            text = "\n".join(commands) if isinstance(commands, list) else str(commands)
            (configs_dir / f"{node}.cfg").write_text(text + "\n", encoding="utf-8")
        session = Session(host=host)
        network = "netconfarena-manual-check"
        session.set_network(network)
        session.init_snapshot(str(snapshot), name="candidate", overwrite=True)
        return {
            "init_issues": json.loads(
                session.q.initIssues().answer().frame().to_json(orient="records")
            ),
            "routes": json.loads(
                session.q.routes().answer().frame().to_json(orient="records")
            ),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--mode", choices=("emulate", "batfish"), default="emulate")
    parser.add_argument("--batfish-host", default="localhost")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--review-notes", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    task_data = render_task_file(str(args.task), randomize=True, seed=args.seed)
    evidence = (
        run_emulator(task_data)
        if args.mode == "emulate" else run_batfish(task_data, args.batfish_host)
    )
    report = args.report or args.task.with_name(f"step5_{args.mode}_report.json")
    write_json(report, {
        "stage": "validation_and_refinement",
        "step": "manual_check",
        "mode": args.mode,
        "task": str(args.task),
        "seed": args.seed,
        "evidence": evidence,
        "human_approval_recorded": False,
        "reviewer": args.reviewer,
        "review_notes": args.review_notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    print(f"Wrote manual-review evidence to {report}")
    print("Inspect this report before recording approval with the Stage 2 driver.")


if __name__ == "__main__":
    main()
