#!/usr/bin/env python3
"""Stage 2 / Step 4: generate an optional LLM reasoning draft for human review.

The model response is not ground truth. The output is a review artifact only: it neither
changes the task nor counts as static, emulator, or test-case evidence.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.pipeline.common import call_llm, write_json


SYSTEM_PROMPT = """You assist a human network benchmark author. Explain a
plausible configuration approach and flag assumptions or missing evidence.
Do not claim that the configuration or test cases have been validated. The
human reviewer and emulator provide authoritative validation."""


def build_prompt(task: dict) -> str:
    payload = {
        "scenario": task.get("scenario"),
        "goals": task.get("goals"),
        "topology_model": task.get("topology_model", task.get("topology")),
        "startup_configs": task.get("startup_configs"),
        "reference_configuration": task.get(
            "groundtruth_configs", task.get("ground_truth_configs")
        ),
        "testcases": task.get("testcases"),
    }
    return (
        "Draft a step-by-step rationale for a human author. Separate facts "
        "visible in the task from assumptions, and list emulator observations "
        "needed to validate the draft.\n\n" + json.dumps(payload, indent=2, ensure_ascii=False)
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--model", help="OpenAI-compatible model name")
    parser.add_argument("--response", type=Path, help="Use an existing model response")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if bool(args.model) == bool(args.response):
        raise SystemExit("provide exactly one of --model or --response")
    task = json.loads(args.task.read_text(encoding="utf-8"))
    prompt = build_prompt(task)
    content = (
        call_llm(args.model, SYSTEM_PROMPT, prompt)
        if args.model else args.response.read_text(encoding="utf-8")
    )
    output = args.output or args.task.with_name("step4_reasoning_draft.json")
    write_json(output, {
        "artifact": "llm_reasoning_draft",
        "authoritative_validation_evidence": False,
        "model": args.model or "external/pre-generated",
        "prompt": prompt,
        "response": content,
        "human_review_status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    print(f"Wrote non-authoritative reasoning draft to {output}")


if __name__ == "__main__":
    main()
