"""Render the public task context for an MCP evaluation harness.

This is a command-line harness helper, not an MCP tool. Ground-truth
configuration and hidden test cases are deliberately omitted.
"""

import argparse
import json

from agent.utils import build_physical_topology
from task.task_loader import render_task


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-index", type=int, required=True, help="1-based task index")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()

    task_data = render_task(args.task_index - 1, randomize=True, seed=args.seed)
    public_context = {
        "task_index": args.task_index,
        "seed": args.seed,
        "scenario": task_data["scenario"],
        "topology": build_physical_topology(task_data),
        "goals": task_data["goals"],
    }
    print(json.dumps(public_context, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
