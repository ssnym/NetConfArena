"""Shared helpers for the task-construction pipeline."""

import json
import os
import re
from pathlib import Path
from typing import Any


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from a plain or fenced LLM response."""
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", candidate, re.S | re.I)
    if fenced:
        candidate = fenced.group(1)
    else:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start:end + 1]
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must contain one JSON object")
    return parsed


def call_llm(model: str, system_prompt: str, user_prompt: str) -> str:
    """Call an OpenAI-compatible endpoint using the repository environment."""
    from openai import OpenAI

    kwargs: dict[str, Any] = {}
    base_url = os.environ.get("LLM_BASE_URL") or os.environ.get("VLLM_SERVER_URL")
    if base_url:
        kwargs["base_url"] = base_url
        kwargs["api_key"] = os.environ.get("LLM_API_KEY", "EMPTY")
    elif os.environ.get("LLM_API_KEY"):
        kwargs["api_key"] = os.environ["LLM_API_KEY"]

    client = OpenAI(**kwargs)
    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("LLM returned an empty response")
    return content


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._ -]+", "_", value).strip(" ._")
    return slug or "candidate"


def parse_seeds(raw: str) -> list[int]:
    seeds = [int(item) for item in re.split(r"[\s,]+", raw.strip()) if item]
    if not seeds:
        raise ValueError("at least one seed is required")
    return seeds
