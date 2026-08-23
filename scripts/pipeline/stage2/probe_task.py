"""Interactive task probe for deploying a topology and running show commands.

Use this tool to investigate why a reference configuration fails on an
emulated device. Validation reports failed assertions; the probe exposes the
device's actual state.

Examples:
  # Apply the reference configuration, then run commands on selected nodes.
  python -m scripts.pipeline.stage2.probe_task --task_index 67 --seed 1 \
      --cmd "router2:show running-config | include mpls static" \
      --cmd "router2:show mpls forwarding-table"

  # Apply additional candidate configuration before inspecting the result.
  python -m scripts.pipeline.stage2.probe_task --task_index 67 --seed 1 \
      --extra "router2:mpls label range 1000 100000 static 16 999" \
      --cmd "router2:show mpls label range"

  # Build only the startup baseline, without applying the reference config.
  python -m scripts.pipeline.stage2.probe_task --task_index 67 --seed 1 --no-groundtruth --cmd "router2:show mpls ldp neighbor"

Node selectors may be parameter keys such as router2_name or rendered node
names. --cmd and --extra are repeatable and preserve order; all --extra lines
are applied before any --cmd is run.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# Allow both direct execution and module execution from the repository root.
REPO_ROOT = str(Path(__file__).resolve().parents[3])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(REPO_ROOT, ".env"))

from task.task_loader import render_task
from environment.task_environment import TaskEnvironment

parser = argparse.ArgumentParser()
parser.add_argument("--task_index", type=int, required=True, help="Index into task/all_tasks.json")
parser.add_argument("--seed", type=int, default=1)
parser.add_argument("--cmd", action="append", default=[], metavar="NODE:COMMAND",
                    help="Show command to execute; may be repeated")
parser.add_argument("--extra", action="append", default=[], metavar="NODE:CONFIG",
                    help="Configuration line to apply after groundtruth; repeatable")
parser.add_argument("--no-groundtruth", action="store_true",
                    help="Do not apply groundtruth_configs")
parser.add_argument("--wait", type=int, default=0, metavar="SECONDS",
                    help="Seconds to wait for convergence before show commands")
parser.add_argument("--keep", action="store_true",
                    help="Keep the GNS3 project open on exit")
parser.add_argument("--raw", action="store_true",
                    help="Print raw CLI output instead of only parsed data")
args = parser.parse_args()

task_data = render_task(args.task_index, randomize=True, seed=args.seed)
params = task_data.get("resolved_params", {})

print(f"scenario : {task_data['scenario']}")
print(f"params   : {json.dumps(params, indent=2)}\n")


def resolve_node(name):
    """Resolve router2, router2_name, or an actual node name."""
    if name in params:
        return params[name]
    if f"{name}_name" in params:
        return params[f"{name}_name"]
    return name


def split_spec(spec):
    node, sep, rest = spec.partition(":")
    if not sep:
        raise SystemExit(f"Malformed spec (expected NODE:TEXT): {spec!r}")
    return resolve_node(node.strip()), rest.strip()


task_env = TaskEnvironment(task_data)
try:
    if not args.no_groundtruth:
        print("=== applying groundtruth_configs ===")
        task_env.apply_config(task_data["groundtruth_configs"])

    if args.extra:
        extra_by_node = {}
        for spec in args.extra:
            node, line = split_spec(spec)
            extra_by_node.setdefault(node, []).append(line)
        print(f"=== applying extra configs ===\n{json.dumps(extra_by_node, indent=2)}")
        task_env.apply_config(extra_by_node)

    if args.wait:
        print(f"\n=== waiting {args.wait}s for convergence ===")
        time.sleep(args.wait)

    iface = task_env.evaluator_interface
    for spec in args.cmd:
        node, command = split_spec(spec)
        print(f"\n{'=' * 70}\n### {node} | {command}\n{'=' * 70}")
        output = iface.execute_command(node, command)
        if args.raw:
            print(output)
        else:
            from evaluator.parse_cli_output import parse_cli_output
            parsed = parse_cli_output(command, output)
            print("--- raw ---")
            print(output)
            print("--- parsed ---")
            print(json.dumps(parsed, indent=2, ensure_ascii=False))
finally:
    if args.keep:
        print("\n(Project kept open.)")
    else:
        task_env.close()
