#!/usr/bin/env python3
"""Stage 3 driver: instantiate an approved parameterized template by seed."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.pipeline.common import parse_seeds, write_json
from task.task_loader import render_task_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--stage2-report", type=Path,
                        help="Defaults to stage2_report.json beside the template")
    parser.add_argument("--seeds", default="1 2 3 4 5")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--allow-unapproved", action="store_true",
                        help="Debug only: instantiate without human Stage 2 approval")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report_path = args.stage2_report or args.template.with_name("stage2_report.json")
    if not args.allow_unapproved:
        if not report_path.exists():
            raise SystemExit(f"missing Stage 2 report: {report_path}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("human_status") != "approved":
            raise SystemExit("Stage 2 human_status is not approved")

    seeds = parse_seeds(args.seeds)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    instances = []
    resolved_variants = []
    for seed in seeds:
        rendered = render_task_file(str(args.template), randomize=True, seed=seed)
        output = args.out_dir / f"seed_{seed}.json"
        write_json(output, rendered)
        instances.append({"seed": seed, "path": str(output)})
        resolved_variants.append(rendered.get("resolved_params", {}))

    source_bytes = args.template.read_bytes()
    manifest = {
        "stage": "parameterization_and_instantiation",
        "method": "constraint-guided deterministic rendering after human approval",
        "template": str(args.template),
        "template_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "stage2_report": str(report_path),
        "seeds": seeds,
        "instances": instances,
        "parameterizations_are_distinct": len({
            json.dumps(value, sort_keys=True) for value in resolved_variants
        }) == len(resolved_variants),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(args.out_dir / "manifest.json", manifest)
    print(f"Rendered {len(instances)} instances into {args.out_dir}")


if __name__ == "__main__":
    main()
