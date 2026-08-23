"""Generate a rendered task instance for human review.

For each task we render with `render_task(idx, randomize=True, seed=SEED)` and
emit `seed_<seed>_rendered_task.txt` into the task directory, containing:
  - the Oneshot prompt   (uses agent/configs/OneShot.yaml)
  - the ReAct prompt     (uses agent/configs/ReAct.yaml)
  - the instantiated startup_configs and groundtruth_configs
  - the instantiated testcases

Only the two prompt sections are what an agent actually sees at runtime; the
rest is reference material for reviewing the task. The topology section is the
JSON physical topology produced by `agent.utils.build_physical_topology` —
identical to what the agents send to the LLM at runtime.
"""

"""Run with: python -m scripts.pipeline.stage3.render_review (from project root).

Optional flags:
  --task_index N   only regenerate task N (0-based index into all_tasks.json)
  --seed S         render with seed S instead of the default 0
"""

import argparse
import json
import os
import sys
from string import Template
from pathlib import Path

import yaml

ROOT = str(Path(__file__).resolve().parents[3])
# Allow both direct execution and module execution from the repository root.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from task.task_loader import render_task
from agent.utils import build_physical_topology

ALL_TASKS = os.path.join(ROOT, "task", "all_tasks.json")
SEED = 0

AGENT_CONFIGS = [
    ("oneshot", os.path.join(ROOT, "agent", "configs", "OneShot.yaml")),
    ("react",   os.path.join(ROOT, "agent", "configs", "ReAct.yaml")),
]


DEFAULT_MAX_ACTION_TURNS = 20


def load_prompts(path: str) -> tuple[str, Template]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    max_action_turns = int(cfg["agent"].get("max_action_turns", DEFAULT_MAX_ACTION_TURNS))
    system_prompt = Template(cfg["agent"]["system_prompt"]).safe_substitute(
        max_action_turns=max_action_turns
    )
    return system_prompt, Template(cfg["agent"]["task_prompt"])


def format_configs(configs: dict) -> str:
    """Render a {device: [command, ...]} mapping as readable per-device blocks."""
    if not configs:
        return "(none)"
    return "\n\n".join(
        f"----- {device} -----\n" + "\n".join(lines) for device, lines in configs.items()
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--task_index",
        type=int,
        default=None,
        help="Only regenerate this task (0-based index into task/all_tasks.json). Default: all tasks.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=SEED,
        help=f"Random seed used to render the task. Default: {SEED}.",
    )
    parser.add_argument(
        "--out-dir",
        default=os.path.join(ROOT, "pipeline_work", "rendered_tasks"),
        help="Directory for review artifacts (default: pipeline_work/rendered_tasks).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    templates = [(name, *load_prompts(path)) for name, path in AGENT_CONFIGS]

    with open(ALL_TASKS, "r", encoding="utf-8") as f:
        task_paths = json.load(f)

    indices = range(len(task_paths)) if args.task_index is None else [args.task_index]

    for idx in indices:
        task_data = render_task(idx, randomize=True, seed=args.seed)

        scenario = task_data.get("scenario", "")
        goals = task_data.get("goals", "")
        current_config = json.dumps(
            {name: "\n".join(lines) for name, lines in task_data.get("startup_configs", {}).items()},
            indent=4
        )
        topology = json.dumps(build_physical_topology(task_data), indent=4)

        sections = []
        for name, system_prompt, template in templates:
            task_prompt = template.safe_substitute(
                scenario=scenario,
                goals=goals,
                topology=topology,
                current_config=current_config,
            )
            sections.append(
                f"===== {name.capitalize()} =====\n"
                f"----- System Prompt -----\n{system_prompt}\n\n"
                f"----- Task Prompt -----\n{task_prompt}"
            )

        sections.append(
            "===== Startup Configs =====\n"
            + format_configs(task_data.get("startup_configs", {}))
        )
        sections.append(
            "===== Groundtruth Configs =====\n"
            + format_configs(task_data.get("groundtruth_configs", {}))
        )

        testcases = json.dumps(task_data.get("testcases", []), indent=4)
        sections.append(f"===== Testcases =====\n{testcases}")

        task_name = os.path.basename(os.path.dirname(task_paths[idx]))
        task_dir = os.path.join(args.out_dir, task_name)
        os.makedirs(task_dir, exist_ok=True)
        out_path = os.path.join(task_dir, f"seed_{args.seed}_rendered_task.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(sections) + "\n")
        print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
