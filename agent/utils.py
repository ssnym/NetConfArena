def build_physical_topology(task_data):
    """Return a JSON-serializable physical topology: nodes + links with endpoints.

    Output shape:
        {
            "nodes": [<node_name>, ...],
            "links": [
                {
                    "endpoint1": {"node": <name>, "interface": <name>},
                    "endpoint2": {"node": <name>, "interface": <name>}
                },
                ...
            ]
        }
    """
    topo = task_data.get("topology", {})
    node_name_by_id = {}
    port_name_by_id = {}
    for node in topo.get("nodes", []):
        node_id = node["id"]
        node_name_by_id[node_id] = (node.get("label", {}) or {}).get("text", node_id)
        for port in node.get("ports", []) or []:
            port_name_by_id[port["id"]] = port.get("name", str(port.get("port_number", "")))

    nodes = [node_name_by_id[n["id"]] for n in topo.get("nodes", [])]
    links = [
        {
            "endpoint1": {
                "node": node_name_by_id.get(link["source_node_id"], link["source_node_id"]),
                "interface": port_name_by_id.get(link["source_port_id"], str(link["source_port_id"])),
            },
            "endpoint2": {
                "node": node_name_by_id.get(link["destination_node_id"], link["destination_node_id"]),
                "interface": port_name_by_id.get(link["destination_port_id"], str(link["destination_port_id"])),
            },
        }
        for link in topo.get("links", [])
    ]
    return {"nodes": nodes, "links": links}
