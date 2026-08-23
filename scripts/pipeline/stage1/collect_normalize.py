#!/usr/bin/env python3
"""Stage 1 driver: LLM-assisted collection and normalization with human approval.

The script never treats an LLM response as a benchmark task automatically. It
first saves the extraction prompt and raw proposal. A candidate template is
emitted only when the operator explicitly supplies ``--human-approved``.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scripts.pipeline.common import call_llm, extract_json_object, slugify, write_json


SYSTEM_PROMPT = """You assist a network-benchmark author. Extract a candidate
task, but do not claim it is correct or validated. Return JSON only. Preserve
uncertainties explicitly; do not invent missing topology, configuration, or
test evidence. The human reviewer, not you, decides whether the candidate is
accepted."""


def extraction_prompt(source_text: str, source_id: str, source_url: str) -> str:
    return f"""Convert the following source notes into a NetConfArena candidate.

Return exactly this JSON shape:
{{
  "template": {{
    "params": {{}},
    "scenario": "...",
    "goals": "• ...",
    "topology_model": {{"nodes": [], "links": []}},
    "topology_attributes": {{"nodes": {{}}}},
    "startup_configs": {{}},
    "groundtruth_configs": {{}},
    "testcases": []
  }},
  "params_values": {{}},
  "extraction_notes": [],
  "uncertainties": []
}}

Use {{{{parameter_name}}}} placeholders for names, interfaces, and addresses.
Test cases are suggestions only and must use deterministic show/ping/traceroute
observations. Do not copy prose unnecessarily.

Source id: {source_id}
Source URL: {source_url or "not supplied"}

SOURCE NOTES
------------
{source_text}
"""


def validate_proposal(proposal: dict) -> list[str]:
    errors = []
    template = proposal.get("template")
    if not isinstance(template, dict):
        return ["proposal.template must be an object"]
    required = (
        "params", "scenario", "goals", "topology_model", "topology_attributes",
        "startup_configs", "groundtruth_configs", "testcases",
    )
    for key in required:
        if key not in template:
            errors.append(f"template is missing {key}")
    if not isinstance(proposal.get("params_values", {}), dict):
        errors.append("proposal.params_values must be an object")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path,
                        help="Human-readable source notes (txt/Markdown/JSON)")
    parser.add_argument("--out-dir", required=True, type=Path,
                        help="Working directory for prompt, proposal, and candidate")
    parser.add_argument("--source-id", help="Stable provenance identifier")
    parser.add_argument("--source-url", default="", help="Optional public source URL")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--llm-model", help="Call an OpenAI-compatible model")
    source.add_argument("--llm-proposal", type=Path,
                        help="Use a previously generated JSON/text response")
    parser.add_argument("--human-approved", action="store_true",
                        help="Explicitly approve the proposal for Stage 2")
    parser.add_argument("--reviewer", default="", help="Human reviewer name or identifier")
    parser.add_argument("--review-notes", default="", help="Human corrections/decision notes")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_text = args.source.read_text(encoding="utf-8")
    source_id = args.source_id or args.source.stem
    prompt = extraction_prompt(source_text, source_id, args.source_url)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "stage1_extraction_prompt.md").write_text(prompt, encoding="utf-8")

    if not args.llm_model and not args.llm_proposal:
        print(f"Wrote extraction prompt to {args.out_dir / 'stage1_extraction_prompt.md'}")
        print("Obtain an LLM proposal, review it, then rerun with --llm-proposal.")
        return

    raw_response = (
        call_llm(args.llm_model, SYSTEM_PROMPT, prompt)
        if args.llm_model
        else args.llm_proposal.read_text(encoding="utf-8")
    )
    (args.out_dir / "stage1_llm_response.txt").write_text(raw_response, encoding="utf-8")
    proposal = extract_json_object(raw_response)
    write_json(args.out_dir / "stage1_llm_proposal.json", proposal)

    errors = validate_proposal(proposal)
    record = {
        "stage": "collection_and_normalization",
        "method": "llm-assisted, human-reviewed",
        "source_id": source_id,
        "source_url": args.source_url,
        "llm_model": args.llm_model or "external/pre-generated",
        "human_approved": bool(args.human_approved),
        "reviewer": args.reviewer,
        "review_notes": args.review_notes,
        "proposal_errors": errors,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(args.out_dir / "stage1_record.json", record)

    if errors:
        raise SystemExit("LLM proposal is structurally invalid: " + "; ".join(errors))
    if not args.human_approved:
        print("Saved the LLM proposal, but did not emit a candidate template.")
        print("A human must inspect it and rerun with --human-approved.")
        return

    template = proposal["template"]
    template["pipeline_record"] = record
    candidate_name = slugify(str(template.get("scenario", source_id)))
    candidate_dir = args.out_dir / candidate_name
    candidate_dir.mkdir(parents=True, exist_ok=True)
    write_json(candidate_dir / f"{candidate_name}.json", template)
    (candidate_dir / "params.yaml").write_text(
        yaml.safe_dump(proposal.get("params_values", {}), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    write_json(candidate_dir / "stage1_record.json", record)
    print(f"Human-approved candidate written to {candidate_dir}")


if __name__ == "__main__":
    main()
