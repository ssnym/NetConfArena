#!/usr/bin/env python3
"""Stage 2 driver: static/emulation validation, LLM critique, and human refinement.

The LLM may critique goals and test coverage, but its suggestions are only
recorded. It never edits the template. A human records the final disposition
with ``--human-status`` after reviewing static and emulator evidence.
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.pipeline.common import call_llm, parse_seeds, write_json
from task.task_loader import render_task_file


REQUIRED_KEYS = (
    "params", "scenario", "goals", "topology_model", "topology_attributes",
    "startup_configs", "groundtruth_configs", "testcases",
)


def static_review(template_path: Path, seeds: list[int]) -> tuple[list[str], list[str], list[dict]]:
    template = json.loads(template_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    warnings: list[str] = []
    rendered_summary = []

    for key in REQUIRED_KEYS:
        if key not in template:
            errors.append(f"missing top-level key: {key}")
    if errors:
        return errors, warnings, rendered_summary

    test_ids = []
    for goal_index, goal in enumerate(template.get("testcases", []), start=1):
        units = goal.get("testcase", [])
        if not units:
            warnings.append(f"testcase goal {goal_index} has no executable test unit")
        for unit in units:
            test_id = str(unit.get("id", ""))
            if not test_id:
                errors.append(f"testcase goal {goal_index} has a unit without id")
            test_ids.append(test_id)
            command = str(unit.get("command", "")).lstrip()
            if not command.startswith(("show ", "ping ", "traceroute ")):
                errors.append(f"test unit {test_id or '?'} uses a non-diagnostic command")
    duplicates = sorted({item for item in test_ids if test_ids.count(item) > 1})
    if duplicates:
        errors.append("duplicate test ids: " + ", ".join(duplicates))

    for seed in seeds:
        try:
            rendered = render_task_file(str(template_path), randomize=True, seed=seed)
            serialized = json.dumps(rendered, ensure_ascii=False)
            unresolved = sorted(set(re.findall(r"\{\{[^{}]+\}\}", serialized)))
            if unresolved:
                errors.append(f"seed {seed} has unresolved placeholders: {unresolved}")
            rendered_summary.append({
                "seed": seed,
                "scenario": rendered.get("scenario"),
                "devices": len(rendered.get("topology", {}).get("nodes", [])),
                "links": len(rendered.get("topology", {}).get("links", [])),
                "test_units": sum(len(g.get("testcase", [])) for g in rendered.get("testcases", [])),
            })
        except Exception as exc:
            errors.append(f"seed {seed} failed to render: {type(exc).__name__}: {exc}")

    return errors, warnings, rendered_summary


def emulation_review(template_path: Path, seeds: list[int]) -> list[dict[str, Any]]:
    from environment.task_environment import TaskEnvironment
    from evaluator.evaluate_task import Evaluator

    outcomes = []
    for seed in seeds:
        task_data = render_task_file(str(template_path), randomize=True, seed=seed)
        task_env = TaskEnvironment(task_data)
        try:
            task_env.apply_config(task_data["groundtruth_configs"])
            evaluator = Evaluator()
            evaluator.evaluate_task(task_env.evaluator_interface, task_data)
            result = evaluator.task_evaluation_result
            testcase_result = result.get("testcase_execution_results", {}) or {}
            units = testcase_result.get("testcases_passed", {}) or {}
            outcomes.append({
                "seed": seed,
                "score": testcase_result.get("score"),
                "task_passed": bool(units) and all(units.values()),
                "failed_test_ids": [key for key, passed in units.items() if not passed],
                "routing_converged": result.get("routing_converged"),
            })
        finally:
            task_env.close()
    return outcomes


def critique_prompt(template: dict) -> str:
    compact = {
        "scenario": template.get("scenario"),
        "goals": template.get("goals"),
        "topology_model": template.get("topology_model"),
        "groundtruth_configs": template.get("groundtruth_configs"),
        "testcases": template.get("testcases"),
    }
    return """Review this candidate network benchmark task. Identify ambiguous
goals, unsupported assumptions, missing positive/negative behavioral checks,
and tests that inspect syntax without establishing behavior. Do not rewrite the
template and do not claim emulator validation. Return a concise review for a
human benchmark author.\n\n""" + json.dumps(compact, indent=2, ensure_ascii=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--seeds", default="1 2 3")
    parser.add_argument("--emulate", action="store_true",
                        help="Deploy ground truth and run deterministic tests")
    parser.add_argument("--llm-model", help="Optional model for non-authoritative critique")
    parser.add_argument("--human-status", choices=("approved", "needs-revision"),
                        default="needs-revision")
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--review-notes", default="")
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    seeds = parse_seeds(args.seeds)
    report_path = args.report or args.template.with_name("stage2_report.json")
    errors, warnings, rendered = static_review(args.template, seeds)

    template = json.loads(args.template.read_text(encoding="utf-8"))
    llm_critique = None
    if args.llm_model:
        llm_critique = call_llm(
            args.llm_model,
            "You are a critical assistant to a human network benchmark author.",
            critique_prompt(template),
        )

    emulation = emulation_review(args.template, seeds) if args.emulate and not errors else []
    emulation_passed = bool(emulation) and all(item["task_passed"] for item in emulation)
    effective_status = args.human_status
    if args.human_status == "approved" and not args.emulate:
        warnings.append(
            "human approval was downgraded: Stage 2 approval requires "
            "emulator-grounded evidence (--emulate)"
        )
        effective_status = "needs-revision"
    if errors or (args.emulate and not emulation_passed):
        effective_status = "needs-revision"

    report = {
        "stage": "validation_and_refinement",
        "method": "llm-assisted, human-reviewed, emulator-grounded",
        "template": str(args.template),
        "seeds": seeds,
        "static_errors": errors,
        "static_warnings": warnings,
        "rendered_instances": rendered,
        "llm_critique": llm_critique,
        "llm_critique_applied_automatically": False,
        "emulation_requested": bool(args.emulate),
        "emulation_outcomes": emulation,
        "human_status": effective_status,
        "reviewer": args.reviewer,
        "review_notes": args.review_notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(report_path, report)
    print(f"Wrote Stage 2 report to {report_path}")
    print(f"Human status: {effective_status}")
    if errors:
        raise SystemExit(1)
    if args.emulate and not emulation_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
