import json
import os
import sys
import argparse
from pathlib import Path

# Allow both direct execution and
# `python -m scripts.pipeline.stage2.validate_task`.
REPO_ROOT = str(Path(__file__).resolve().parents[3])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(REPO_ROOT, ".env"))

from task.task_loader import render_task
from environment.task_environment import TaskEnvironment
from evaluator.evaluate_task import Evaluator

parser = argparse.ArgumentParser()
parser.add_argument("--task_index", type=int, default=0, help="Index into task/all_tasks.json")
parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
parser.add_argument("--dump", action="store_true", help="Save the rendered task_data to a JSON file")
args = parser.parse_args()

task_data = render_task(args.task_index, randomize=True, seed=args.seed)

if args.dump:
    dump_dir = os.path.join(REPO_ROOT, "pipeline_work", "task_data_dumps")
    os.makedirs(dump_dir, exist_ok=True)
    dump_path = os.path.join(dump_dir, f"task_{args.task_index}_seed_{args.seed}.json")
    with open(dump_path, "w", encoding="utf-8") as f:
        json.dump(task_data, f, indent=2, ensure_ascii=False)
    print(f"Saved task_data to {dump_path}\n")

print(f"scenario : {task_data['scenario']}")
print(f"params   : {json.dumps(task_data['resolved_params'], indent=2)}\n")

task_env = TaskEnvironment(task_data)
try:
    task_env.apply_config(task_data['groundtruth_configs'])

    evaluator = Evaluator()
    evaluator.evaluate_task(task_env.evaluator_interface, task_data)

    config_result = evaluator.task_evaluation_result.get("config_evaluation_results", {})
    testcase_result = evaluator.task_evaluation_result.get("testcase_execution_results", {})

    print("evaluation_result:")
    eval_metrics = config_result.get("evaluation_result", {})
    print(json.dumps(eval_metrics, indent=2))
    tp = eval_metrics.get("true_positives")
    pred = eval_metrics.get("pred")
    gt = eval_metrics.get("ground_truth")
    config_exact_match = tp is not None and tp == pred == gt
    print(f"config_exact_match: {str(config_exact_match).lower()}")
    print("testcases_passed:")
    print(json.dumps(testcase_result.get("testcases_passed", {}), indent=2))
    print(f"score: {testcase_result.get('score', 'N/A')}")
finally:
    task_env.close()
