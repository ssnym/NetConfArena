# NetConfArena

An executable benchmark for LLM agents in closed-loop network configuration.

This repository defines the current task inventory, taxonomy, pipeline,
interfaces, and evaluation behavior.

NetConfArena runs an agent against a **real network emulator**. Each task gives
the agent a multi-device topology with a startup configuration and a set of
goals stated in operator language; the agent must reach those goals by issuing
vendor CLI configuration through a five-action interface, observing what the
devices actually report back, and iterating. Scoring is not a text comparison
and not an LLM judge: hidden test cases execute diagnostic commands on the
emulated devices and evaluate the output with deterministic predicates.

- **96 task templates** across static routing, RIP, OSPF, BGP, MPLS (LDP,
  TE, L3VPN including MPLS over GRE, L2VPN) and IP overlays (L2TPv3, GRE,
  DMVPN, IPsec).
- **Parameterized instantiation** — every template renders into many instances
  with different device names, interfaces and IP addressing, from a seed.
- **416 scoring groups / 1109 executable checks** over the whole suite.
- **Trajectory logging** for process-level analysis, plus scripts that
  reproduce the reported metrics.
- **MCP server** exposing the same interface, so other agent frameworks can run
  the benchmark without reimplementing the harness.

---

## Contents

| Path | What it is |
|---|---|
| [main.py](main.py) | Run one agent on one task instance and evaluate it |
| [run.sh](run.sh) | Batch driver over task ranges, seeds and trials |
| [agent/](agent/) | Agent harnesses (`ReAct`, `OneShot`) and their prompts |
| [environment/](environment/) | GNS3 orchestration, device console I/O, agent and evaluator interfaces |
| [evaluator/](evaluator/) | Test-case execution, CLI output parsing, configuration diffing |
| [task/](task/) | The 96 task templates, runtime loader, statistics, and taxonomy |
| [scripts/](scripts/) | Three-stage task-construction pipeline, characterization and result aggregation |
| [mcp_server/](mcp_server/) | MCP packaging of the agent-network interface |

---

## Environment setup

### 1. Emulator

NetConfArena drives **GNS3 2.2.x** over its REST API (developed against
2.2.55). Install the GNS3 server (and, optionally, the GUI) following the
[GNS3 documentation](https://docs.gns3.com/). The server must be reachable at
`http://$GNS3_SERVER:3080`.

### 2. Device images

The task templates use Dynamips Cisco IOS platforms. **These images are
proprietary Cisco software and are not distributed with this repository** —
supply your own under your own Cisco license:

| Platform used by tasks | Image used during development | GNS3 template name expected |
|---|---|---|
| `C3600` (tasks 1–17, 19–59) | `c3640-a3js-mz.124-25d` | `Cisco 3640 124-25d` |
| `C7200` (task 18, tasks 60–96) | `c7200-adventerprisek9-mz.124-24.T5` | `Cisco 7200 124-24.T5` |
| `EthernetSwitch` (tasks 17, 23, 38) | built into GNS3 | `Ethernet switch` |

Templates are resolved by name **or** by Dynamips platform string, so a
template named differently still works as long as its platform is `c3600` /
`c7200` (see `get_template_id` in
[environment/gns3_project.py](environment/gns3_project.py)). The interface
names and adapter models the loader assumes for each platform are declared in
[task/router_capabilities.yaml](task/router_capabilities.yaml); if you use a
different platform, add a `platforms:` entry there.

The templates also expect the platform's adapters to be installable in the
slots listed in that file (for C3600: `NM-4E`, `NM-1FE-TX`, `NM-4T`; for
C7200: `PA-2FE-TX`, `PA-4T+`, `PA-GE`, `PA-4E`). Dynamips also needs an idle-PC
value per image for reasonable CPU usage — set it in the GNS3 template.

### 3. Python dependencies

Python 3.10+ (developed on 3.13). Install in **two steps** — `gns3fy` must come
last and without its dependencies:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install --no-deps "gns3fy>=0.8.0,<0.9"
```

`gns3fy` 0.8.0 declares `pydantic<2` but works against GNS3 2.2.x with
pydantic 2.x, which the rest of the stack needs. Installing it with its
dependencies would downgrade pydantic and break those packages.

For the optional MCP server, use `pip install -r requirements-mcp.txt` in place
of the first command.

### 4. Configuration

```bash
cp .env.example .env
$EDITOR .env          # GNS3 address and projects path, model endpoint
```

| Variable | Meaning |
|---|---|
| `GNS3_SERVER` | GNS3 server address (default `127.0.0.1`) |
| `GNS3_PROJECT_PATH` | GNS3 projects directory (default `~/GNS3/projects`) |
| `LLM_BASE_URL` | OpenAI-compatible endpoint; set for self-hosted models. `VLLM_SERVER_URL` is accepted as an alias |
| `LLM_API_KEY` | Key for that endpoint (`EMPTY` is fine for most self-hosted servers) |
| `OPENAI_API_KEY` | Used when `LLM_BASE_URL` is unset |

All entry points load `.env` automatically. Run commands from the repository
root.

---

## Quickstart

### Check your setup without an LLM

Deploy one task, apply its reference configuration, and score it. This
exercises the whole stack — topology creation, console I/O, convergence
detection, test-case execution — and needs no model:

```bash
python -m scripts.pipeline.stage2.validate_task --task_index 0 --seed 1
```

`--task_index` here is **0-based** (an index into `task/all_tasks.json`). A
healthy setup ends with `score: 1.0`. Expect a few minutes per task: Dynamips
routers boot slowly and the evaluator waits for routing to converge.

To sweep a range of templates with several seeds each:

```bash
TASK_START=0 TASK_END=9 SEEDS="1 2 3" MAX_JOBS=1 \
  bash scripts/pipeline/stage2/validate_all.sh
```

Keep `MAX_JOBS=1` unless your GNS3 host is comfortable with parallel Dynamips
instances; concurrent projects can collide on UDP NIO ports.

### Run an agent on one task

```bash
python main.py --mode ReAct --llm_model gpt-4o-mini \
               --task_index 5 --seed 1 --trial 1
```

`--task_index` for `main.py` is **1-based** (task 5 = `05.RIP Filtering`).
Results land in
`results/05.RIP Filtering/<model>/ReAct/seed_1_trial_1.json`, next to a log of
the same run. The JSON holds the resolved parameters, the full trajectory, and
the evaluation.

`--seed` fixes the instance. The same template and seed always render the same
device names, interfaces and addressing, which is what makes a reported number
reproducible; omit it and you get a fresh random instance.

### Batch runs

```bash
SEEDS="1 2 3 4 5" TRIALS=8 LLM_MODELS="gpt-4o-mini" ./run.sh 1 96
```

`run.sh --help` lists the environment variables it honours (mode, models,
reasoning channel, trials, seeds).

---

## The agent-network interface

An agent sees the scenario name, the goals, and the physical topology (devices
and the interface pairs of each link). Everything else must be observed. Five
actions are available; each returns a JSON observation:

| Action | Observation returned |
|---|---|
| `get_running_config(devices)` | The full running configuration of each named device |
| `apply_config({device: commands})` | The device CLI output of applying the commands — this is where syntax and semantic rejections surface |
| `execute_validation({device: commands})` | The output of `show` / `ping` / `traceroute`; anything else is refused |
| `wait(seconds)` | Confirmation that the wait elapsed, letting protocols converge |
| `submit()` | Ends the episode |

`get_current_config` and `wait_for_stabilization` are accepted as aliases of
`get_running_config` and `wait`. Actions are implemented in
[environment/agent_interface.py](environment/agent_interface.py); the `ReAct`
harness parses one `Action:` per turn and has a 20-turn budget by default
([agent/configs/ReAct.yaml](agent/configs/ReAct.yaml)).

---

## How scoring works

Each template ships hidden test cases grouped by goal. A test unit runs one
diagnostic command on one device and asserts a predicate over the parsed
output (`contains_all`, `all_match`, `empty`, `==`, `>=`, ...). Parsing is done
by [evaluator/parse_cli_output.py](evaluator/parse_cli_output.py); no model is
involved in judging.

- **Test case score** — for each scoring group (the test units sharing an id
  prefix, one group per goal), the fraction of units that pass; the run's score
  is the arithmetic mean over groups. Groups, not units, carry equal weight.
- **Task pass rate** — the fraction of instances in which *every* unit passes.
  A stricter, task-level view.
- **Configuration precision / recall** — the applied configuration diff against
  the reference configuration. Reported alongside the behavioural score, since
  a different-but-correct configuration can pass the tests.

Before running test cases, the evaluator waits for routing to converge: it
polls every device's routing table (global and per-VRF) and requires several
consecutive identical snapshots that already contain learned routes.

### Reproducing the reported metrics

```bash
python scripts/compute_task_stats.py --markdown        # task suite characterization
python scripts/aggregate_results.py --generalizability --failure-signals \
       --out analysis/metrics.json --csv analysis/runs.csv
```

`aggregate_results.py` reads every run JSON under `results/` and reports, per
model and harness: test case score, task pass rate, goal pass rate,
configuration precision/recall, interaction efficiency (turns, action mix,
budget exhaustion), and breakdowns by category, subcategory or complexity tier.

`--generalizability` adds the spread of performance across protocol categories,
across templates within a category, and across parameterizations (seeds) of the
same template. `--failure-signals` adds heuristic trajectory signals for
triage; the generated report explicitly marks them as non-authoritative.

---

## Task suite

Each template lives in its own directory:

```
task/05.RIP Filtering/
├── 05.RIP Filtering.json     # the template
└── params.yaml               # value pools for parameterization
```

The template JSON has these sections:

| Key | Meaning |
|---|---|
| `scenario` | Task name, shown to the agent |
| `goals` | The operator-language objectives, shown to the agent |
| `params` | Parameter schema: what varies per instance and how |
| `topology_model` | Logical nodes and links (`media` selects the physical port type) |
| `topology_attributes` | Per-node platform and per-link attributes |
| `startup_configs` | Pre-applied configuration, per device |
| `groundtruth_configs` | Reference solution, used for validation and for the configuration metrics — never shown to the agent |
| `testcases` | Goals with their executable test units — hidden from the agent |

Placeholders such as `{{router1_name}}` and `{{link1_ip1}}` are resolved by
[task/task_loader.py](task/task_loader.py) from `params` plus the pools in
`params.yaml`. Parameterization varies device names, interface names and IP
subnets while preserving the topology and the task semantics; it does not
generate new topologies.

Machine-readable characterization of the suite:

| File | Contents |
|---|---|
| [task/task_stats.json](task/task_stats.json) / [.csv](task/task_stats.csv) | Per-template statistics: devices, links, goals, scoring groups, test units, reference-configuration commands, detected protocol layers, complexity tier |
| [task/task_taxonomy.json](task/task_taxonomy.json) | Category / subcategory / complexity assignment of all 96 templates |
| [task/TASK_TAXONOMY.md](task/TASK_TAXONOMY.md) | The same taxonomy in prose, with the counting conventions spelled out |
| [task/readme.md](task/readme.md) | Dataset layout and runtime data-file roles |
| [scripts/pipeline/docs/TASK_AUTHORING_GUIDE.md](scripts/pipeline/docs/TASK_AUTHORING_GUIDE.md) | Template authoring conventions in depth |

`task/task_stats.json` is regenerated with `scripts/compute_task_stats.py`.

### Inspecting and debugging a template

```bash
python -m scripts.pipeline.stage3.render_review --task_index 4
python -m scripts.pipeline.stage2.validate_task --task_index 4 --seed 1 --dump
python -m scripts.pipeline.stage2.probe_task --task_index 4 --seed 1 \
    --cmd "router2:show ip route" --cmd "router2:show ip protocols"
```

`render_review.py` writes the exact prompts an agent would see plus the
instantiated configurations and test cases under `pipeline_work/`.
`probe_task.py` brings a topology up,
applies the reference configuration, and then runs arbitrary commands — the tool
to reach for when a test case fails and you need to see what the device thinks.

---

## Extending the benchmark

### A new task template

1. Create `task/<NN>.<Name>/<NN>.<Name>.json` and add its path to
   `task/all_tasks.json`.
2. Describe the topology in `topology_model` (logical nodes and links, each link
   with a `media`) and `topology_attributes` (per-node platform). Do not
   hard-code a rendered topology.
3. Declare everything that should vary in `params`, and put the value pools in
   `params.yaml` — device names, address pools, prefix lengths, media choices.
   Both ends of a link must use compatible media.
4. Write `startup_configs` and `groundtruth_configs` as command lists per
   device, using `{{placeholders}}` rather than literal names or addresses.
5. Write `testcases`: one entry per goal, each with test units carrying ids
   `<group>-<n>`, an explicit operator, and a command whose output the parser in
   `evaluator/parse_cli_output.py` understands.
6. Validate with
   `python -m scripts.pipeline.stage2.validate_task --task_index <0-based> --seed 1`;
   it must reach `score: 1.0` for several seeds. A reference configuration that
   does not pass its own test cases means the template — or the test case — is
   wrong.
7. Regenerate statistics: `python scripts/compute_task_stats.py`, and add the
   template to `task/task_taxonomy.json`.

[scripts/pipeline/docs/TASK_AUTHORING_GUIDE.md](scripts/pipeline/docs/TASK_AUTHORING_GUIDE.md)
documents the conventions in detail.

### A new device type or platform

Add a `platforms:` entry to
[task/router_capabilities.yaml](task/router_capabilities.yaml) giving the GNS3
template name, the Dynamips platform string, the slot range, and which adapter
modules provide which media, then reference it as a node `type` in
`topology_attributes`. Interface naming follows the module's
`interface_template`.

### A new protocol

Protocol support is bounded by two things: what the emulated image implements,
and what the evaluator can parse. If a `show` command's output has no parser in
`evaluator/parse_cli_output.py`, add one — the existing parsers show the
expected shape (a list of dicts, or `{"line": ...}` for `| include` filters).
Then express assertions over those fields in `testcases`.

### A new agent harness

Subclass `BaseAgent` (see [agent/react.py](agent/react.py)) and implement
`execute_task(task_index, interface, task_data)`, appending one entry per turn
to `self.trajectory`. Register it in `create_agent` in [main.py](main.py) and
add a prompt config under `agent/configs/`.

---

## MCP server

[mcp_server/server.py](mcp_server/server.py) exposes exactly the same five
agent actions as MCP tools: `get_running_config`, `apply_config`,
`execute_validation`, `wait`, and `submit`. Task discovery, assignment,
evaluation, result persistence, and teardown belong to the evaluation harness;
they are deliberately not agent-callable tools.

The harness selects a task out of band with a 1-based index and optional seed.
It can render the public prompt context (scenario, topology, goals, but no
ground truth or hidden tests) with:

```bash
python -m mcp_server.task_context --task-index 5 --seed 1 > /tmp/task-context.json
```

The harness gives that context to the agent and starts the server with matching
values:

```bash
pip install -r requirements-mcp.txt
NETCONFARENA_TASK_INDEX=5 NETCONFARENA_TASK_SEED=1 \
  python -m mcp_server.server
```

Client configuration:

```json
{
  "mcpServers": {
    "netconfarena": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/NetConfArena",
      "env": {"GNS3_SERVER": "127.0.0.1",
              "GNS3_PROJECT_PATH": "/home/<user>/GNS3/projects",
              "NETCONFARENA_TASK_INDEX": "5",
              "NETCONFARENA_TASK_SEED": "1"}
    }
  }
}
```

One server process holds exactly one harness-selected task. `submit` ends the
episode, runs hidden evaluation, writes a result JSON, and returns only a
terminal status to the agent. The harness reads the result from
`NETCONFARENA_RESULT_PATH`, or from the documented default under `results/`.
Consequently hidden test outcomes cannot become an iterative feedback action.

---

## LLM-assisted task-construction pipeline

The construction workflow is implemented under
[scripts/pipeline/](scripts/pipeline/) in three stages:

1. **Task collection and normalization.** Actions 1–3 collect GNS3 projects,
   parse `.gns3`/`.net`, classify and filter incomplete candidates, and
   normalize candidate JSON. An LLM may propose structured content from source
   notes. A human reviews provenance, intent, topology, configuration,
   parameterization, and uncertainties before approval.
2. **Task validation and refinement.** Actions 4–5 provide optional LLM
   reasoning and manual emulator/Batfish helpers. Static checks, human editing,
   emulator deployment, and deterministic test execution are combined. LLM
   reasoning is a review draft, not correctness evidence, and is never applied
   automatically.
3. **Task parameterization and instantiation.** Human-authored parameter schemas
   and constraints are rendered deterministically into seeded instances.

This is an **LLM-assisted, human-reviewed, emulator-grounded** pipeline, not an
end-to-end automatic task generator. Complete commands are in
[scripts/pipeline/README.md](scripts/pipeline/README.md). Source material and
intermediate model outputs stay under the ignored `pipeline_work/` directory.

The current template schema is documented in
[scripts/pipeline/docs/TASK_AUTHORING_GUIDE.md](scripts/pipeline/docs/TASK_AUTHORING_GUIDE.md). Existing
templates can be inspected and validated with the Stage 2/3 utilities under
`scripts/pipeline/`; suite statistics are regenerated with
`scripts/compute_task_stats.py`. Running the benchmark itself never downloads
third-party lab content. See [NOTICE](NOTICE) for the provenance of tasks 1–59.

---

## Validation status

A template is considered emulation-validated only after its reference
configuration has been deployed and has passed the template's own executable
test cases. All 96 released templates have completed that task-level validation.
Users should still perform the Quickstart setup check because image builds,
GNS3 templates, and host resources vary between installations.

---

## Attribution and third-party material

The included [LICENSE](LICENSE) applies to rights held by the NetConfArena
authors. It does not grant rights in third-party material.

[NOTICE](NOTICE) records the provenance of the task material: the network
scenarios behind tasks 1–59 are derived from the publicly archived
[GNS3Vault lab collection](https://github.com/networklessons/labs/tree/main/gns3vault-archive)
(now maintained by NetworkLessons) — reusing the technical substance, with all
templates, reference configurations and test cases re-authored here; tasks 60–96
are original. No file from that collection is redistributed. Public
availability of the upstream archive should not be interpreted as a grant to
copy or relicense its contents. GNS3 and the Cisco IOS images are third-party
software that this repository neither includes nor licenses.
