#!/usr/bin/env python3
"""Stage 1 / Step 1: collect GNS3Vault labs and create raw task candidates.

It provides .net parsing, GNS3 project discovery, configuration classification,
page extraction, and missing-input inventories. It writes only to an explicit
working directory and never runs as part of benchmark execution.

Prefer ``--archive`` with a local checkout of the public GNS3Vault archive.
The optional website input is enabled by ``--website``. If the site requires an
authenticated session, pass it in ``GNS3VAULT_COOKIE``. No cookie or downloaded
source material is stored in the release repository.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable

import requests


def parse_topology_net(file_content: str) -> dict[str, Any]:
    """Parse the router/link/note/shape subset of a GNS3 ``.net`` file."""
    result: dict[str, Any] = {
        "ellipses": [], "links": [], "nodes": [], "notes": [], "servers": []
    }
    server_match = re.search(r"\[localhost:(\d+)\]", file_content)
    if server_match:
        result["servers"].append({
            "host": "127.0.0.1", "id": 1, "local": True,
            "port": int(server_match.group(1)),
        })

    routers = re.findall(
        r"\[\[ROUTER\s+([^\]]+)\]\](.*?)(?=\[\[|\[GNS3-DATA\]|$)",
        file_content,
        re.DOTALL,
    )
    node_id = 1
    next_port_id = 1
    connection_specs: list[tuple[str, str, str, str]] = []
    interface_kinds = {
        "f": "FastEthernet", "e": "Ethernet", "g": "GigabitEthernet",
        "s": "Serial",
    }

    for router_name, router_config in routers:
        node: dict[str, Any] = {
            "description": "Router c3600",
            "id": node_id,
            "label": {"text": router_name.strip(), "x": 19.5, "y": -25.0},
            "ports": [],
            "properties": {"name": router_name.strip(), "chassis": "3640"},
            "router_id": node_id,
            "server_id": 1,
            "type": "C3600",
            "x": 0,
            "y": 0,
        }
        for raw_line in router_config.strip().splitlines():
            if "=" not in raw_line:
                continue
            key, value = (part.strip() for part in raw_line.split("=", 1))
            if key == "console":
                node["properties"]["console"] = int(value)
            elif key == "cnfg":
                node["properties"]["startup_config"] = value
            elif key.startswith("slot"):
                node["properties"][key] = value
            elif key in {"x", "y"}:
                node[key] = float(value)
            else:
                endpoint = re.fullmatch(r"([fegs])(\d+)/(\d+)", key, re.I)
                target = value.split()
                target_endpoint = (
                    re.fullmatch(r"([fegs])(\d+)/(\d+)", target[1], re.I)
                    if len(target) >= 2 else None
                )
                if not endpoint or not target_endpoint:
                    continue
                prefix = interface_kinds[endpoint.group(1).lower()]
                target_prefix = interface_kinds[target_endpoint.group(1).lower()]
                interface = f"{prefix}{endpoint.group(2)}/{endpoint.group(3)}"
                target_interface = (
                    f"{target_prefix}{target_endpoint.group(2)}/{target_endpoint.group(3)}"
                )
                node["ports"].append({
                    "description": f"connected to {target[0]} on port {target_interface}",
                    "id": next_port_id,
                    "name": interface,
                    "port_number": int(endpoint.group(3)),
                    "slot_number": int(endpoint.group(2)),
                })
                connection_specs.append(
                    (router_name.strip(), interface, target[0], target_interface)
                )
                next_port_id += 1
        result["nodes"].append(node)
        node_id += 1

    node_by_name = {node["label"]["text"]: node for node in result["nodes"]}
    port_by_endpoint = {
        (node["label"]["text"], port["name"]): port
        for node in result["nodes"] for port in node["ports"]
    }
    linked: set[frozenset[tuple[str, str]]] = set()
    for source_name, source_iface, target_name, target_iface in connection_specs:
        edge = frozenset(((source_name, source_iface), (target_name, target_iface)))
        if edge in linked or target_name not in node_by_name:
            continue
        source_port = port_by_endpoint.get((source_name, source_iface))
        target_port = port_by_endpoint.get((target_name, target_iface))
        if not source_port or not target_port:
            continue
        link_id = len(result["links"]) + 1
        source_port["link_id"] = link_id
        target_port["link_id"] = link_id
        result["links"].append({
            "description": (
                f"Link from {source_name} port {source_iface} "
                f"to {target_name} port {target_iface}"
            ),
            "destination_node_id": node_by_name[target_name]["id"],
            "destination_port_id": target_port["id"],
            "id": link_id,
            "source_node_id": node_by_name[source_name]["id"],
            "source_port_id": source_port["id"],
        })
        linked.add(edge)

    for note_config in re.findall(
        r"\[\[NOTE\s+\d+\]\](.*?)(?=\[\[|\[GNS3-DATA\]|$)",
        file_content,
        re.DOTALL,
    ):
        note: dict[str, Any] = {"color": "#1a1a1a", "text": "", "x": 0, "y": 0}
        for raw_line in note_config.strip().splitlines():
            if "=" not in raw_line:
                continue
            key, value = (part.strip().strip('"') for part in raw_line.split("=", 1))
            if key == "text":
                note["text"] = value
            elif key in {"x", "y"}:
                note[key] = float(value)
            elif key == "color":
                note[key] = value
        if note["text"]:
            result["notes"].append(note)

    for shape_config in re.findall(
        r"\[\[SHAPE\s+\d+\]\](.*?)(?=\[\[|$)", file_content, re.DOTALL
    ):
        ellipse: dict[str, Any] = {
            "border_style": 2, "fill_color": "#ffff7f", "height": 100.0,
            "width": 100.0, "x": 0, "y": 0, "z": -2.0,
        }
        for raw_line in shape_config.strip().splitlines():
            if "=" not in raw_line:
                continue
            key, value = (part.strip().strip('"') for part in raw_line.split("=", 1))
            if key in {"x", "y", "width", "height", "z"}:
                ellipse[key] = float(value)
            elif key == "fill_color":
                ellipse[key] = value
            elif key == "border_style":
                ellipse[key] = int(value)
        result["ellipses"].append(ellipse)
    return result


def find_files(root: Path, suffix: str) -> list[Path]:
    return sorted(path for path in root.rglob(f"*{suffix}") if path.is_file())


def load_topology(lab_dir: Path) -> dict[str, Any] | None:
    gns3_files = find_files(lab_dir, ".gns3")
    if gns3_files:
        project = json.loads(gns3_files[0].read_text(encoding="utf-8", errors="replace"))
        return project.get("topology", project)
    net_files = find_files(lab_dir, ".net")
    if net_files:
        return parse_topology_net(net_files[0].read_text(encoding="utf-8", errors="replace"))
    return None


def classify_configs(lab_dir: Path) -> tuple[list[Path], list[Path]]:
    configs = find_files(lab_dir, ".cfg")
    startup = [path for path in configs if "startup" in str(path).lower()]
    final = [
        path for path in configs
        if any(marker in str(path).lower() for marker in ("final", "groundtruth", "ground_truth"))
    ]
    return startup, final


def write_raw_candidate(
    lab_dir: Path,
    task_number: int,
    output_dir: Path,
    scenario: str | None = None,
    goals: str = "",
    source_url: str = "",
    source_scenario_text: str = "",
) -> tuple[Path, dict[str, bool]]:
    scenario = (scenario or lab_dir.name).strip()
    topology = load_topology(lab_dir)
    startup, ground_truth = classify_configs(lab_dir)
    task_name = f"{task_number:03d}.{scenario}"
    task_data = {
        "task_name": task_name,
        "scenario": scenario,
        "goals": goals,
        "topology": topology,
        "startup_configs": [str(path) for path in startup],
        "ground_truth_configs": [str(path) for path in ground_truth],
        "source": {
            "kind": "GNS3Vault",
            "url": source_url,
            "scenario_text": source_scenario_text,
        },
    }
    output_path = output_dir / "all_tasks" / f"{task_name}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(task_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return output_path, {
        "no_topology": topology is None,
        "no_startup": not startup,
        "no_ground_truth": not ground_truth,
    }


def infer_lab_root(project_file: Path, archive: Path) -> Path:
    """Find the nearest ancestor containing both startup and final configs."""
    fallback = project_file.parent
    for candidate in project_file.parents:
        if candidate == archive.parent:
            break
        configs = find_files(candidate, ".cfg")
        if not configs:
            continue
        names = [str(path).lower() for path in configs]
        has_startup = any("startup" in name for name in names)
        has_final = any(
            marker in name for name in names
            for marker in ("final", "groundtruth", "ground_truth")
        )
        if has_startup and has_final:
            return candidate
        fallback = candidate
        if candidate == archive:
            break
    return fallback


def archive_lab_directories(archive: Path) -> list[Path]:
    project_files = find_files(archive, ".gns3") + find_files(archive, ".net")
    directories: list[Path] = []
    seen: set[Path] = set()
    for project_file in sorted(project_files):
        directory = infer_lab_root(project_file, archive)
        if directory not in seen:
            directories.append(directory)
            seen.add(directory)
    return directories


def extract_page_text(soup: Any) -> tuple[str, str]:
    scenario = ""
    scenario_header = soup.find("h2", string=re.compile("Scenario", re.I))
    if scenario_header:
        scenario = "\n".join(
            sibling.get_text(" ", strip=True)
            for sibling in scenario_header.find_next_siblings()
            if sibling.name != "h2"
        )
    goals = ""
    goals_header = soup.find("h2", string=re.compile("Goal", re.I))
    goals_list = goals_header.find_next("ul") if goals_header else None
    if goals_list:
        goals = "\n".join(f"• {item.get_text(' ', strip=True)}" for item in goals_list.find_all("li"))
    return scenario, goals


def safe_extract_zip(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if destination not in target.parents and target != destination:
                raise ValueError(f"unsafe path in archive: {member.filename}")
        bundle.extractall(destination)


def website_labs(root_url: str, raw_dir: Path, session: requests.Session) -> Iterable[tuple[Path, str]]:
    from bs4 import BeautifulSoup

    response = session.get(root_url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    content = soup.find(class_="entry-content")
    if not content:
        return
    for class_link in content.find_all("a", href=True):
        class_name = class_link.get_text(" ", strip=True)
        if not class_name or class_name.lower() == "helpful books":
            continue
        class_response = session.get(class_link["href"], timeout=30)
        class_response.raise_for_status()
        class_soup = BeautifulSoup(class_response.text, "html.parser")
        lab_list = class_soup.find(id="lcp_instance_0")
        if not lab_list:
            continue
        for lab_link in lab_list.find_all("a", href=True):
            lab_name = lab_link.get_text(" ", strip=True)
            if lab_name:
                yield raw_dir / class_name / lab_name, lab_link["href"]


def download_lab(lab_dir: Path, lab_url: str, session: requests.Session) -> tuple[str, str]:
    from bs4 import BeautifulSoup

    lab_dir.mkdir(parents=True, exist_ok=True)
    response = session.get(lab_url, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    content = soup.find(class_="entry-content")
    if content:
        for link in content.find_all("a", href=True):
            if not link.get_text(" ", strip=True).endswith("Configuration"):
                continue
            download = session.get(link["href"], timeout=60)
            download.raise_for_status()
            archive_path = lab_dir / Path(link["href"]).name
            archive_path.write_bytes(download.content)
            if zipfile.is_zipfile(archive_path):
                safe_extract_zip(archive_path, lab_dir / archive_path.stem)
    return extract_page_text(soup)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path, help="Local GNS3Vault archive checkout")
    source.add_argument("--website", action="store_true", help="Use website input")
    parser.add_argument("--root-url", default="https://gns3vault.com/labs")
    parser.add_argument("--work-dir", type=Path, default=Path("pipeline_work/gns3vault"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = args.work_dir / "raw_candidates"
    records: list[tuple[Path, dict[str, bool]]] = []
    if args.archive:
        for index, lab_dir in enumerate(archive_lab_directories(args.archive), start=1):
            records.append(write_raw_candidate(lab_dir, index, output_dir))
    else:
        session = requests.Session()
        cookie = os.environ.get("GNS3VAULT_COOKIE", "").strip()
        if cookie:
            session.headers["Cookie"] = cookie
        raw_dir = args.work_dir / "downloaded_sources"
        for index, (lab_dir, lab_url) in enumerate(
            website_labs(args.root_url, raw_dir, session), start=1
        ):
            scenario_text, goals = download_lab(lab_dir, lab_url, session)
            scenario = lab_dir.name
            records.append(
                write_raw_candidate(
                    lab_dir, index, output_dir, scenario, goals, lab_url,
                    source_scenario_text=scenario_text,
                )
            )

    inventories: dict[str, list[str]] = {
        "all_tasks": [], "no_topology_tasks": [], "no_startup_tasks": [],
        "no_ground_truth_tasks": [],
    }
    for path, flags in records:
        inventories["all_tasks"].append(str(path))
        for flag, present in flags.items():
            if present:
                inventories[f"{flag}_tasks"].append(str(path))
    for name, values in inventories.items():
        target = output_dir / f"{name}.json"
        target.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    print(f"Created {len(records)} raw candidates under {output_dir}")
    print("These candidates require Step 2 filtering and human provenance review.")


if __name__ == "__main__":
    main()
