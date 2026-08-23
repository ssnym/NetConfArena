# Task Parameterization and Test-Case Authoring Guide

This guide describes how to turn a fixed network configuration exercise into a
seeded NetConfArena template and how to express evaluator assertions explicitly.
The process is LLM-assisted and human-reviewed; generated text is never treated
as validation evidence.

## Design Principles

Parameterization varies both the physical presentation and logical addressing
of a task while preserving its intended behavior. At minimum, parameterize
device names, interface allocation, IP subnets, and prefix lengths. Goals decide
which additional protocol values must vary, for example administrative
distance, OSPF cost, BGP AS number, route target, or tunnel identifier.

Read the goals and test cases before choosing parameters. Every parameter range
must preserve feasibility and the property being evaluated. Operational details
that do not affect task semantics, such as console ports and image idle-PC
values, belong in the environment rather than the template.

Use explicit test operators. Examples:

- an absent route or configuration fragment: `{"op": "empty"}`;
- required output entries: `{"op": "contains_all", "value": [...]}`;
- a numeric threshold: `{"op": ">=", "value": 2}`;
- every output row matches one pattern:
  `{"op": "all_match", "value": {...}}`;
- every row belongs to an allowed set:
  `{"op": "all_match", "value": [{...}, {...}]}`.

## Relevant Files

```text
task/
  all_tasks.json
  task_loader.py
  router_capabilities.yaml
  <NN>.<Task Name>/
    <NN>.<Task Name>.json
    params.yaml
evaluator/
  evaluate_task.py
  parse_cli_output.py
  utils.py
scripts/pipeline/
  stage2/
  stage3/
```

`task_loader.py` resolves parameters, generates the physical topology, and
renders placeholders. `router_capabilities.yaml` contains reusable platform,
slot, module, port, media, and interface-name knowledge. Do not repeat those
fixed platform facts in each task's `params.yaml`.

## `params.yaml`

Store semantic candidate lists and address pools in `params.yaml`; the JSON
template refers to them by key.

```yaml
r1_names: [NewYork, Boston, Chicago]
r2_names: [Washington, Atlanta, Portland]

r1_loopback_pool: "1.1.0.0/24"
r1_loopback_prefix_lens: [28, 29, 30]

link1_pool: "192.168.0.0/16"
link1_prefix_lens: [24, 25, 26]
link1_media_options: [ethernet, serial]

backup_distances: [10, 20, 50, 100, 150, 200]
```

Tie every pool to a numbered node or link. Prefer `r2_loopback_pool` and
`link1_pool` over ambiguous names such as `loopback_pool` or names derived from
a scenario-specific router label. A fixed /30 link should expose `[30]`, not a
wider range that changes the task.

## Parameter Types

### `choice`

Select one value from a literal list or a `params.yaml` key. `different_from`
excludes already resolved values.

```json
"router1_name": {"type": "choice", "options": "r1_names"},
"router2_name": {
  "type": "choice",
  "options": "r2_names",
  "different_from": ["router1_name"]
}
```

### `same_as`

Copy an already resolved parameter when two semantic values must be identical.

```json
"backup_distance_display": {
  "type": "same_as",
  "param": "backup_distance"
}
```

Do not use `same_as` to bind link-end modules. The physical topology generator
selects compatible media automatically.

### `random_subnet`

Allocate a subnet from a pool. `no_overlap` names previously resolved subnets
that must remain disjoint.

```json
"_link1": {
  "type": "random_subnet",
  "pool": "link1_pool",
  "prefix_len": "link1_prefix_lens"
},
"_link2": {
  "type": "random_subnet",
  "pool": "link2_pool",
  "prefix_len": "link2_prefix_lens",
  "no_overlap": ["_link1"]
}
```

### `subnet_attr`

Extract a network, netmask, prefix length, or host address from a resolved
subnet. Host indexes are one-based.

```json
"link1_network": {
  "type": "subnet_attr", "subnet": "_link1", "attr": "network"
},
"link1_mask": {
  "type": "subnet_attr", "subnet": "_link1", "attr": "netmask"
},
"link1_prefix_len": {
  "type": "subnet_attr", "subnet": "_link1", "attr": "prefixlen"
},
"link1_ip1": {
  "type": "subnet_attr", "subnet": "_link1", "attr": "host", "index": 1
}
```

### `format`

Construct a string from resolved values with single-brace placeholders.

```json
"description": {
  "type": "format",
  "template": "{router1_name}-{router2_name}"
}
```

## Naming Conventions

| Purpose | Form | Example |
|---|---|---|
| Internal value | leading underscore | `_link1` |
| Rendered node name | `router{n}_name` | `router1_name` |
| Node-name candidates | `r{n}_names` | `r1_names` |
| Internal link subnet | `_link{n}` | `_link1` |
| Link attribute | `link{n}_{attr}` | `link1_network` |
| Link pool | `link{n}_pool` | `link1_pool` |
| Link prefix choices | `link{n}_prefix_lens` | `link1_prefix_lens` |
| Link medium | `link{n}_media` | `link1_media` |
| Medium choices | `link{n}_media_options` | `link1_media_options` |
| Loopback pool | `r{n}_loopback_pool` | `r1_loopback_pool` |
| Generated interface | `router{n}_link{k}_iface` | `router1_link1_iface` |

Generated slot, module, port, and interface values may be referenced from
configs, goals, and tests, but they are intentionally omitted from the public
`resolved_params` output.

## Topology Schema

### `topology_model`

Describe only the abstract graph: node IDs, link IDs, endpoints, and required
media. Do not put coordinates, slots, port numbers, or rendered names here.

```json
"topology_model": {
  "nodes": ["r1", "r2"],
  "links": [
    {"id": "link1", "nodes": ["r1", "r2"], "media": "{{link1_media}}"},
    {"id": "link2", "nodes": ["r1", "r2"], "media": "{{link2_media}}"}
  ]
}
```

### `topology_attributes`

Attach implementation and display attributes without repeating connectivity.

```json
"topology_attributes": {
  "nodes": {
    "r1": {"name": "{{router1_name}}", "type": "C3600", "x": -200, "y": 0},
    "r2": {"name": "{{router2_name}}", "type": "C3600", "x": 200, "y": 0}
  }
}
```

The loader selects compatible modules, assigns free slots and ports, derives
interface names, and emits the physical `topology` consumed by GNS3. Every
logical link must use a medium supported at both endpoints. A slot must provide
enough ports for all links assigned to its module.

Current templates express node/link graphs and media requirements. More complex
hardware constraints should be added to `router_capabilities.yaml` or the
loader, not encoded as per-task physical plans.

## Test-Case Assertions

A test unit normally has an ID, node, command, and explicit expected result:

```json
{
  "id": "2-1",
  "node": "{{router2_name}}",
  "command": "show ip route {{target_network}} {{target_mask}}",
  "expected_result": {
    "op": "contains_all",
    "value": [{"protocol": "ospf", "network": "{{target_prefix}}"}]
  }
}
```

Supported operators include:

| Operator | Meaning |
|---|---|
| `==`, `!=` | Exact equality or inequality |
| `contains`, `not_contains` | Membership or substring relation |
| `contains_all` | Actual output contains every expected entry |
| `all_match` | Every actual row matches one allowed pattern |
| `empty`, `not_empty` | Empty or non-empty output |
| `>`, `>=`, `<`, `<=` | Numeric comparison |
| `regex` | Regular-expression match |

Use one goal-level ID prefix consistently, such as `3-1`, `3-2`, and `3-3`.
The evaluator averages results by that prefix. Do not share a prefix between
goals or split one goal across prefixes.

Use `empty` for absence, including configuration constraints. Do not use
`null` or `[]` as an implicit absence assertion. Use `contains_all` for
unordered required rows, and `all_match` when every returned row must satisfy a
condition. Use implicit comparison modes only when the required relation cannot
be represented by an explicit operator.

## Protocol and Platform Pitfalls

- Do not use switching-only modules such as `NM-16ESW` for routed links.
- Summary address pools must remain inside the configured summary range.
- OSPF cost-sensitive tasks must restrict media and bandwidth choices so the
  intended preferred path remains invariant.
- Router-ID and virtual-link tasks must prevent random interface addresses from
  changing election outcomes accidentally.
- RIP classful behavior can change which `network` statements are valid; choose
  pools accordingly.
- Serial-only and Ethernet-only features must declare matching media options.
- IOS may omit the mask from a BGP `network` line when it equals the classful
  default. Write an explicit prefix when a Class A or B subnet must not be
  interpreted at its classful length.
- `show running-config` validates configured method; routing and protocol show
  commands validate operational outcome. Use both when the task constrains the
  method as well as the result.

## Authoring Workflow

1. Read every goal and existing test before editing the template.
2. Enumerate semantic parameters and safe candidate ranges.
3. Define unique pools and constraints in `params.yaml`.
4. Model the abstract graph in `topology_model`.
5. Add node platform/display data in `topology_attributes`.
6. Replace semantic literals with `{{parameter}}` placeholders.
7. Express every executable test with an explicit operator and consistent ID
   prefix.
8. Render several seeds and inspect names, addresses, physical interfaces,
   configs, and tests.
9. Run reference configurations in GNS3 and require all executable checks to
   pass before approval.
10. Regenerate suite statistics after changing registered tasks.

Useful commands are documented in `scripts/pipeline/README.md`. Generated
instances, model drafts, validation reports, and probe output belong under
`pipeline_work/` or `logs/`, never inside the canonical task directories.
