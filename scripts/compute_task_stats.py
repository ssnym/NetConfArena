#!/usr/bin/env python3
"""Compute per-template characterization statistics for the NetConfArena task suite.

For every template in ``task/all_tasks.json`` this script instantiates the
template with a fixed seed and reports:

  * topology size            — devices, links, configured interfaces, device models
  * task shape               — goal count, devices touched by the reference
                               configuration, reference configuration commands
  * evaluation size          — scoring groups (the unit the evaluator averages
                               over) and individual test units
  * protocol characterization — the set of protocol layers exercised
  * taxonomy placement      — category, subcategory and complexity tier, joined
                               from ``task/task_taxonomy.json``

The complexity tier follows the rule documented in ``task/TASK_TAXONOMY.md``:

  L  single protocol layer, reference configuration touches <= 2 devices
  M  single protocol family across >= 3 devices, or two interacting protocols
  H  >= 3 protocol layers stacked, or multi-AS / multi-domain / overlay
     coordination

``--recompute-complexity`` re-derives the tier from the measured features
instead of reading it from the taxonomy file, which checks that assigned tiers
still follow the rule.

Topology size is seed-independent (parameterization preserves topology), so the
numbers below are stable across instantiations; interface names and addresses
are not, and are therefore not reported.

Usage:
    python scripts/compute_task_stats.py                  # writes task/task_stats.{json,csv}
    python scripts/compute_task_stats.py --markdown       # also print a markdown table
    python scripts/compute_task_stats.py --seed 7
"""

import argparse
import csv
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from task.task_loader import render_task, ALL_TASKS_PATH  # noqa: E402


# Protocol layers, detected from the reference configuration. Order matters only
# for presentation. Each entry is (layer name, compiled pattern).
PROTOCOL_PATTERNS = [
    ("static",   r"^\s*ip route\s+\d"),
    ("rip",      r"^\s*router rip\b"),
    ("ospf",     r"^\s*router ospf\b"),
    ("eigrp",    r"^\s*router eigrp\b"),
    ("bgp",      r"^\s*router bgp\b"),
    ("mpls_ldp", r"^\s*(mpls ip\b|mpls ldp\b|mpls label\b|mpls static\b)"),
    ("mpls_te",  r"(mpls traffic-eng|tunnel mode mpls traffic-eng|^\s*ip rsvp bandwidth)"),
    ("l3vpn",    r"(^\s*ip vrf\b|^\s*vrf definition\b|address-family vpnv4|^\s*ip vrf forwarding\b)"),
    ("l2vpn",    r"(^\s*xconnect\b|^\s*pseudowire-class\b|^\s*l2 vfi\b|^\s*l2tp-class\b)"),
    ("gre",      r"tunnel mode gre|^\s*tunnel destination\b"),
    ("dmvpn",    r"^\s*ip nhrp\b|tunnel mode gre multipoint"),
    ("ipsec",    r"^\s*crypto\b|tunnel protection ipsec"),
    ("bfd",      r"^\s*bfd interval\b|bfd all-interfaces"),
]
PROTOCOL_PATTERNS = [(name, re.compile(pat, re.IGNORECASE)) for name, pat in PROTOCOL_PATTERNS]

TAXONOMY_PATH = os.path.join(REPO_ROOT, "task", "task_taxonomy.json")


def load_taxonomy():
    """task_index (1-based) -> {category, subcategory, complexity}."""
    if not os.path.exists(TAXONOMY_PATH):
        return {}
    with open(TAXONOMY_PATH) as f:
        data = json.load(f)
    placement = {}
    for entry in data.get("subcategories", []):
        for task_index in entry.get("tasks", []):
            placement[task_index] = {
                "category": entry["category"],
                "subcategory": entry["subcategory"],
                "complexity": entry["complexity"],
            }
    return placement


# Fallback category, derived from the template name, used only when a template
# is missing from task/task_taxonomy.json.
CATEGORY_RULES = [
    ("Static Routing", r"^static routing"),
    ("RIP",            r"^rip\b"),
    ("OSPF",           r"^ospf\b"),
    ("BGP",            r"^(bgp\b|ibgp|ebgp)"),
    ("MPLS L2VPN",     r"^(eompls|atom|l2tpv3)\b"),
    ("MPLS",           r"^mpls\b"),
    ("IP Overlay",     r"^(gre|dmvpn|ipsec)\b"),
]
CATEGORY_RULES = [(name, re.compile(pat, re.IGNORECASE)) for name, pat in CATEGORY_RULES]


def detect_category(scenario):
    for name, pattern in CATEGORY_RULES:
        if pattern.search(scenario):
            return name
    return "Other"


def detect_protocols(groundtruth_configs):
    found = []
    for commands in groundtruth_configs.values():
        for line in commands:
            for name, pattern in PROTOCOL_PATTERNS:
                if name not in found and pattern.search(line):
                    found.append(name)
    return found


def count_autonomous_systems(groundtruth_configs, startup_configs):
    """Distinct BGP AS numbers across the reference and startup configurations."""
    as_numbers = set()
    for configs in (groundtruth_configs, startup_configs):
        for commands in configs.values():
            for line in commands:
                match = re.match(r"\s*router bgp\s+(\d+)", line, re.IGNORECASE)
                if match:
                    as_numbers.add(match.group(1))
    return len(as_numbers)


def complexity_tier(protocols, devices_configured, autonomous_systems, scenario):
    """L / M / H, per the rule documented in task/TASK_TAXONOMY.md."""
    # Layers that count towards "protocol stacking". BFD is a helper, not a layer.
    layers = [p for p in protocols if p != "bfd"]
    multi_domain = (
        autonomous_systems >= 2
        or bool(re.search(r"inter-as|confederation|route reflector", scenario, re.IGNORECASE))
    )
    if len(layers) >= 3 or multi_domain:
        return "H"
    if len(layers) <= 1 and devices_configured <= 2:
        return "L"
    return "M"


def template_stats(index, seed, taxonomy, recompute_complexity=False):
    task_data = render_task(index, randomize=True, seed=seed)

    topology = task_data.get("topology", {})
    nodes = topology.get("nodes", [])
    links = topology.get("links", [])
    device_models = sorted({
        (n.get("type") or n.get("properties", {}).get("platform") or "unknown")
        for n in nodes
    })

    groundtruth = task_data.get("groundtruth_configs", {}) or {}
    startup = task_data.get("startup_configs", {}) or {}

    goals_text = task_data.get("goals", "") or ""
    goal_lines = [ln for ln in goals_text.splitlines() if ln.strip().startswith(("•", "-", "*"))]

    testcases = task_data.get("testcases", []) or []
    test_units = 0
    scoring_groups = set()
    for testcase in testcases:
        for unit in testcase.get("testcase", []) or []:
            test_units += 1
            unit_id = str(unit.get("id", ""))
            scoring_groups.add(unit_id.split("-", 1)[0] if "-" in unit_id else unit_id)

    gt_commands = sum(len(v) for v in groundtruth.values())
    startup_commands = sum(len(v) for v in startup.values())
    protocols = detect_protocols(groundtruth)
    autonomous_systems = count_autonomous_systems(groundtruth, startup)
    scenario = task_data.get("scenario", "")

    placement = taxonomy.get(index + 1, {})
    derived_tier = complexity_tier(protocols, len(groundtruth), autonomous_systems, scenario)

    return {
        "task_index": index + 1,
        "scenario": scenario,
        "category": placement.get("category") or detect_category(scenario),
        "subcategory": placement.get("subcategory", ""),
        "complexity": derived_tier if recompute_complexity else placement.get("complexity", derived_tier),
        "protocols": protocols,
        "protocol_layers": len([p for p in protocols if p != "bfd"]),
        "autonomous_systems": autonomous_systems,
        "devices": len(nodes),
        "links": len(links),
        "device_models": device_models,
        "goals": len(goal_lines),
        "devices_configured": len(groundtruth),
        "reference_config_commands": gt_commands,
        "startup_config_commands": startup_commands,
        "scoring_groups": len(scoring_groups),
        "test_units": test_units,
        "parameters": len(task_data.get("resolved_params", {})),
    }


CSV_COLUMNS = [
    "task_index", "scenario", "category", "subcategory", "complexity", "protocols", "protocol_layers",
    "autonomous_systems", "devices", "links", "device_models", "goals",
    "devices_configured", "reference_config_commands", "startup_config_commands",
    "scoring_groups", "test_units", "parameters",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=1,
                        help="Instantiation seed used to materialise each template (default 1)")
    parser.add_argument("--out-json", default=os.path.join(REPO_ROOT, "task", "task_stats.json"))
    parser.add_argument("--out-csv", default=os.path.join(REPO_ROOT, "task", "task_stats.csv"))
    parser.add_argument("--markdown", action="store_true",
                        help="Also print a markdown table aggregated by subcategory")
    parser.add_argument("--recompute-complexity", action="store_true",
                        help="Re-derive the complexity tier from measured features instead "
                             "of reading it from task/task_taxonomy.json")
    args = parser.parse_args()

    with open(ALL_TASKS_PATH) as f:
        total = len(json.load(f))

    taxonomy = load_taxonomy()
    if not taxonomy:
        print(f"[warn] {TAXONOMY_PATH} not found; falling back to name-derived categories",
              file=sys.stderr)

    rows = []
    for index in range(total):
        try:
            rows.append(template_stats(index, args.seed, taxonomy, args.recompute_complexity))
        except Exception as exc:  # a broken template should not hide the rest
            print(f"[warn] task {index + 1}: {type(exc).__name__}: {exc}", file=sys.stderr)

    payload = {
        "seed": args.seed,
        "templates": len(rows),
        "complexity_source": "measured features" if args.recompute_complexity
                             else "task/task_taxonomy.json",
        "note": (
            "Topology size and evaluation size are seed-independent; interface names "
            "and IP addressing vary per instantiation and are not reported here. "
            "Complexity tier follows task/TASK_TAXONOMY.md."
        ),
        "tasks": rows,
    }
    with open(args.out_json, "w") as f:
        json.dump(payload, f, indent=2)

    with open(args.out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            flat["protocols"] = ";".join(row["protocols"])
            flat["device_models"] = ";".join(row["device_models"])
            writer.writerow(flat)

    # Console summary.
    print(f"templates            : {len(rows)}")
    print(f"devices per template : {min(r['devices'] for r in rows)}-{max(r['devices'] for r in rows)}"
          f" (mean {sum(r['devices'] for r in rows) / len(rows):.1f})")
    print(f"scoring groups total : {sum(r['scoring_groups'] for r in rows)}")
    print(f"test units total     : {sum(r['test_units'] for r in rows)}")
    print(f"reference commands   : {sum(r['reference_config_commands'] for r in rows)}")
    for tier in ("L", "M", "H"):
        count = sum(1 for r in rows if r["complexity"] == tier)
        print(f"complexity {tier}         : {count}")
    print(f"\nwrote {args.out_json}\nwrote {args.out_csv}")

    if args.markdown:
        # Same shape as the task taxonomy document: one row per
        # subcategory, in task-index order.
        print("\n| Category | Subcategory | Complexity | Tmpl. | Devices | Groups | Checks |")
        print("|---|---|:---:|---:|:---:|---:|---:|")
        seen = []
        for row in sorted(rows, key=lambda r: r["task_index"]):
            key = (row["category"], row["subcategory"])
            if key not in seen:
                seen.append(key)
        for category, subcategory in seen:
            group = [r for r in rows if (r["category"], r["subcategory"]) == (category, subcategory)]
            lo, hi = min(r["devices"] for r in group), max(r["devices"] for r in group)
            devices = str(lo) if lo == hi else f"{lo}-{hi}"
            tiers = sorted({r["complexity"] for r in group})
            print(f"| {category} | {subcategory} | {'/'.join(tiers)} | {len(group)} | {devices} | "
                  f"{sum(r['scoring_groups'] for r in group)} | {sum(r['test_units'] for r in group)} |")
        print(f"| **Total** | {len(seen)} subcategories | — | **{len(rows)}** | "
              f"{min(r['devices'] for r in rows)}-{max(r['devices'] for r in rows)} | "
              f"**{sum(r['scoring_groups'] for r in rows)}** | "
              f"**{sum(r['test_units'] for r in rows)}** |")


if __name__ == "__main__":
    main()
