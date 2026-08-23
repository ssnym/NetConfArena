#!/usr/bin/env python3
"""Aggregate NetConfArena run results into benchmark metrics.

Reads every run JSON written by ``main.py`` under ``results/`` (or a directory
given with ``--results-dir``) and reports, per (model, mode):

  Outcome metrics
    test case score   mean of the per-run score produced by the evaluator: the
                      arithmetic mean over scoring groups of the within-group
                      pass fraction
    task pass rate    fraction of runs in which every test unit passes — the
                      stricter, task-level measure
    goal pass rate    fraction of scoring groups fully passed, pooled over runs

  Configuration correctness
    precision/recall  of the applied configuration diff against the reference
                      configuration, micro-averaged over runs
    exact match       fraction of runs whose diff equals the reference exactly

  Interaction efficiency
    turns             mean action turns consumed
    budget exhausted  fraction of runs that hit the turn limit without submitting
    action mix        share of each of the five interface actions

  Breakdowns
    by category, subcategory and complexity tier (joined from
    task/task_taxonomy.json via scripts/compute_task_stats.py output)

  Generalizability analysis (--generalizability)
    across protocol categories, within a category across templates, and across
    parameterizations (seeds) of the same template

  Failure signals (--failure-signals)
    heuristic trajectory signals for triage. These are not authoritative
    failure labels, and the output says so explicitly.

Usage:
    python scripts/aggregate_results.py
    python scripts/aggregate_results.py --generalizability --failure-signals
    python scripts/aggregate_results.py --out analysis/metrics.json --csv analysis/runs.csv
"""

import argparse
import csv
import json
import os
import re
import statistics
import sys
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ACTIONS = ["get_current_config", "apply_config", "execute_validation",
           "wait_for_stabilization", "submit"]
# Aliases accepted by the interface map onto the same action.
ACTION_ALIASES = {"get_running_config": "get_current_config", "wait": "wait_for_stabilization"}

# Heuristic markers of a rejected or invalid CLI command.
CLI_ERROR_RE = re.compile(r"% (Invalid input|Incomplete command|Ambiguous command|Unrecognized)",
                          re.IGNORECASE)


def load_taxonomy():
    """scenario name -> {category, subcategory, complexity}, via task_stats.json."""
    stats_path = os.path.join(REPO_ROOT, "task", "task_stats.json")
    if not os.path.exists(stats_path):
        return {}
    with open(stats_path) as f:
        data = json.load(f)
    return {
        row["scenario"]: {
            "category": row.get("category", ""),
            "subcategory": row.get("subcategory", ""),
            "complexity": row.get("complexity", ""),
            "task_index": row.get("task_index"),
        }
        for row in data.get("tasks", [])
    }


def parse_action(action_str):
    """Extract the action name from a recorded 'Action:' payload.

    The ReAct harness records a call expression (``apply_config({...})``); the
    MCP server records the tool name alone. Accept either.
    """
    if not action_str:
        return None
    match = re.match(r"\s*`*\s*([A-Za-z_][A-Za-z0-9_]*)\s*(\(|$)", str(action_str).strip())
    if not match:
        return None
    name = match.group(1)
    return ACTION_ALIASES.get(name, name)


def observation_text(observation):
    if isinstance(observation, str):
        return observation
    try:
        return json.dumps(observation)
    except (TypeError, ValueError):
        return str(observation)


def load_run(path, taxonomy):
    with open(path) as f:
        data = json.load(f)

    evaluation = data.get("evaluation_data", {}) or {}
    testcases = evaluation.get("testcase_execution_results", {}) or {}
    config = evaluation.get("config_evaluation_results", {}) or {}
    config_metrics = config.get("evaluation_result", {}) or {}

    units = testcases.get("testcases_passed", {}) or {}
    unit_total = len(units)
    unit_passed = sum(1 for value in units.values() if value)

    # Scoring groups: test ids share a prefix before the first '-'.
    groups = defaultdict(lambda: [0, 0])
    for unit_id, passed in units.items():
        key = str(unit_id).split("-", 1)[0] if "-" in str(unit_id) else str(unit_id)
        groups[key][1] += 1
        if passed:
            groups[key][0] += 1
    groups_full = sum(1 for passed, total in groups.values() if total and passed == total)

    trajectory = data.get("trajectory", []) or []
    action_counts = defaultdict(int)
    cli_errors = 0
    for step in trajectory:
        name = parse_action(step.get("Action"))
        if name:
            action_counts[name] += 1
        if CLI_ERROR_RE.search(observation_text(step.get("Observation"))):
            cli_errors += 1
    submitted = action_counts.get("submit", 0) > 0

    true_positives = config_metrics.get("true_positives")
    predicted = config_metrics.get("pred")
    ground_truth = config_metrics.get("ground_truth")

    scenario = data.get("scenario", "")
    placement = taxonomy.get(scenario, {})

    return {
        "path": os.path.relpath(path, REPO_ROOT),
        "scenario": scenario,
        "task_index": data.get("task_index") or placement.get("task_index"),
        "category": placement.get("category", ""),
        "subcategory": placement.get("subcategory", ""),
        "complexity": placement.get("complexity", ""),
        "llm_model": data.get("llm_model", ""),
        "mode": data.get("mode", ""),
        "seed": data.get("seed"),
        "trial": data.get("trial"),
        "score": testcases.get("score"),
        "unit_total": unit_total,
        "unit_passed": unit_passed,
        "task_passed": bool(unit_total) and unit_passed == unit_total,
        "groups_total": len(groups),
        "groups_passed": groups_full,
        "config_true_positives": true_positives,
        "config_pred": predicted,
        "config_ground_truth": ground_truth,
        "config_exact_match": (
            true_positives is not None and true_positives == predicted == ground_truth
        ),
        "turns": len(trajectory),
        "submitted": submitted,
        "cli_error_turns": cli_errors,
        "actions": dict(action_counts),
        "routing_converged": evaluation.get("routing_converged"),
    }


def mean(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def fraction(values):
    values = list(values)
    return (sum(1 for v in values if v) / len(values)) if values else None


def fmt(value, digits=3):
    return "n/a" if value is None else f"{value:.{digits}f}"


def summarize(runs):
    """Metrics over a set of runs."""
    tp = sum(r["config_true_positives"] or 0 for r in runs)
    pred = sum(r["config_pred"] or 0 for r in runs)
    gt = sum(r["config_ground_truth"] or 0 for r in runs)
    groups_total = sum(r["groups_total"] for r in runs)
    groups_passed = sum(r["groups_passed"] for r in runs)

    action_totals = defaultdict(int)
    for run in runs:
        for name, count in run["actions"].items():
            action_totals[name] += count
    action_sum = sum(action_totals.values())

    return {
        "runs": len(runs),
        "templates": len({r["scenario"] for r in runs}),
        "test_case_score": mean(r["score"] for r in runs),
        "task_pass_rate": fraction(r["task_passed"] for r in runs),
        "goal_pass_rate": (groups_passed / groups_total) if groups_total else None,
        "config_precision": (tp / pred) if pred else None,
        "config_recall": (tp / gt) if gt else None,
        "config_exact_match_rate": fraction(r["config_exact_match"] for r in runs),
        "mean_turns": mean(r["turns"] for r in runs),
        "submit_rate": fraction(r["submitted"] for r in runs),
        "budget_exhausted_rate": fraction(not r["submitted"] for r in runs),
        "convergence_failure_rate": fraction(r["routing_converged"] is False for r in runs),
        "action_mix": {
            name: action_totals.get(name, 0) / action_sum for name in ACTIONS
        } if action_sum else {},
    }


def generalizability(runs):
    """Spread of performance across categories, within categories, across seeds."""
    def by(key):
        buckets = defaultdict(list)
        for run in runs:
            if run[key]:
                buckets[run[key]].append(run)
        return {k: summarize(v)["test_case_score"] for k, v in sorted(buckets.items())}

    across_categories = by("category")
    category_scores = [v for v in across_categories.values() if v is not None]

    # Within a category: spread across the templates that make it up.
    within_category = {}
    per_category = defaultdict(lambda: defaultdict(list))
    for run in runs:
        if run["category"]:
            per_category[run["category"]][run["scenario"]].append(run)
    for category, templates in sorted(per_category.items()):
        scores = [summarize(v)["test_case_score"] for v in templates.values()]
        scores = [s for s in scores if s is not None]
        if len(scores) >= 2:
            within_category[category] = {
                "templates": len(scores),
                "mean": statistics.fmean(scores),
                "stdev": statistics.pstdev(scores),
                "min": min(scores),
                "max": max(scores),
            }

    # Across parameterizations: spread over seeds of the same template.
    per_template_seed = defaultdict(lambda: defaultdict(list))
    for run in runs:
        if run["seed"] is not None:
            per_template_seed[run["scenario"]][run["seed"]].append(run)
    seed_spreads = []
    for scenario, seeds in per_template_seed.items():
        if len(seeds) < 2:
            continue
        scores = [summarize(v)["test_case_score"] for v in seeds.values()]
        scores = [s for s in scores if s is not None]
        if len(scores) >= 2:
            seed_spreads.append({
                "scenario": scenario,
                "seeds": len(scores),
                "mean": statistics.fmean(scores),
                "stdev": statistics.pstdev(scores),
                "range": max(scores) - min(scores),
            })

    return {
        "across_categories": {
            "score_by_category": across_categories,
            "stdev": statistics.pstdev(category_scores) if len(category_scores) >= 2 else None,
            "spread": (max(category_scores) - min(category_scores))
                      if len(category_scores) >= 2 else None,
        },
        "within_category": within_category,
        "across_parameterizations": {
            "templates_with_multiple_seeds": len(seed_spreads),
            "mean_stdev_across_seeds": mean(s["stdev"] for s in seed_spreads),
            "mean_range_across_seeds": mean(s["range"] for s in seed_spreads),
            "per_template": sorted(seed_spreads, key=lambda s: -s["stdev"])[:20],
        },
        "note": ("Score spread across categories reflects task difficulty as well as "
                 "generalization; the within-category and across-seed spreads isolate "
                 "template-to-template and parameterization sensitivity respectively."),
    }


def failure_signals(runs, turn_limit):
    """Compute heuristic failure signals for triage, not authoritative labels."""
    signals = defaultdict(int)
    failed = [r for r in runs if not r["task_passed"]]
    for run in failed:
        actions = run["actions"]
        applies = actions.get("apply_config", 0)
        validations = actions.get("execute_validation", 0)

        # Knowledge deficiency: the device rejected commands the agent issued.
        if run["cli_error_turns"] > 0:
            signals["knowledge_deficiency"] += 1
        # Unproductive hesitation: burned the turn budget mostly on inspection.
        if not run["submitted"] and validations > applies:
            signals["unproductive_hesitation"] += 1
        # Premature abandonment: submitted early with the task unsolved.
        if run["submitted"] and run["turns"] < 0.5 * turn_limit:
            signals["premature_abandonment"] += 1
        # Specification deviation: configured plenty, but the diff does not match
        # the reference — a solution built the way the task forbade.
        if (applies > 0 and run["config_ground_truth"]
                and (run["config_true_positives"] or 0) < 0.75 * run["config_ground_truth"]):
            signals["specification_deviation"] += 1

    return {
        "failed_runs": len(failed),
        "signal_counts": dict(signals),
        "signal_rates": {k: v / len(failed) for k, v in signals.items()} if failed else {},
        "caveat": ("Heuristic signals computed from trajectories for triage only. A run "
                   "may match several signals; these counts are not authoritative labels."),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-dir", default=os.path.join(REPO_ROOT, "results"))
    parser.add_argument("--out", default=None, help="Write the full metrics as JSON here")
    parser.add_argument("--csv", default=None, help="Write one row per run here")
    parser.add_argument("--generalizability", action="store_true",
                        help="Include the generalizability analysis")
    parser.add_argument("--failure-signals", action="store_true",
                        help="Include heuristic failure-mode signals")
    parser.add_argument("--turn-limit", type=int, default=20,
                        help="Action-turn budget used in the runs (default 20, matching "
                             "agent/configs/ReAct.yaml)")
    parser.add_argument("--by", default="category",
                        choices=["category", "subcategory", "complexity", "scenario"],
                        help="Breakdown dimension for the printed table (default category)")
    args = parser.parse_args()

    if not os.path.isdir(args.results_dir):
        sys.exit(f"no such results directory: {args.results_dir}")

    taxonomy = load_taxonomy()
    if not taxonomy:
        print("[warn] task/task_stats.json not found — run scripts/compute_task_stats.py "
              "first to enable category breakdowns", file=sys.stderr)

    runs = []
    for root, _, files in os.walk(args.results_dir):
        for name in sorted(files):
            if not name.endswith(".json"):
                continue
            path = os.path.join(root, name)
            try:
                runs.append(load_run(path, taxonomy))
            except Exception as exc:
                print(f"[warn] {path}: {type(exc).__name__}: {exc}", file=sys.stderr)

    if not runs:
        sys.exit(f"no run JSON files found under {args.results_dir}")

    configurations = defaultdict(list)
    for run in runs:
        configurations[(run["llm_model"], run["mode"])].append(run)

    report = {"results_dir": os.path.relpath(args.results_dir, REPO_ROOT),
              "total_runs": len(runs), "configurations": {}}

    for (model, mode), group in sorted(configurations.items()):
        label = f"{model} / {mode}"
        entry = {"overall": summarize(group), "breakdown": {}}

        buckets = defaultdict(list)
        for run in group:
            buckets[run[args.by] or "(unknown)"].append(run)
        for key, bucket in sorted(buckets.items()):
            entry["breakdown"][key] = summarize(bucket)

        if args.generalizability:
            entry["generalizability"] = generalizability(group)
        if args.failure_signals:
            entry["failure_signals"] = failure_signals(group, args.turn_limit)

        report["configurations"][label] = entry

        overall = entry["overall"]
        print(f"\n=== {label} ===")
        print(f"runs / templates      : {overall['runs']} / {overall['templates']}")
        print(f"test case score       : {fmt(overall['test_case_score'])}")
        print(f"task pass rate        : {fmt(overall['task_pass_rate'])}")
        print(f"goal pass rate        : {fmt(overall['goal_pass_rate'])}")
        print(f"config precision      : {fmt(overall['config_precision'])}")
        print(f"config recall         : {fmt(overall['config_recall'])}")
        print(f"config exact match    : {fmt(overall['config_exact_match_rate'])}")
        print(f"mean action turns     : {fmt(overall['mean_turns'], 1)}")
        print(f"submit rate           : {fmt(overall['submit_rate'])}")
        print(f"budget exhausted      : {fmt(overall['budget_exhausted_rate'])}")
        if overall["action_mix"]:
            mix = "  ".join(f"{k}={v:.2f}" for k, v in overall["action_mix"].items())
            print(f"action mix            : {mix}")

        print(f"\n  by {args.by}:")
        print(f"  {'group':<32} {'runs':>5} {'score':>7} {'pass':>7} {'turns':>6}")
        for key, stats in entry["breakdown"].items():
            print(f"  {key[:32]:<32} {stats['runs']:>5} {fmt(stats['test_case_score']):>7} "
                  f"{fmt(stats['task_pass_rate']):>7} {fmt(stats['mean_turns'], 1):>6}")

        if args.generalizability:
            gen = entry["generalizability"]
            print("\n  generalizability:")
            spread = gen["across_categories"]["spread"]
            print(f"    across categories       : spread {fmt(spread)}, "
                  f"stdev {fmt(gen['across_categories']['stdev'])}")
            for category, stats in gen["within_category"].items():
                print(f"    within {category[:20]:<20} : mean {fmt(stats['mean'])}, "
                      f"stdev {fmt(stats['stdev'])} over {stats['templates']} templates")
            across = gen["across_parameterizations"]
            print(f"    across parameterizations: {across['templates_with_multiple_seeds']} templates, "
                  f"mean stdev {fmt(across['mean_stdev_across_seeds'])}, "
                  f"mean range {fmt(across['mean_range_across_seeds'])}")

        if args.failure_signals:
            signals = entry["failure_signals"]
            print(f"\n  failure signals (heuristic) over {signals['failed_runs']} failed runs:")
            for key, count in sorted(signals["signal_counts"].items(), key=lambda kv: -kv[1]):
                print(f"    {key:<26} {count:>4}  ({signals['signal_rates'][key]:.2f})")

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nwrote {args.out}")

    if args.csv:
        os.makedirs(os.path.dirname(os.path.abspath(args.csv)), exist_ok=True)
        columns = ["path", "task_index", "scenario", "category", "subcategory", "complexity",
                   "llm_model", "mode", "seed", "trial", "score", "unit_passed", "unit_total",
                   "task_passed", "groups_passed", "groups_total", "config_true_positives",
                   "config_pred", "config_ground_truth", "config_exact_match", "turns",
                   "submitted", "cli_error_turns", "routing_converged"]
        with open(args.csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(runs)
        print(f"wrote {args.csv}")


if __name__ == "__main__":
    main()
