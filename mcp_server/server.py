#!/usr/bin/env python3
"""Expose NetConfArena's five-action agent-network interface through MCP.

Exactly five tools are registered:

    get_running_config
    apply_config
    execute_validation
    wait
    submit

Task discovery, assignment, evaluation, persistence, and teardown are harness
responsibilities rather than agent actions. The harness selects one task when
starting the server with ``NETCONFARENA_TASK_INDEX`` (1-based) and optionally
``NETCONFARENA_TASK_SEED``. It must give the rendered scenario, topology, and
goals to the agent separately as its task prompt.

``submit`` terminates the agent episode, executes the hidden evaluator, writes
the trajectory and evaluation to a result JSON, and returns only a terminal
status. Hidden test outcomes are therefore never exposed as an action the agent
can use for iterative feedback.
"""

import json
import os
import re
import sys
import time
from typing import Any

import anyio

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(REPO_ROOT, ".env"))

# The ergonomic server class was renamed from FastMCP (mcp 1.x) to MCPServer
# (mcp 2.x); the decorator and run() API are the same.
try:
    from mcp.server.mcpserver import MCPServer as _ServerClass
except ImportError:  # pragma: no cover - depends on the installed mcp version
    from mcp.server.fastmcp import FastMCP as _ServerClass

from environment.task_environment import TaskEnvironment
from evaluator.evaluate_task import Evaluator
from task.task_loader import render_task


server = _ServerClass(
    name="netconfarena",
    instructions=(
        "NetConfArena provides exactly five actions for a task already assigned "
        "by the evaluation harness: get_running_config, apply_config, "
        "execute_validation, wait, and submit. Use the scenario, topology, and "
        "goals supplied in the task prompt. Call submit once the goals are "
        "satisfied. Hidden evaluation is performed only after submission."
    ),
)


class Episode:
    """One harness-selected task, its environment, and its action trajectory."""

    def __init__(self, task_index: int, seed: int | None, trial: int):
        self.task_index = task_index
        self.seed = seed
        self.trial = trial
        self.started_at = time.time()
        self.task_data = render_task(task_index - 1, randomize=True, seed=seed)
        self.scenario = self.task_data["scenario"]
        self.trajectory: list[dict[str, Any]] = []
        self.submitted = False
        self.closed = False
        self.evaluation: dict[str, Any] | None = None
        self.env = TaskEnvironment(task_data=self.task_data)

    @property
    def interface(self):
        return self.env.agent_interface

    def record(self, action: str, arguments: Any, observation: Any) -> None:
        self.trajectory.append({
            "turn": len(self.trajectory) + 1,
            "Action": action,
            "Arguments": arguments,
            "Observation": observation,
        })

    def close(self) -> None:
        if not self.closed:
            self.env.close()
            self.closed = True


_episode: Episode | None = None


def _require_episode() -> Episode:
    if _episode is None:
        raise RuntimeError(
            "the MCP server has no configured task; set "
            "NETCONFARENA_TASK_INDEX before starting it"
        )
    if _episode.submitted:
        raise RuntimeError("the task has already been submitted")
    return _episode


async def _blocking(func, *args):
    """Run a blocking emulator operation without blocking the MCP event loop."""
    return await anyio.to_thread.run_sync(func, *args)


def _parse_positive_int(name: str, default: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        if default is None:
            raise ValueError(f"{name} is required")
        return default
    value = int(raw)
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _parse_optional_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return int(raw)


def _safe_path_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", value).strip() or "task"


def _result_path(episode: Episode) -> str:
    configured = os.environ.get("NETCONFARENA_RESULT_PATH")
    if configured:
        return configured if os.path.isabs(configured) else os.path.join(REPO_ROOT, configured)

    seed_tag = "random" if episode.seed is None else str(episode.seed)
    scenario = _safe_path_component(episode.scenario)
    return os.path.join(
        REPO_ROOT,
        "results",
        f"{episode.task_index:02d}.{scenario}",
        "mcp-client",
        "MCP",
        f"seed_{seed_tag}_trial_{episode.trial}.json",
    )


def _evaluate_and_persist(episode: Episode) -> str:
    evaluator = Evaluator()
    evaluator.evaluate_task(episode.env.evaluator_interface, episode.task_data)
    episode.evaluation = evaluator.task_evaluation_result

    path = _result_path(episode)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    payload = {
        "scenario": episode.scenario,
        "task_index": episode.task_index,
        "llm_model": os.environ.get("NETCONFARENA_MCP_MODEL", "mcp-client"),
        "mode": "MCP",
        "seed": episode.seed,
        "trial": episode.trial,
        "agent_duration_seconds": time.time() - episode.started_at,
        "resolved_params": episode.task_data.get("resolved_params", {}),
        "trajectory": episode.trajectory,
        "evaluation_data": episode.evaluation,
    }
    with open(path, "w", encoding="utf-8") as result_file:
        json.dump(payload, result_file, indent=2, ensure_ascii=False)
    return path


@server.tool()
async def get_running_config(devices: list[str]) -> dict[str, Any]:
    """Return the full running configuration of the named devices."""
    episode = _require_episode()
    result = await _blocking(episode.interface.get_running_config, devices)
    episode.record("get_running_config", {"devices": devices}, result.get("observation"))
    return result


@server.tool()
async def apply_config(config_commands: dict[str, str]) -> dict[str, Any]:
    """Apply newline-separated CLI configuration commands on named devices."""
    episode = _require_episode()
    result = await _blocking(episode.interface.apply_config, config_commands)
    episode.record("apply_config", config_commands, result.get("observation"))
    return result


@server.tool()
async def execute_validation(validation_commands: dict[str, str]) -> dict[str, Any]:
    """Run read-only show, ping, or traceroute commands on named devices."""
    episode = _require_episode()
    result = await _blocking(episode.interface.execute_validation, validation_commands)
    episode.record("execute_validation", validation_commands, result.get("observation"))
    return result


@server.tool()
async def wait(seconds: int) -> dict[str, Any]:
    """Wait for protocol convergence after configuration changes."""
    episode = _require_episode()
    result = await _blocking(episode.interface.wait, seconds)
    episode.record("wait", {"seconds": seconds}, result.get("observation"))
    return result


@server.tool()
async def submit() -> dict[str, Any]:
    """End the episode; hidden evaluation and result persistence follow."""
    episode = _require_episode()
    interface_result = episode.interface.submit()
    episode.submitted = True
    episode.record("submit", {}, interface_result.get("status"))

    try:
        await _blocking(_evaluate_and_persist, episode)
    finally:
        await _blocking(episode.close)

    # The benchmark result remains hidden from the agent action interface. The
    # harness reads NETCONFARENA_RESULT_PATH (or the documented default path).
    return {"status": "Finished"}


def main() -> None:
    global _episode

    try:
        task_index = _parse_positive_int("NETCONFARENA_TASK_INDEX")
        seed = _parse_optional_int("NETCONFARENA_TASK_SEED")
        trial = _parse_positive_int("NETCONFARENA_TRIAL", default=1)
        _episode = Episode(task_index=task_index, seed=seed, trial=trial)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"Invalid MCP task configuration: {exc}") from exc

    transport = os.environ.get("NETCONFARENA_MCP_TRANSPORT", "stdio")
    try:
        server.run(transport=transport)
    finally:
        if _episode is not None:
            _episode.close()


if __name__ == "__main__":
    main()
