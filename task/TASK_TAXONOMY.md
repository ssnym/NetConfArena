# NetConfArena Task Taxonomy

This file reports reproducible statistics for the **96 templates registered**
in `task/all_tasks.json`. `Complexity` and `Tmpl.` are generated from the
current repository data.

Counting rules, reproducible from `task/all_tasks.json` and the task JSON files:

- **Tmpl.** is the number of templates in a subcategory. Every template can be
  instantiated with arbitrary seeds.
- **Nodes** is the range of rendered device counts in `topology_model.nodes`.
- **Goals** is the number of scoring groups used by
  `evaluator/evaluate_task.py`. The evaluator groups test IDs by prefix with
  `tid.split('-', 1)[0]`, computes each group pass rate, and averages the
  groups: `overall_score = sum(group_scores) / len(groups)`. A group, not an
  individual test unit, is therefore the scoring unit.
- **Checks** is the total number of executable test units in
  `testcases[].testcase[]`.
- **Complexity** uses these rules:
  - **L:** one protocol and reference changes on no more than two devices;
  - **M:** one protocol family coordinated across at least three devices, or an
    interaction between two protocols;
  - **H:** at least three stacked layers (IGP + label distribution + BGP/VPN),
    or multi-domain coordination across AS, administrative, or overlay bounds.

---

## Task Taxonomy

| Category | Subcategory | Feature | Complexity | Tmpl. | Nodes | Goals | Checks | Evaluation Target |
|---|---|---|:---:|---:|:---:|---:|---:|---|
| **Static Routing** | Basic Routing | Static, default, and floating routes | L | 1 | 2 | 4 | 4 | Route entries, next hops, AD-based failover, reachability |
| **RIP** | Fundamentals | RIPv2 unicast neighbors | L | 1 | 2 | 2 | 6 | Neighbor peering, passive interfaces, no multicast/broadcast updates |
| | Policy and Route Control | Summarization, offset-list, distribute-list, prefix-list, ACL | M | 3 | 4 | 13 | 34 | Aggregation, filtering, metric manipulation, path preference |
| **OSPF** | Fundamentals | Single-area operation, network statements, router ID, interface cost | L | 1 | 3 | 6 | 8 | Adjacency, route exchange, network-statement forms, cost |
| | Areas and Route Control | Stub/NSSA areas, summarization, DR/BDR, reference bandwidth | M | 8 | 2–7 | 31 | 79 | Area behavior, LSA visibility, summary routes, election and metric outcomes |
| | Advanced Area Design | Virtual links, forward-address suppression, Type-7/5 translation | H | 4 | 3–5 | 17 | 41 | Backbone continuity, transit-area behavior, translator selection |
| **BGP** | Peering, Propagation, and Scaling | iBGP/eBGP, update-source, transit, synchronization, soft reconfiguration, peer groups, route reflection, confederations | H | 12 | 2–9 | 55 | 144 | Session state, route propagation, next-hop reachability, scaling behavior |
| | Routing Policy and Filtering | Private-AS removal, AS limits, prefix/ACL/AS-path filters, communities, allow-AS-in | M | 11 | 2–5 | 53 | 138 | Route acceptance, export policy, community handling, loop-prevention exceptions |
| | Best-Path Selection and Traffic Engineering | Local AS, local preference, MED, weight, origin, AS path, router ID, DMZ bandwidth, backdoor | H | 13 | 2–7 | 79 | 196 | Best-path decisions, attribute manipulation, path preference, traffic steering |
| | Advertisement Control and Aggregation | Unsuppress-map, conditional advertisement, aggregation, AS_SET | M | 5 | 2–5 | 26 | 48 | Conditional export, suppression, summary generation, path-information retention |
| **MPLS** | LDP and Label Forwarding | LDP sessions, label policy, explicit null, static binding, TTL and label ranges | M | 12 | 3–4 | 37 | 107 | Adjacency, bindings, LFIB forwarding, session protection, label behavior |
| | Traffic Engineering | RSVP-TE, explicit paths, bandwidth admission, affinity, autoroute, FRR | H | 6 | 4 | 20 | 58 | CR-LSP setup, path constraints, reservations, protected forwarding |
| | L3VPN | VRF/RD/RT, VPNv4, PE-CE routing, hub-and-spoke, route reflection, Inter-AS, GRE-core transport | H | 9 | 4–6 | 37 | 121 | Route import/export, VPN isolation, control-plane and data-plane reachability |
| | L2VPN | VPWS, EoMPLS, AToM, pseudowire classes, Ethernet interworking, service aggregation | M | 4 | 5–6 | 13 | 48 | AC/PW binding, VC state, point-to-point L2 reachability, service isolation |
| **IP Overlay** | IP Tunnels | L2TPv3, GRE, keepalive, MTU/MSS adjustment, IPsec tunnel protection | M | 4 | 3–5 | 15 | 49 | Tunnel and pseudowire state, overlay routing, fragmentation control, encryption |
| | Dynamic Multipoint Overlay | DMVPN phases 1/2, mGRE, NHRP | H | 2 | 4 | 8 | 28 | Registration, hub relaying, shortcut forwarding, next-hop preservation |
| **Total** | 16 subcategories | — | — | **96** | 2–9 | **416** | **1109** | — |

### Why Goals Is 416 Rather Than 444

The task JSON files contain **444** `testcases` entries, but the evaluator
scores only **416** groups:

| | Entries | Explanation |
|---|---:|---|
| All `testcases` entries | 444 | |
| Minus goals without a test unit | 28 | Narrative or constraint-only text that is not scored |
| **Executable scoring groups** | **416** | `overall_score` averages these groups within each task |

The scoring-group count exactly matches the number of goal entries with
checks. Every group maps to one goal and uses a consistent ID prefix. No two
goals are silently merged under one prefix, and no goal is split across
prefixes, so group weights are neither inflated nor compressed accidentally.

All 28 entries without test units occur in tasks 01–59. Examples include the
task 04 constraint "You are only allowed to use offset-lists for this lab" and
narrative "Take a look at..." questions in task 55. They provide context to an
agent but make no scoring contribution.

### Method Constraints

Tasks 48 and 62–96 contain 52 assertions that check prohibited-method
constraints, usually by asserting absent configuration with `{"op": "empty"}`.
Each method constraint is an ordinary check in its ID-prefix group. A solution
with the right outcome but a prohibited method loses the corresponding score
fraction.

---

## Subcategory-to-Task Mapping

| Category | Subcategory | Task IDs |
|---|---|---|
| Static Routing | Basic Routing | 01 |
| RIP | Fundamentals | 02 |
| RIP | Policy and Route Control | 03, 04, 05 |
| OSPF | Fundamentals | 06 |
| OSPF | Areas and Route Control | 07, 08, 09, 10, 11, 12, 17, 18 |
| OSPF | Advanced Area Design | 13, 14, 15, 16 |
| BGP | Peering, Propagation, and Scaling | 19, 20, 21, 22, 24, 25, 26, 28, 29, 30, 37, 39 |
| BGP | Routing Policy and Filtering | 27, 31, 35, 36, 41, 42, 43, 44, 45, 54, 56 |
| BGP | Best-Path Selection and Traffic Engineering | 32, 33, 34, 38, 46, 47, 48, 49, 50, 51, 52, 53, 55 |
| BGP | Advertisement Control and Aggregation | 23, 40, 57, 58, 59 |
| MPLS | LDP and Label Forwarding | 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71 |
| MPLS | Traffic Engineering | 72, 73, 74, 75, 76, 77 |
| MPLS | L3VPN | 78, 79, 80, 81, 82, 83, 84, 85, 86 |
| MPLS | L2VPN | 87, 88, 89, 90 |
| IP Overlay | IP Tunnels | 91, 92, 93, 96 |
| IP Overlay | Dynamic Multipoint Overlay | 94, 95 |

The 16 subcategories contain exactly 96 unique task IDs, matching all 96
entries in `task/all_tasks.json` without omissions or duplicates.

## Validation Status

All 96 released templates have completed task-level emulator validation. Users
should still run the Quickstart check because images, GNS3 templates, and host
resources vary across installations.
