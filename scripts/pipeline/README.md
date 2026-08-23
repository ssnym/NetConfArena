# LLM-Assisted Task-Construction Pipeline

This directory contains the current three-stage task-construction pipeline.
It is not an end-to-end automatic generator: an LLM may propose or critique
content, humans approve task semantics, and emulator execution with deterministic
test cases provides behavioral evidence.

## Layout

```text
scripts/pipeline/
├── stage1/   # task collection and normalization; Actions 1–3
├── stage2/   # validation and refinement; Actions 4–5 plus validation tools
├── stage3/   # parameterization, instantiation, and rendered-instance review
├── docs/     # task-authoring conventions
├── common.py
└── README.md
```

Run Python entry points as modules from the repository root. Generated files,
model responses, reports, and rendered review copies belong under the ignored
`pipeline_work/` directory, not under `task/`.

## Pipeline Actions

| Action | Stage | Current implementation |
|---|---|---|
| 1. Collect source labs and create candidates | Stage 1 | `stage1/step_1_download_data_and_create_task.py` discovers GNS3Vault archive data, parses `.gns3`/`.net` projects, discovers configurations, and writes candidate inventories. |
| 2. Filter unsupported candidates | Stage 1 | `stage1/step_2_delete_invalid_task.py` combines exclusion lists with an optional human-maintained list. |
| 3. Normalize candidate JSON | Stage 1 | `stage1/step_3_modify_task_json.py` normalizes source keys and creates visible test-case shells for human completion. It writes review copies and never overwrites source data. |
| 4. Generate an optional reasoning draft | Stage 2 | `stage2/step_4_generate_thought.py` writes a non-authoritative LLM draft for human review. |
| 5. Run manual and emulator checks | Stage 2 | `stage2/step_5_manual_check.py` runs emulator/evaluator checks or optional Batfish checks and writes evidence for human inspection. |

Stage 3 uses `stage3/parameterize_instantiate.py` to render approved templates
deterministically for specified seeds.

The gated drivers are:

- `stage1/collect_normalize.py`: prepares an LLM proposal and requires explicit
  human approval before emitting a candidate;
- `stage2/validate_refine.py`: combines static checks, optional LLM critique,
  human disposition, and optional GNS3 evidence. LLM feedback is recorded but
  never applied automatically.

Benchmark execution does not invoke collection or task-construction code.

## Stage 1: Collection and Normalization

Use a local checkout of the public GNS3Vault archive:

```bash
python -m scripts.pipeline.stage1.step_1_download_data_and_create_task \
  --archive /path/to/gns3vault-archive \
  --work-dir pipeline_work/gns3vault

python -m scripts.pipeline.stage1.step_2_delete_invalid_task \
  --candidate-dir pipeline_work/gns3vault/raw_candidates \
  --manual-exclusions pipeline_work/gns3vault/manual_exclusions.json

python -m scripts.pipeline.stage1.step_3_modify_task_json \
  --task-list pipeline_work/gns3vault/raw_candidates/valid_tasks.json \
  --materialize-configs \
  --out-dir pipeline_work/gns3vault/normalized_candidates
```

An optional website input is also available:

```bash
# Set GNS3VAULT_COOKIE privately in the shell if the site requires it.
python -m scripts.pipeline.stage1.step_1_download_data_and_create_task \
  --website --work-dir pipeline_work/gns3vault
```

Use website collection only when authorized. No credential is bundled, logged,
or copied into candidate JSON. Public access to an upstream archive is not a
license grant; review upstream terms and [NOTICE](../../NOTICE) before
redistributing derived material.

Action 3 deliberately stops at a normalized review candidate. A human must
verify provenance, intent, topology, configuration, parameterization, and
executable tests before the candidate enters Stage 2.

The gated Stage 1 driver can generate a prompt without calling a model:

```bash
python -m scripts.pipeline.stage1.collect_normalize \
  --source pipeline_work/source_notes.md \
  --source-id example-01 \
  --source-url https://example.org/source \
  --out-dir pipeline_work/example-01
```

After reviewing a model response, record explicit human approval:

```bash
python -m scripts.pipeline.stage1.collect_normalize \
  --source pipeline_work/source_notes.md \
  --source-id example-01 \
  --out-dir pipeline_work/example-01 \
  --llm-proposal pipeline_work/example-01/model-response.txt \
  --human-approved --reviewer reviewer-id \
  --review-notes "Topology and objectives checked against the source."
```

Use `--llm-model MODEL` instead of `--llm-proposal` to call the configured
OpenAI-compatible endpoint.

## Stage 2: Validation and Refinement

Prepare an optional reasoning draft:

```bash
python -m scripts.pipeline.stage2.step_4_generate_thought \
  --task pipeline_work/example-01/Candidate/Candidate.json \
  --model MODEL
```

Run static review without starting GNS3:

```bash
python -m scripts.pipeline.stage2.validate_refine \
  --template pipeline_work/example-01/Candidate/Candidate.json \
  --seeds "1 2 3" --human-status needs-revision
```

After human correction, collect emulator evidence with either interface:

```bash
python -m scripts.pipeline.stage2.step_5_manual_check \
  --task pipeline_work/example-01/Candidate/Candidate.json --seed 1

python -m scripts.pipeline.stage2.validate_refine \
  --template pipeline_work/example-01/Candidate/Candidate.json \
  --seeds "1 2 3" --emulate \
  --human-status approved --reviewer reviewer-id
```

The integrated driver records `approved` only when emulation and executable
tests pass. Optional LLM feedback remains non-authoritative.

Released templates can be inspected with:

```bash
python -m scripts.pipeline.stage2.validate_task --task_index 0 --seed 1
bash scripts/pipeline/stage2/validate_all.sh
python -m scripts.pipeline.stage2.probe_task --task_index 0 --seed 1 \
  --cmd "router1:show ip route"
```

## Stage 3: Parameterization and Instantiation

Instantiate a template only after Stage 2 approval:

```bash
python -m scripts.pipeline.stage3.parameterize_instantiate \
  --template pipeline_work/example-01/Candidate/Candidate.json \
  --seeds "1 2 3 4 5" \
  --out-dir pipeline_work/example-01/instances
```

The manifest records the template hash, seeds, output paths, and whether
resolved parameterizations are distinct. Rendered instances are review
artifacts; the canonical dataset remains the parameterized template.

To inspect the prompts and resolved data for a registered template:

```bash
python -m scripts.pipeline.stage3.render_review --task_index 0 --seed 1
```

See [TASK_AUTHORING_GUIDE.md](docs/TASK_AUTHORING_GUIDE.md) for the current
template schema and authoring rules.
